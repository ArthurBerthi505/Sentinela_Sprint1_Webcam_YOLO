"""Fila SQLite persistente e envio HTTP fora da thread de detecção."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


class Outbox:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS rejected (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint TEXT NOT NULL, payload TEXT NOT NULL, error TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def enqueue(self, endpoint, payload):
        with self.connection() as connection:
            connection.execute("INSERT INTO pending(endpoint,payload) VALUES (?,?)",
                               (endpoint, json.dumps(payload, ensure_ascii=False, allow_nan=False)))

    def first(self, endpoint=None):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM pending WHERE endpoint=? ORDER BY id LIMIT 1", (endpoint,)).fetchone() \
                if endpoint else connection.execute("SELECT * FROM pending ORDER BY id LIMIT 1").fetchone()
        return dict(row) if row else None

    def pending(self, endpoint=None):
        with self.connection() as connection:
            return connection.execute("SELECT count(*) FROM pending WHERE endpoint=?", (endpoint,)).fetchone()[0] \
                if endpoint else connection.execute("SELECT count(*) FROM pending").fetchone()[0]

    def acknowledge(self, item_id):
        with self.connection() as connection:
            connection.execute("DELETE FROM pending WHERE id=?", (item_id,))

    def reject(self, row, error):
        with self.connection() as connection:
            connection.execute("INSERT INTO rejected(endpoint,payload,error) VALUES (?,?,?)",
                               (row["endpoint"], row["payload"], error[:1000]))
            connection.execute("DELETE FROM pending WHERE id=?", (row["id"],))


class Publisher:
    def __init__(self, api_url, queue_path, camera_id, sala, config, mode="camera", autostart=True):
        self.endpoint = api_url.rstrip("/")+"/api/events"
        self.outbox = Outbox(queue_path)
        self.camera_id = camera_id
        self.sala = sala
        self.config = config
        self.mode = mode
        self.session_id = str(uuid4())
        self.sequence = 0
        self.last_sample = float("-inf")
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.last_error = None
        self.worker = threading.Thread(target=self._loop, name="sentinela-api", daemon=True)
        if autostart:
            self.worker.start()

    def record(self, state, count, score, fps, confidence, reason, inference_ms=0, image_age_ms=0):
        now = time.monotonic()
        if reason == "amostra" and now-self.last_sample < 5:
            return
        if reason in ("encerramento_voluntario", "interrupcao_usuario", "erro"):
            state, count = "MONITORAMENTO_INDISPONIVEL", None
        if state == "MONITORAMENTO_INDISPONIVEL":
            count, score = None, None
        self.sequence += 1
        payload = {"event_id": str(uuid4()), "session_id": self.session_id, "sequence": self.sequence,
                   "camera_id": self.camera_id, "sala": self.sala,
                   "observed_at": datetime.now(timezone.utc).isoformat(), "state": state,
                   "count": count, "score": score if count else None, "fps": fps,
                   "inference_ms": inference_ms, "image_age_ms": image_age_ms,
                   "confidence": confidence, "model_name": self.config.get("modelo", "yolo26n.pt"),
                   "imgsz": self.config.get("imgsz", 320),
                   "reason": "heartbeat" if reason == "amostra" else reason,
                   "mode": self.mode, "config": self.config}
        self.outbox.enqueue(self.endpoint, payload)
        self.last_sample = now
        self.wake.set()

    def deliver_one(self):
        row = self.outbox.first(self.endpoint)
        if not row:
            return False
        request = Request(row["endpoint"], data=row["payload"].encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=2) as response:
                if not 200 <= response.status < 300:
                    raise OSError(f"HTTP {response.status}")
        except HTTPError as error:
            if 400 <= error.code < 500 and error.code not in (408, 429):
                detail = error.read(1000).decode("utf-8", errors="replace")
                self.outbox.reject(row, f"HTTP {error.code}: {detail}")
                print(f"API rejeitou um evento (HTTP {error.code}); guardado na tabela rejected da fila.")
                return True
            raise
        self.outbox.acknowledge(row["id"])
        return True

    def _loop(self):
        delay = 1
        while not self.stop.is_set():
            try:
                if self.deliver_one():
                    self.last_error = None
                    delay = 1
                    continue
                self.wake.wait(0.5)
                self.wake.clear()
            except Exception as error:
                message = str(error)
                if self.last_error != message:
                    print("API indisponível; eventos permanecem na fila local para reenvio.")
                self.last_error = message
                self.stop.wait(delay)
                delay = min(30, delay*2)

    def close(self):
        deadline = time.monotonic()+1.5
        while self.worker.is_alive() and self.outbox.pending(self.endpoint) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.stop.set()
        self.wake.set()
        if self.worker.is_alive():
            self.worker.join(timeout=2.5)
        pending = self.outbox.pending(self.endpoint)
        if pending:
            print(f"{pending} evento(s) aguardando reenvio em {self.outbox.path.resolve()}")
