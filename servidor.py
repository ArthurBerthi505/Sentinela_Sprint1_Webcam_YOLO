"""API e painel local: python servidor.py (http://127.0.0.1:8000)."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager, contextmanager
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator


ROOT = Path(__file__).resolve().parent
STATES = Literal["VERIFICANDO", "PRESENCA_DETECTADA", "SEM_PESSOAS_DETECTADAS", "MONITORAMENTO_INDISPONIVEL"]
REASONS = Literal["inicio", "mudanca_estado", "heartbeat", "ajuste_confianca", "erro", "quadro_atrasado",
                  "encerramento_voluntario", "interrupcao_usuario"]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,80}$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


def utcnow():
    return datetime.now(timezone.utc)


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    event_id: UUID
    session_id: UUID
    sequence: int = Field(ge=1, strict=True)
    camera_id: Identifier
    sala: Label
    observed_at: AwareDatetime
    state: STATES
    count: int | None = Field(default=None, ge=0, strict=True)
    score: float | None = Field(default=None, ge=0, le=1)
    fps: float | None = Field(default=None, ge=0)
    inference_ms: float | None = Field(default=None, ge=0)
    image_age_ms: float | None = Field(default=None, ge=0)
    confidence: float = Field(gt=0, lt=1)
    model_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    imgsz: int = Field(ge=32, le=4096, multiple_of=32)
    reason: REASONS
    mode: Literal["camera", "simulacao"] = "camera"
    config: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unknown(self):
        if self.state == "MONITORAMENTO_INDISPONIVEL" and self.count is not None:
            raise ValueError("Contagem indisponível deve ser null, não zero.")
        if len(json.dumps(self.config, ensure_ascii=False, allow_nan=False)) > 12000:
            raise ValueError("Configuração excede o limite de 12 KB.")
        return self


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path, clock=utcnow):
        self.path = Path(path)
        self.clock = clock

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    camera_id TEXT NOT NULL,
                    config_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    sequence INTEGER NOT NULL,
                    camera_id TEXT NOT NULL,
                    observed_ts REAL NOT NULL,
                    received_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(session_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS events_camera_time ON events(camera_id, observed_ts DESC, id DESC);
                CREATE INDEX IF NOT EXISTS events_time ON events(observed_ts DESC, id DESC);
                PRAGMA user_version=1;
            """)

    def insert(self, event: EventIn):
        now = self.clock()
        if event.observed_at > now + timedelta(seconds=60):
            raise Conflict("Relógio da origem está mais de 60 segundos à frente da API.")
        payload = event.model_dump(mode="json")
        payload["observed_at"] = event.observed_at.astimezone(timezone.utc).isoformat()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT fingerprint FROM events WHERE event_id=?",
                                          (str(event.event_id),)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise Conflict("O mesmo event_id já foi usado com outro conteúdo.")
                return False
            session = connection.execute("SELECT camera_id FROM sessions WHERE session_id=?",
                                         (str(event.session_id),)).fetchone()
            if session and session["camera_id"] != event.camera_id:
                raise Conflict("Uma sessão não pode pertencer a duas câmeras.")
            connection.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, ?)",
                               (str(event.session_id), event.camera_id, json.dumps(event.config, ensure_ascii=False)))
            try:
                connection.execute("""INSERT INTO events
                    (event_id, session_id, sequence, camera_id, observed_ts, received_at, state, reason,
                     fingerprint, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(event.event_id), str(event.session_id), event.sequence, event.camera_id,
                     event.observed_at.timestamp(), now.isoformat(), event.state, event.reason,
                     fingerprint, serialized))
            except sqlite3.IntegrityError as error:
                raise Conflict("A sequência desta sessão já existe com outro identificador.") from error
        return True

    @staticmethod
    def decode(row):
        payload = json.loads(row["payload_json"])
        payload["received_at"] = row["received_at"]
        return payload

    def overview(self, offline_after=15):
        now = self.clock()
        with self.connection() as connection:
            rows = connection.execute("""SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY camera_id ORDER BY observed_ts DESC, id DESC) AS rank
                FROM events) WHERE rank=1 ORDER BY camera_id""").fetchall()
            total = connection.execute("SELECT count(*) FROM events WHERE observed_ts>=? AND observed_ts<=?",
                                       ((now-timedelta(hours=24)).timestamp(), now.timestamp())).fetchone()[0]
        cameras = []
        for row in rows:
            data = self.decode(row)
            received = datetime.fromisoformat(row["received_at"])
            age = max(0, now.timestamp()-row["observed_ts"])
            stale = age > offline_after or (now-received).total_seconds() > offline_after
            data["effective_state"] = "SEM_COMUNICACAO" if stale else data["state"]
            data["seconds_since_observation"] = round(age, 1)
            data.pop("config", None)
            if stale or data["state"] == "MONITORAMENTO_INDISPONIVEL":
                data["count"] = None
            cameras.append(data)
        return {"server_time": now.isoformat(), "offline_after_seconds": offline_after,
                "summary": {"cameras": len(cameras), "presence": sum(c["effective_state"] == "PRESENCA_DETECTADA" for c in cameras),
                            "unavailable": sum(c["effective_state"] in ("SEM_COMUNICACAO", "MONITORAMENTO_INDISPONIVEL") for c in cameras),
                            "events_24h": total}, "cameras": cameras}

    def events(self, camera_id=None, state=None, hours=24, only_changes=True, limit=25, offset=0):
        conditions = ["observed_ts<=?"]
        now = self.clock()
        parameters = [now.timestamp()+60]
        if hours:
            conditions.append("observed_ts>=?")
            parameters.append((now-timedelta(hours=hours)).timestamp())
        if camera_id:
            conditions.append("camera_id=?")
            parameters.append(camera_id)
        if state:
            conditions.append("state=?")
            parameters.append(state)
        if only_changes:
            conditions.append("reason!='heartbeat'")
        where = " AND ".join(conditions)
        with self.connection() as connection:
            count = connection.execute("SELECT count(*) FROM events WHERE "+where, parameters).fetchone()[0]
            rows = connection.execute("SELECT * FROM events WHERE "+where+" ORDER BY observed_ts DESC, id DESC LIMIT ? OFFSET ?",
                                      [*parameters, limit, offset]).fetchall()
        items = [self.decode(row) for row in rows]
        for item in items:
            item.pop("config", None)
        return {"items": items, "total": count, "limit": limit, "offset": offset}

    def event(self, event_id):
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM events WHERE event_id=?", (str(event_id),)).fetchone()
        return self.decode(row) if row else None


def create_app(database_path=None, clock=utcnow):
    store = Store(database_path or ROOT/"dados"/"sentinela.db", clock)

    @asynccontextmanager
    async def lifespan(application):
        store.initialize()
        yield

    application = FastAPI(title="Sentinela API", version="1.2.0", lifespan=lifespan)
    application.state.store = store

    @application.get("/api/health")
    def health():
        with store.connection() as connection:
            connection.execute("SELECT 1")
        return {"status": "ok", "server_time": clock().isoformat(), "version": "1.2.0"}

    @application.post("/api/events")
    def receive(event: EventIn, response: Response):
        try:
            inserted = store.insert(event)
        except Conflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        response.status_code = 201 if inserted else 200
        return {"event_id": str(event.event_id), "inserted": inserted}

    @application.get("/api/overview")
    def overview():
        return store.overview()

    @application.get("/api/events")
    def events(camera_id: str | None = None, state: STATES | None = None,
               hours: Annotated[int, Query(ge=0, le=720)] = 24, only_changes: bool = True,
               limit: Annotated[int, Query(ge=1, le=100)] = 25,
               offset: Annotated[int, Query(ge=0)] = 0):
        return store.events(camera_id, state, hours, only_changes, limit, offset)

    @application.get("/api/events.csv")
    def export(camera_id: str | None = None, state: STATES | None = None,
               hours: Annotated[int, Query(ge=0, le=720)] = 24, only_changes: bool = True):
        rows = store.events(camera_id, state, hours, only_changes, 10000)["items"]
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=";")
        fields = ["observed_at", "received_at", "camera_id", "sala", "mode", "state", "count", "score",
                  "inference_ms", "image_age_ms", "confidence", "model_name", "reason", "event_id", "session_id", "sequence"]
        writer.writerow(fields)
        for row in rows:
            values = [row.get(field, "") for field in fields]
            values = [("'"+value if value.lstrip().startswith(("=", "+", "-", "@")) else value)
                      if isinstance(value, str) else value for value in values]
            writer.writerow(values)
        return Response(content="\ufeff"+output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="sentinela_eventos.csv"'})

    @application.get("/api/events/{event_id}")
    def detail(event_id: UUID):
        event = store.event(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Evento não encontrado.")
        return event

    @application.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(ROOT/"painel"/"index.html")

    application.mount("/assets", StaticFiles(directory=ROOT/"painel"), name="assets")
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=str(ROOT/"dados"/"sentinela.db"))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Porta inválida.")
    print(f"Painel: http://127.0.0.1:{args.port}")
    print(f"Banco SQLite: {Path(args.db).resolve()}")
    uvicorn.run(create_app(args.db), host="127.0.0.1", port=args.port)
