"""Testes sem câmera ou bibliotecas de IA: python -m unittest -v."""

import contextlib
import csv
import io
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from webcam_yolo import (INDISPONIVEL, PRESENTE, SEM_DETECCAO, VERIFICANDO,
                         LatestFrameCapture, PresenceFilter, inside_region,
                         parse_args, person_detections, run)


class PresenceTests(unittest.TestCase):
    def test_presence_needs_continuous_confirmation(self):
        detector = PresenceFilter(confirm_seconds=1)
        self.assertEqual(detector.update(1, 0), VERIFICANDO)
        self.assertEqual(detector.update(2, 0.5), VERIFICANDO)
        self.assertEqual(detector.update(1, 1), PRESENTE)

    def test_isolated_person_detection_does_not_confirm_presence(self):
        detector = PresenceFilter()
        detector.update(1, 0)
        self.assertEqual(detector.update(0, 0.5), VERIFICANDO)
        self.assertEqual(detector.update(1, 1), VERIFICANDO)

    def test_initial_no_detection_needs_full_interval(self):
        detector = PresenceFilter(absence_seconds=10)
        for timestamp in range(10):
            self.assertEqual(detector.update(0, timestamp), VERIFICANDO)
        self.assertEqual(detector.update(0, 10), SEM_DETECCAO)

    def test_short_detection_loss_keeps_confirmed_presence(self):
        detector = PresenceFilter(absence_seconds=3)
        detector.update(1, 0)
        detector.update(1, 1)
        self.assertEqual(detector.update(0, 2), PRESENTE)
        self.assertEqual(detector.update(0, 3), PRESENTE)
        self.assertEqual(detector.update(0, 5), SEM_DETECCAO)

    def test_person_reappearance_restarts_absence_timer(self):
        detector = PresenceFilter(absence_seconds=3)
        detector.update(1, 0)
        detector.update(1, 1)
        detector.update(0, 2)
        detector.update(1, 4)
        self.assertEqual(detector.update(0, 5), PRESENTE)
        self.assertEqual(detector.update(0, 7), PRESENTE)
        self.assertEqual(detector.update(0, 8), SEM_DETECCAO)

    def test_capture_failure_never_means_no_people(self):
        detector = PresenceFilter(absence_seconds=2)
        detector.update(0, 0)
        detector.update(0, 1)
        detector.update(0, 2)
        self.assertEqual(detector.state, SEM_DETECCAO)
        self.assertEqual(detector.unavailable(), INDISPONIVEL)
        self.assertEqual(detector.update(0, 100), VERIFICANDO)
        self.assertEqual(detector.update(0, 101), VERIFICANDO)
        self.assertEqual(detector.update(0, 102), SEM_DETECCAO)

    def test_long_gap_does_not_count_as_observed_absence(self):
        detector = PresenceFilter(absence_seconds=3, max_gap_seconds=2)
        detector.update(0, 0)
        self.assertEqual(detector.update(0, 10), VERIFICANDO)
        self.assertEqual(detector.update(0, 11), VERIFICANDO)
        self.assertEqual(detector.update(0, 12), VERIFICANDO)
        self.assertEqual(detector.update(0, 13), SEM_DETECCAO)

    def test_confidence_change_resets_previous_evidence(self):
        detector = PresenceFilter()
        detector.update(1, 0)
        detector.update(1, 1)
        detector.reset()
        self.assertEqual(detector.update(1, 2), VERIFICANDO)


class InputTests(unittest.TestCase):
    def test_roi_filters_corridor_detection_by_center(self):
        roi = (0.25, 0.25, 0.75, 0.75)
        self.assertTrue(inside_region([40, 40, 60, 60], roi, 100, 100))
        self.assertFalse(inside_region([0, 40, 20, 60], roi, 100, 100))

    def test_frame_size_does_not_change_normalized_roi(self):
        roi = (0.25, 0.25, 0.75, 0.75)
        self.assertTrue(inside_region([400, 400, 600, 600], roi, 1000, 1000))

    def test_invalid_parameters_are_rejected_before_camera_access(self):
        cases = (("--confianca", "nan"), ("--confianca", "1"),
                 ("--ausencia", "0"), ("--camera", "-1"), ("--max-atraso", "-1"),
                 ("--imgsz", "321"), ("--roi", "0.9", "0", "0.1", "1"))
        for case in cases:
            with self.subTest(case=case), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parse_args(case)
                self.assertEqual(error.exception.code, 2)


    def test_other_classes_and_low_scores_are_not_counted_as_people(self):
        boxes = [[10, 10, 30, 70], [40, 20, 60, 80], [60, 20, 90, 90]]
        detections = person_detections(boxes, [0.8, 0.99, 0.40], [0, 56, 0],
                                       [0], 0.65, None, 100, 100)
        self.assertEqual(detections, [(boxes[0], 0.8)])


class QueueCamera:
    def __init__(self):
        self.frames = queue.Queue()
        self.released = threading.Event()
        self.release_calls = 0

    def read(self):
        return self.frames.get()

    def release(self):
        self.release_calls += 1
        self.released.set()


class CaptureTests(unittest.TestCase):
    def make_reader(self):
        camera = QueueCamera()
        reader = LatestFrameCapture(camera).start()
        def cleanup():
            camera.frames.put((False, None))
            reader.close()
            self.assertTrue(camera.released.wait(1))
            self.assertEqual(camera.release_calls, 1)
        self.addCleanup(cleanup)
        return reader, camera

    def test_slow_consumer_receives_latest_frame_instead_of_old_queue(self):
        reader, camera = self.make_reader()
        frames = [SimpleNamespace(size=1, name=f"quadro{index}") for index in range(3)]
        for frame in frames:
            camera.frames.put((True, frame))
        with reader.condition:
            self.assertTrue(reader.condition.wait_for(lambda: reader.sequence == 3, timeout=1))
        sequence, captured_at, frame = reader.read_after(0)
        self.assertEqual(sequence, 3)
        self.assertIs(frame, frames[-1])
        self.assertLessEqual(captured_at, time.monotonic())

    def test_same_frame_cannot_be_used_twice_to_confirm_presence(self):
        reader, camera = self.make_reader()
        camera.frames.put((True, SimpleNamespace(size=1)))
        sequence, _, _ = reader.read_after(0)
        with self.assertRaisesRegex(RuntimeError, "nova imagem"):
            reader.read_after(sequence, timeout=0.01)

    def test_capture_error_invalidates_previously_cached_image(self):
        reader, camera = self.make_reader()
        camera.frames.put((True, SimpleNamespace(size=1)))
        reader.read_after(0)
        camera.frames.put((False, None))
        with reader.condition:
            self.assertTrue(reader.condition.wait_for(lambda: reader.error is not None, timeout=1))
        with self.assertRaisesRegex(RuntimeError, "Falha na captura"):
            reader.read_after(0)


def fake_runtime(capture_rows, key=-1, inference_error=None, stale=False):
    cv2 = MagicMock()
    cv2.error = RuntimeError
    frame = MagicMock()
    frame.shape = (480, 640, 3)
    frame.size = 480 * 640 * 3
    capture = cv2.VideoCapture.return_value
    capture.isOpened.return_value = True
    capture.read.side_effect = [(ok, frame if ok else None) for ok in capture_rows]
    cv2.waitKey.return_value = key
    cv2.getWindowProperty.return_value = 1
    xyxy = MagicMock()
    xyxy.cpu.return_value.tolist.return_value = [[100, 100, 200, 300]]
    scores = MagicMock()
    scores.cpu.return_value.tolist.return_value = [0.8]
    class_ids = MagicMock()
    class_ids.cpu.return_value.tolist.return_value = [0]
    prediction = SimpleNamespace(boxes=SimpleNamespace(xyxy=xyxy, conf=scores, cls=class_ids))
    model = SimpleNamespace(task="detect", names={0: "person", 1: "bicycle"},
                            predict=MagicMock(return_value=[prediction]))
    if inference_error:
        model.predict.side_effect = RuntimeError(inference_error)
    ultralytics = SimpleNamespace(YOLO=MagicMock(return_value=model))
    events = iter(enumerate(capture_rows, start=1))
    def read_after(_previous):
        sequence, ok = next(events)
        if not ok:
            raise RuntimeError("captura falhou")
        return sequence, time.monotonic() - (10 if stale else 0), frame
    reader = MagicMock()
    reader.start.return_value = reader
    reader.read_after.side_effect = read_after
    reader.close.side_effect = capture.release
    return cv2, ultralytics, capture, model, reader


class SessionTests(unittest.TestCase):
    def run_fake(self, directory, capture_rows, key=-1, inference_error=None, stale=False, diagnostic=False):
        cv2, ultralytics, capture, model, reader = fake_runtime(capture_rows, key, inference_error, stale)
        args = parse_args(["--saida", directory, "--sem-api"] + (["--diagnostico"] if diagnostic else []))
        with patch.dict("sys.modules", {"cv2": cv2, "ultralytics": ultralytics}), \
                patch("webcam_yolo.LatestFrameCapture", return_value=reader), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run(args)
        path = next(Path(directory).glob("*.csv"))
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        capture.release.assert_called_once()
        cv2.destroyAllWindows.assert_called_once()
        reader.close.assert_called_once()
        self.assertEqual(len(list(Path(directory).glob("*.json"))), 1)
        return result, rows, model

    def test_valid_capture_filters_people_and_records_user_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            result, rows, model = self.run_fake(directory, [True], key=ord("q"))
        self.assertEqual(result, 0)
        self.assertEqual(rows[0]["deteccoes_no_quadro"], "1")
        self.assertEqual(rows[-1]["motivo"], "encerramento_voluntario")
        self.assertEqual(model.predict.call_args.kwargs["classes"], [0])
        self.assertFalse(model.predict.call_args.kwargs["save"])

    def test_disconnected_camera_logs_unknown_count(self):
        with tempfile.TemporaryDirectory() as directory:
            result, rows, _ = self.run_fake(directory, [True, False])
        self.assertEqual(result, 1)
        self.assertEqual(rows[-1]["estado"], INDISPONIVEL)
        self.assertEqual(rows[-1]["deteccoes_no_quadro"], "")
        self.assertEqual(rows[-1]["motivo"], "erro")

    def test_failed_inference_cannot_report_an_empty_room(self):
        with tempfile.TemporaryDirectory() as directory:
            result, rows, _ = self.run_fake(directory, [True], inference_error="modelo falhou")
        self.assertEqual(result, 1)
        self.assertEqual(rows[-1]["estado"], INDISPONIVEL)
        self.assertEqual(rows[-1]["deteccoes_no_quadro"], "")

    def test_stale_image_does_not_confirm_presence_or_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            result, rows, _ = self.run_fake(directory, [True], key=ord("q"), stale=True)
        self.assertEqual(result, 0)
        self.assertEqual(rows[0]["estado"], INDISPONIVEL)
        self.assertEqual(rows[0]["deteccoes_no_quadro"], "")
        self.assertEqual(rows[0]["motivo"], "quadro_atrasado")
        self.assertGreater(float(rows[0]["idade_imagem_ms"]), 2000)

    def test_diagnostic_mode_uses_all_classes_but_keeps_person_count(self):
        with tempfile.TemporaryDirectory() as directory:
            result, rows, model = self.run_fake(directory, [True], key=ord("q"), diagnostic=True)
        self.assertEqual(result, 0)
        self.assertEqual(rows[0]["deteccoes_no_quadro"], "1")
        self.assertIsNone(model.predict.call_args.kwargs["classes"])
        self.assertEqual(model.predict.call_args.kwargs["conf"], 0.15)


if __name__ == "__main__":
    unittest.main()
