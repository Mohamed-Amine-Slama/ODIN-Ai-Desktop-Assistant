"""Microphone listening and speech-to-text.

Uses faster-whisper locally instead of SpeechRecognition's Google backend: it
works offline, is markedly more accurate, and doesn't ship your audio to a
third party. Recording stops on silence rather than after a fixed timeout.
"""
import config
from core.audio import Microphone, MicrophoneUnavailable, rms


class SpeechInput:
    def __init__(self, mic: Microphone | None = None):
        self.mic = mic or Microphone()
        self._owns_mic = mic is None
        self._np = self.mic._np
        self._model = _load_model()
        # Calibrated on first listen() from the ambient floor.
        self._noise_floor: float | None = None

        if self._owns_mic:
            self.mic.start()

    def listen(self, max_seconds: float | None = None) -> str:
        """Record until the user stops talking, then transcribe.

        Returns '' if nothing intelligible was captured.
        """
        audio = self._record(max_seconds or config.VAD_MAX_SECONDS)
        if audio is None or len(audio) == 0:
            return ""

        samples = audio.astype(self._np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            samples,
            language="en",
            beam_size=1,          # greedy: much faster, fine for short commands
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if text:
            print(f"You said: {text}")
        return text

    def close(self) -> None:
        if self._owns_mic:
            self.mic.stop()

    # -- internals ---------------------------------------------------------

    def _record(self, max_seconds: float):
        np = self._np
        q = self.mic.subscribe()
        block_seconds = self.mic.block_size / self.mic.sample_rate
        silence_blocks_needed = max(1, int(config.VAD_SILENCE_SECONDS / block_seconds))
        max_blocks = int(max_seconds / block_seconds)

        print("Listening...")
        collected = []
        silent_run = 0
        heard_speech = False

        for _ in range(max_blocks):
            try:
                block = q.get(timeout=1.0)
            except Exception:
                break

            level = rms(block, np)
            threshold = self._threshold(level, heard_speech)

            if level >= threshold:
                heard_speech = True
                silent_run = 0
                collected.append(block)
            elif heard_speech:
                # Keep trailing silence — clipping the last word hurts accuracy.
                silent_run += 1
                collected.append(block)
                if silent_run >= silence_blocks_needed:
                    break
            # Leading silence before any speech is simply dropped.

        self.mic.unsubscribe(q)

        if not heard_speech:
            return None
        return np.concatenate(collected)

    def _threshold(self, level: float, heard_speech: bool) -> float:
        """Adaptive gate. Tracks the ambient floor while nothing is being said,
        so a noisy room doesn't make Jarvis deaf (or a silent one make it
        trigger-happy)."""
        if self._noise_floor is None:
            self._noise_floor = level
        if not heard_speech:
            # Slow decay toward the current ambient level.
            self._noise_floor = 0.9 * self._noise_floor + 0.1 * level
        return max(self._noise_floor * 3.0, 0.012)


def _load_model():
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise MicrophoneUnavailable(
            "faster-whisper isn't installed. Run: pip install -r requirements-voice.txt"
        ) from e

    try:
        # First call downloads the weights (~75MB for base.en) and caches them.
        return WhisperModel(config.STT_MODEL, device="auto", compute_type=config.STT_COMPUTE)
    except Exception as e:
        raise MicrophoneUnavailable(
            f"couldn't load the '{config.STT_MODEL}' speech model ({e})"
        ) from e
