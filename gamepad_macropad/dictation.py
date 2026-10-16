"""Hold-to-talk dictation using Vosk (offline) and the system default microphone.

The English model is downloaded once into the app data folder. Audio is read
on a background thread; recognized phrases are queued for the engine to type.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

from .config import app_data_dir

log = logging.getLogger(__name__)

_MODEL_NAME = "vosk-model-small-en-us-0.15"
_MODEL_URL = f"https://alphacephei.com/vosk/models/{_MODEL_NAME}.zip"
_TARGET_RATE = 16000


class MicDictation:
    def __init__(self) -> None:
        self._text: queue.Queue[str] = queue.Queue()
        self._notes: queue.Queue[str] = queue.Queue()
        self._listen = threading.Event()
        self._quit = threading.Event()
        self._model = None
        self._model_lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, name="mic", daemon=True)
        self._thread.start()

    @property
    def listening(self) -> bool:
        return self._listen.is_set()

    def preload(self) -> None:
        """Start fetching/loading the model so the first hold is faster."""
        threading.Thread(target=self._ensure_model, name="mic-model", daemon=True).start()

    def set_listening(self, active: bool) -> None:
        if active:
            self._listen.set()
        else:
            self._listen.clear()

    def shutdown(self) -> None:
        self._quit.set()
        self._listen.clear()
        self._listen.set()
        self._thread.join(timeout=2.0)
        self._listen.clear()

    def drain_text(self) -> list[str]:
        return _drain(self._text)

    def drain_notes(self) -> list[str]:
        return _drain(self._notes)

    # -- background -----------------------------------------------------------------

    def _loop(self) -> None:
        while not self._quit.is_set():
            if not self._listen.wait(0.5):
                continue
            if self._quit.is_set():
                return
            try:
                self._session()
            except Exception as exc:
                log.exception("dictation failed")
                self._notes.put(f"Mic failed: {exc}")
                self._listen.clear()

    def _session(self) -> None:
        import audioop

        import pyaudio
        import vosk

        model = self._ensure_model()
        if model is None or not self._listen.is_set() or self._quit.is_set():
            return
        vosk.SetLogLevel(-1)
        rec = vosk.KaldiRecognizer(model, _TARGET_RATE)
        pa = pyaudio.PyAudio()
        stream = None
        try:
            stream, rate = _open_default_mic(pa)
            self._notes.put("Listening")
            rate_state = None
            while self._listen.is_set() and not self._quit.is_set():
                raw = stream.read(max(800, rate // 8), exception_on_overflow=False)
                if rate != _TARGET_RATE:
                    raw, rate_state = audioop.ratecv(raw, 2, 1, rate, _TARGET_RATE, rate_state)
                if rec.AcceptWaveform(raw):
                    _enqueue_phrase(self._text, rec.Result())
            _enqueue_phrase(self._text, rec.FinalResult())
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    log.debug("mic stream close failed", exc_info=True)
            pa.terminate()

    def _ensure_model(self):
        with self._model_lock:
            if self._model is not None:
                return self._model
            path = _model_dir()
            if not (path / "conf").is_dir():
                self._notes.put("Downloading speech model…")
                try:
                    _download_model(path)
                except Exception as exc:
                    log.exception("speech model download failed")
                    self._notes.put(f"Mic model download failed: {exc}")
                    return None
            import vosk

            vosk.SetLogLevel(-1)
            self._model = vosk.Model(str(path))
            return self._model


def _drain(q: queue.Queue[str]) -> list[str]:
    out: list[str] = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def _enqueue_phrase(q: queue.Queue[str], raw: str) -> None:
    try:
        text = str(json.loads(raw).get("text", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        return
    if text:
        q.put(text)


def _model_dir() -> Path:
    return app_data_dir() / "models" / _MODEL_NAME


def _download_model(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    zip_path = dest.parent / f"{_MODEL_NAME}.zip"
    urlretrieve(_MODEL_URL, zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest.parent)
    try:
        zip_path.unlink()
    except OSError:
        pass
    if not (dest / "conf").is_dir():
        raise FileNotFoundError(f"speech model missing at {dest}")


def _open_default_mic(pa):
    """Open the default input. Prefer 16 kHz; otherwise the device's native rate."""
    import pyaudio

    info = pa.get_default_input_device_info()
    device = int(info["index"])
    native = int(float(info.get("defaultSampleRate") or _TARGET_RATE))
    for rate in (_TARGET_RATE, native):
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=rate,
                input=True,
                input_device_index=device,
                frames_per_buffer=max(800, rate // 8),
            )
            return stream, rate
        except OSError:
            if rate == native:
                raise
    raise OSError("could not open the default microphone")
