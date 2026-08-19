"""Shared microphone capture.

Both the wake-word detector and speech-to-text read from the same 16 kHz mono
stream, so it lives here rather than being opened twice — Windows will happily
let two streams fight over the same device and give you garbage.

sounddevice is used instead of PyAudio: it ships working wheels on Windows,
which is what the old README's "if pip install pyaudio fails..." note was
apologising for.
"""
import queue
import threading

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_SIZE = 1280  # 80ms at 16 kHz — openWakeWord's expected frame size


class MicrophoneUnavailable(RuntimeError):
    """Raised when no usable input device exists (or deps are missing)."""


class Microphone:
    """A single shared input stream. Consumers pull frames off their own queue,
    so the wake detector and the recorder never contend for the device."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, block_size: int = BLOCK_SIZE, device: int | None = None):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.device = device
        self.device_name: str | None = None
        self._sd = _import_sounddevice()
        self._np = _import_numpy()
        self._stream = None
        self._consumers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._stream is not None:
            return
        try:
            self._stream = self._sd.InputStream(
                samplerate=self.sample_rate,
                channels=CHANNELS,
                dtype="int16",
                blocksize=self.block_size,
                device=self.device,
                callback=self._on_block,
            )
            self._stream.start()
        except Exception as e:
            raise MicrophoneUnavailable(str(e)) from e
        # Best-effort only: naming the active device is a diagnostic nicety
        # (surfaced in the "voice mode on" message so a wrong default device
        # is visible without digging through Windows Sound settings first),
        # never worth failing an otherwise-successful stream start over.
        try:
            idx = self.device if self.device is not None else self._sd.default.device[0]
            self.device_name = self._sd.query_devices(idx)["name"]
        except Exception:
            self.device_name = None

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _on_block(self, indata, frames, time_info, status):  # noqa: ARG002
        block = self._np.copy(indata[:, 0])
        with self._lock:
            for q in self._consumers:
                q.put(block)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._consumers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._consumers:
                self._consumers.remove(q)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


def _import_sounddevice():
    try:
        import sounddevice
    except (ImportError, OSError) as e:
        # OSError here means PortAudio is missing, which is common in WSL.
        raise MicrophoneUnavailable(
            "sounddevice is unavailable — install requirements.txt "
            f"and check your audio device ({e})"
        ) from e
    return sounddevice


def _import_numpy():
    try:
        import numpy
    except ImportError as e:
        raise MicrophoneUnavailable("numpy is required for audio capture") from e
    return numpy


def resolve_device(spec: str) -> int | None:
    """MIC_DEVICE setting -> a sounddevice input-device index, or None to use
    the system default. Accepts a numeric index or a case-insensitive
    substring of the device name (e.g. "realtek"). Blank, "auto", and a
    setting that matches nothing all fall back to the default silently
    rather than raising — a stale or misspelled override should degrade to
    "works like before," not stop Jarvis from starting."""
    spec = (spec or "").strip()
    if not spec or spec.lower() == "auto":
        return None
    if spec.isdigit():
        return int(spec)
    try:
        sd = _import_sounddevice()
        spec_lower = spec.lower()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and spec_lower in d["name"].lower():
                return i
    except MicrophoneUnavailable:
        pass
    return None


def list_input_devices() -> list[str]:
    """Every input-capable device sounddevice can see, as '[index] name'
    strings — for a diagnostic message when the active device turns out to
    be producing no signal at all (see SpeechInput._record)."""
    try:
        sd = _import_sounddevice()
        return [f"[{i}] {d['name']}" for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    except MicrophoneUnavailable:
        return []


def no_signal_message(device_name: str | None) -> str:
    name = device_name or "the selected device"
    devices = list_input_devices()
    hint = f" Other input devices found: {'; '.join(devices)}." if devices else ""
    return (
        f"No audio is coming from the microphone ({name}) — check it's the "
        f"right device in Windows Sound settings and isn't muted.{hint} "
        "Set MIC_DEVICE in .env (index or a name substring) to switch which one Jarvis uses."
    )


def rms(block, np) -> float:
    """Root-mean-square level of an int16 block, normalised to roughly 0..1."""
    if len(block) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block.astype(np.float32) / 32768.0))))
