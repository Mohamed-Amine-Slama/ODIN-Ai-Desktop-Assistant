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

    def __init__(self, sample_rate: int = SAMPLE_RATE, block_size: int = BLOCK_SIZE):
        self.sample_rate = sample_rate
        self.block_size = block_size
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
                callback=self._on_block,
            )
            self._stream.start()
        except Exception as e:
            raise MicrophoneUnavailable(str(e)) from e

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
            "sounddevice is unavailable — install requirements-voice.txt "
            f"and check your audio device ({e})"
        ) from e
    return sounddevice


def _import_numpy():
    try:
        import numpy
    except ImportError as e:
        raise MicrophoneUnavailable("numpy is required for audio capture") from e
    return numpy


def rms(block, np) -> float:
    """Root-mean-square level of an int16 block, normalised to roughly 0..1."""
    if len(block) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block.astype(np.float32) / 32768.0))))
