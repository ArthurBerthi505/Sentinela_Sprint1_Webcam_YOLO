"""Sprint 1: detecção de presença com webcam, YOLO e confirmação temporal.

Execute: python webcam_yolo.py
Ajuda:   python webcam_yolo.py --help
As bibliotecas de visão são importadas somente ao iniciar a captura.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import importlib.metadata
import json
import math
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import urlsplit


VERIFICANDO = "VERIFICANDO"
PRESENTE = "PRESENCA_DETECTADA"
SEM_DETECCAO = "SEM_PESSOAS_DETECTADAS"
INDISPONIVEL = "MONITORAMENTO_INDISPONIVEL"
JANELA = "Sentinela - Sprint 1"


@dataclass
class PresenceFilter:
    """Confirma presença/ausência de detecção usando tempo monotônico.

    A contagem é instantânea. O estado exige observações consecutivas da
    mesma categoria; uma falha ou intervalo longo reinicia a confirmação.
    Ausência de detecção não garante que a sala esteja vazia.
    """

    confirm_seconds: float = 0.5
    absence_seconds: float = 2.0
    max_gap_seconds: float = 5.0
    state: str = VERIFICANDO
    candidate: str | None = None
    candidate_since: float | None = None
    last_sample: float | None = None

    def reset(self, state: str = VERIFICANDO) -> None:
        self.state = state
        self.candidate = None
        self.candidate_since = None
        self.last_sample = None

    def unavailable(self) -> str:
        self.reset(INDISPONIVEL)
        return self.state

    def update(self, count: int, now: float) -> str:
        if self.last_sample is not None:
            if now < self.last_sample:
                raise ValueError("O relógio deve ser monotônico.")
            if now - self.last_sample > self.max_gap_seconds:
                self.reset()
        if self.state == INDISPONIVEL:
            self.reset()
        self.last_sample = now
        candidate = PRESENTE if count > 0 else SEM_DETECCAO
        if candidate != self.candidate:
            self.candidate = candidate
            self.candidate_since = now
        delay = self.confirm_seconds if candidate == PRESENTE else self.absence_seconds
        if now - self.candidate_since >= delay:
            self.state = candidate
        return self.state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=0, help="Índice da webcam: 0, 1, 2...")
    parser.add_argument("--modelo", default="yolo26n.pt", help="Modelo ou caminho para pesos .pt")
    parser.add_argument("--confianca", type=float, default=0.65, help="Score mínimo, entre 0 e 1")
    parser.add_argument("--imgsz", type=int, default=320, help="Tamanho da entrada do modelo: 320 ou 640")
    parser.add_argument("--confirmacao", type=float, default=0.5, help="Segundos para confirmar presença")
    parser.add_argument("--ausencia", type=float, default=2.0, help="Segundos sem detecção para mudar o estado")
    parser.add_argument("--max-intervalo", type=float, default=5.0, help="Intervalo máximo entre análises, em segundos")
    parser.add_argument("--max-atraso", type=float, default=2.0, help="Idade máxima de um resultado de imagem, em segundos")
    parser.add_argument("--device", default="cpu", help="cpu ou dispositivo suportado pelo YOLO, como 0")
    parser.add_argument("--backend", choices=("auto", "dshow", "msmf"), default="auto")
    parser.add_argument("--roi", type=float, nargs=4, metavar=("X1", "Y1", "X2", "Y2"),
                        help="Retângulo monitorado em coordenadas de 0 a 1")
    parser.add_argument("--saida", default="logs", help="Pasta para CSV e parâmetros da sessão")
    parser.add_argument("--diagnostico", action="store_true", help="Mostrar também classes e scores de objetos não contados")
    parser.add_argument("--camera-id", default="webcam-01", help="Identificador único para a API e o painel")
    parser.add_argument("--sala", default="Sala de testes", help="Nome da sala exibido no painel")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000", help="Endereço base da API")
    parser.add_argument("--sem-api", action="store_true", help="Usar somente a janela e os CSVs locais")
    parser.add_argument("--fila", default=None, help="Arquivo SQLite da fila; padrão: dados/fila_<camera-id>.sqlite3")
    return parser


def parse_args(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.camera < 0:
        parser.error("--camera deve ser maior ou igual a zero.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", args.camera_id):
        parser.error("--camera-id aceita letras, números, ponto, hífen e sublinhado (até 80 caracteres).")
    args.sala = args.sala.strip()
    if not 1 <= len(args.sala) <= 120:
        parser.error("--sala deve ter entre 1 e 120 caracteres.")
    address = urlsplit(args.api_url)
    if address.scheme not in ("http", "https") or not address.hostname or address.username or address.password \
            or address.query or address.fragment or address.path not in ("", "/"):
        parser.error("--api-url deve ser um endereço base HTTP(S), como http://127.0.0.1:8000.")
    if not math.isfinite(args.confianca) or not 0 < args.confianca < 1:
        parser.error("--confianca deve estar entre 0 e 1, sem incluir os extremos.")
    if args.imgsz < 32 or args.imgsz % 32:
        parser.error("--imgsz deve ser um múltiplo positivo de 32, como 320 ou 640.")
    for name in ("confirmacao", "ausencia", "max_intervalo", "max_atraso"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} deve ser positivo e finito.")
    if args.roi:
        x1, y1, x2, y2 = args.roi
        if not all(math.isfinite(v) for v in args.roi) or not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            parser.error("--roi exige 0 <= X1 < X2 <= 1 e 0 <= Y1 < Y2 <= 1.")
    if sys.platform != "win32" and args.backend != "auto":
        parser.error("dshow e msmf são backends do Windows. Use --backend auto.")
    return args


def inside_region(box, roi, width: int, height: int) -> bool:
    """Conta uma detecção se o centro da caixa estiver no retângulo."""
    x1, y1, x2, y2 = roi or (0.0, 0.0, 1.0, 1.0)
    center_x = (box[0] + box[2]) / 2 / width
    center_y = (box[1] + box[3]) / 2 / height
    return x1 <= center_x <= x2 and y1 <= center_y <= y2


def person_detections(boxes, scores, class_ids, people, confidence, roi, width, height):
    """Aplica explicitamente classe, score e região aos resultados retornados."""
    if not (len(boxes) == len(scores) == len(class_ids)):
        raise RuntimeError("O modelo retornou caixas, scores e classes inconsistentes.")
    return [(box, score) for box, score, class_id in zip(boxes, scores, class_ids)
            if int(class_id) in people and math.isfinite(score) and score >= confidence
            and inside_region(box, roi, width, height)]


class LatestFrameCapture:
    """Captura em segundo plano, mantendo somente o quadro mais recente.

    O worker é o único dono de read/release. Cada quadro é entregue no máximo
    uma vez ao consumidor, e uma falha invalida até o quadro guardado.
    """

    def __init__(self, capture):
        self.capture = capture
        self.condition = threading.Condition()
        self.stop_requested = threading.Event()
        self.latest = None
        self.sequence = 0
        self.error = None
        self.worker = threading.Thread(target=self._loop, name="webcam-capture", daemon=True)

    def start(self):
        self.worker.start()
        return self

    def _loop(self):
        try:
            while not self.stop_requested.is_set():
                ok, frame = self.capture.read()
                if self.stop_requested.is_set():
                    break
                if not ok or frame is None or frame.size == 0:
                    raise RuntimeError("Não foi possível obter um quadro da webcam.")
                captured_at = time.monotonic()
                with self.condition:
                    self.sequence += 1
                    self.latest = (self.sequence, captured_at, frame)
                    self.condition.notify_all()
        except Exception as error:
            with self.condition:
                self.error = error
                self.condition.notify_all()
        finally:
            self.capture.release()

    def raise_if_failed(self):
        with self.condition:
            if self.error is not None:
                raise RuntimeError(f"Falha na captura: {self.error}") from self.error

    def read_after(self, previous_sequence, timeout=5.0):
        with self.condition:
            ready = self.condition.wait_for(
                lambda: self.error is not None or self.stop_requested.is_set()
                or (self.latest is not None and self.latest[0] != previous_sequence), timeout)
            self.raise_if_failed()
            if not ready or self.stop_requested.is_set():
                raise RuntimeError("A webcam não forneceu uma nova imagem dentro do prazo.")
            return self.latest

    def close(self):
        self.stop_requested.set()
        with self.condition:
            self.condition.notify_all()
        self.worker.join(timeout=1.0)
        # Não chamamos release em paralelo a read se o driver estiver bloqueado.
        # O worker é daemon: ao encerrar o processo, o sistema libera o dispositivo.


class SessionLog:
    def __init__(self, args):
        output = Path(args.saida)
        output.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("sessao_%Y%m%d_%H%M%S_%f")
        self.csv_path = output / f"{name}.csv"
        self.config_path = output / f"{name}.json"
        versions = {}
        for package in ("ultralytics", "opencv-python", "torch"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = "nao_instalado"
        self.config_path.write_text(json.dumps({
            "versao_prototipo": "1.2",
            "inicio": datetime.now().astimezone().isoformat(),
            "parametros": vars(args), "python": sys.version, "versoes": versions,
            "descricao": "Confirmação temporal de detecções; não certifica sala vazia.",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        self.handle = self.csv_path.open("w", encoding="utf-8-sig", newline="")
        self.writer = csv.writer(self.handle, delimiter=";")
        self.writer.writerow(("data_hora", "tempo_s", "estado", "deteccoes_no_quadro",
                              "maior_score", "fps_inferencia", "confianca_minima", "motivo",
                              "inferencia_ms", "idade_imagem_ms"))
        self.started = time.monotonic()
        self.publisher = None
        if not args.sem_api:
            from envio_api import Publisher
            queue_path = args.fila or str(Path(__file__).resolve().parent/"dados"/f"fila_{args.camera_id}.sqlite3")
            self.publisher = Publisher(args.api_url, queue_path, args.camera_id, args.sala, vars(args).copy())
            print(f"API: {args.api_url} | Câmera: {args.camera_id} | Sala: {args.sala}")

    def record(self, state, count, score, fps, confidence, reason, inference_ms=0, image_age_ms=0):
        self.writer.writerow((datetime.now().astimezone().isoformat(timespec="milliseconds"),
                              round(time.monotonic() - self.started, 3), state,
                              "" if count is None else count, round(score, 3),
                              round(fps, 2), round(confidence, 2), reason,
                              round(inference_ms, 1), round(image_age_ms, 1)))
        self.handle.flush()
        if self.publisher is not None:
            self.publisher.record(state, count, score, fps, confidence, reason, inference_ms, image_age_ms)

    def close(self):
        self.handle.close()
        if self.publisher is not None:
            self.publisher.close()


def run(args) -> int:
    try:
        import cv2
        from ultralytics import YOLO
    except ImportError:
        print("Instale as dependências: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1

    capture = None
    reader = None
    logger = SessionLog(args)
    temporal = PresenceFilter(args.confirmacao, args.ausencia, args.max_intervalo)
    confidence = args.confianca
    try:
        print(f"Carregando {args.modelo}; o primeiro uso pode baixar os pesos pela internet.")
        model = YOLO(args.modelo)
        if model.task != "detect":
            raise RuntimeError("Use um modelo YOLO de detecção, como yolo26n.pt.")
        names = dict(model.names.items() if isinstance(model.names, dict) else enumerate(model.names))
        people = [int(index) for index, name in names.items() if str(name).lower() == "person"]
        if not people:
            raise RuntimeError("O modelo não possui a classe 'person'. Use pesos pré-treinados no COCO.")

        backend = {"auto": cv2.CAP_ANY, "dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF}[args.backend]
        capture = cv2.VideoCapture(args.camera, backend)
        if not capture.isOpened():
            raise RuntimeError("Webcam não abriu. Feche outros apps, confira a permissão e tente --camera 1.")
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Alguns drivers ignoram esta opção.
        reader = LatestFrameCapture(capture).start()
        cv2.namedWindow(JANELA, cv2.WINDOW_NORMAL)
        print("Q ou Esc: sair | + e -: ajustar confiança em 0,05")
        print(f"CSV: {logger.csv_path.resolve()}")
        last_state = None
        last_log = 0.0
        last_count = 0
        last_score = 0.0
        last_fps = 0.0
        sequence = 0

        while True:
            sequence, captured_at, frame = reader.read_after(sequence)
            inference_start = time.monotonic()
            result = model.predict(frame, classes=None if args.diagnostico else people,
                                   conf=min(confidence, 0.15) if args.diagnostico else confidence, imgsz=args.imgsz,
                                   device=args.device, verbose=False, save=False)[0]
            inference_seconds = max(time.monotonic() - inference_start, 1e-6)
            reader.raise_if_failed()
            if result.boxes is None:
                raise RuntimeError("O modelo não retornou resultados de detecção.")
            boxes = result.boxes.xyxy.cpu().tolist()
            scores = result.boxes.conf.cpu().tolist()
            class_ids = result.boxes.cls.cpu().tolist()
            height, width = frame.shape[:2]
            detections = person_detections(boxes, scores, class_ids, people, confidence,
                                           args.roi, width, height)
            now = time.monotonic()
            image_age = now - captured_at
            fresh = image_age <= args.max_atraso
            count = len(detections) if fresh else None
            score = max((score for _, score in detections), default=0.0)
            state = temporal.update(count, now) if fresh else temporal.unavailable()
            fps = 1.0 / inference_seconds
            inference_ms = inference_seconds * 1000
            image_age_ms = image_age * 1000
            last_count, last_score, last_fps = count, score, fps
            changed = state != last_state
            if changed or now - last_log >= 1:
                reason = "quadro_atrasado" if not fresh else ("mudanca_estado" if changed else "amostra")
                logger.record(state, count, score, fps, confidence, reason, inference_ms, image_age_ms)
                last_log = now
            if changed:
                print(f"{datetime.now():%H:%M:%S} | {state} | detecções atuais: {count}")
                last_state = state

            image = frame.copy()
            if args.roi:
                x1, y1, x2, y2 = args.roi
                cv2.rectangle(image, (int(x1 * width), int(y1 * height)),
                              (int(x2 * width), int(y2 * height)), (255, 180, 0), 2)
            if args.diagnostico and fresh:
                for box, detected_score, class_id in zip(boxes, scores, class_ids):
                    x1, y1, x2, y2 = map(int, box)
                    label = names.get(int(class_id), str(int(class_id)))
                    cv2.rectangle(image, (x1, y1), (x2, y2), (150, 150, 150), 1)
                    cv2.putText(image, f"{label}: {detected_score:.2f}", (x1, max(18, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            for box, detected_score in detections if fresh else []:
                x1, y1, x2, y2 = map(int, box)
                cv2.rectangle(image, (x1, y1), (x2, y2), (0, 220, 0), 2)
                cv2.putText(image, f"pessoa: {detected_score:.2f}", (x1, max(18, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 0), 2)
            cv2.rectangle(image, (0, 0), (width, min(145, height)), (30, 30, 30), -1)
            color = (0, 220, 0) if state == PRESENTE else (0, 220, 255)
            pending = ""
            if fresh and temporal.candidate != temporal.state:
                delay = args.confirmacao if temporal.candidate == PRESENTE else args.ausencia
                remaining = max(0.0, delay - (now - temporal.candidate_since))
                pending = f"Mudanca pendente: {remaining:.1f}s"
            for line, text in enumerate((state, f"Neste quadro: {count if count is not None else '?'} pessoa(s) | {pending}",
                                        f"FPS IA: {fps:.1f} | Inferencia: {inference_ms:.0f}ms",
                                        f"Idade da imagem: {image_age_ms:.0f}ms",
                                        f"Conf: {confidence:.2f} | + / - ajustar | Q sair")):
                cv2.putText(image, text, (10, 25 + 28 * line), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1)
            cv2.imshow(JANELA, image)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27) or cv2.getWindowProperty(JANELA, cv2.WND_PROP_VISIBLE) < 1:
                logger.record(state, last_count, last_score, last_fps, confidence, "encerramento_voluntario",
                              inference_ms, image_age_ms)
                break
            if key in (ord("+"), ord("="), ord("-")):
                step = -0.05 if key == ord("-") else 0.05
                confidence = round(min(0.95, max(0.05, confidence + step)), 2)
                temporal.reset()
                logger.record(VERIFICANDO, count, score, fps, confidence, "ajuste_confianca",
                              inference_ms, image_age_ms)
                last_state = None
                print(f"Confiança mínima: {confidence:.2f}; confirmação reiniciada.")
        return 0
    except KeyboardInterrupt:
        temporal.unavailable()
        logger.record(INDISPONIVEL, None, 0, 0, confidence, "interrupcao_usuario")
        print("Teste interrompido.")
        return 0
    except Exception as error:
        temporal.unavailable()
        logger.record(INDISPONIVEL, None, 0, 0, confidence, "erro")
        print(f"Erro: {error}", file=sys.stderr)
        return 1
    finally:
        if reader is not None:
            reader.close()
        elif capture is not None:
            capture.release()
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass
        logger.close()
        print(f"Registros: {logger.csv_path.resolve()}")


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
