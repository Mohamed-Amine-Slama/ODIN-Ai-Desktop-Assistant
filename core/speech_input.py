"""Microphone listening and speech-to-text.

Uses faster-whisper locally instead of SpeechRecognition's Google backend: it
works offline, is markedly more accurate, and doesn't ship your audio to a
third party. Recording stops on silence rather than after a fixed timeout.
"""
import config
from core.audio import Microphone, MicrophoneUnavailable, rms

# Blocks at the start of every _record() call spent bootstrapping the noise
# floor rather than being judged as possible speech — see _record's comment.
# The first couple of blocks off a *freshly subscribed* queue reliably read
# near-zero regardless of the room (confirmed live: 0.0000/0.0004 for two
# blocks before jumping to the room's real ~0.01-0.03 level) — some initial
# buffering/latency in the callback delivering into a brand new queue, not
# real silence — so those are skipped rather than calibrated from. The
# blocks after that are sampled and the *median* (not a blend) is used, so
# one atypical block can't skew the seed the way a single early reading did.
_FLOOR_CALIBRATION_SKIP_BLOCKS = 2   # ~160ms: discarded, not representative
_FLOOR_CALIBRATION_SAMPLE_BLOCKS = 6  # ~480ms: sampled for the median seed


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
        calibration_samples: list[float] = []
        calibration_end = _FLOOR_CALIBRATION_SKIP_BLOCKS + _FLOOR_CALIBRATION_SAMPLE_BLOCKS

        try:
            for i in range(max_blocks):
                try:
                    block = q.get(timeout=1.0)
                except Exception:
                    break

                level = rms(block, np)

                if i < calibration_end:
                    # Bootstrap the floor from a few fresh blocks instead of
                    # letting it drift up slowly (see _threshold's docstring)
                    # from wherever it was left. A room's ordinary ambient
                    # level can already sit above the 0.012 floor below, and
                    # an unconverged estimate lets that plain noise read as
                    # "speech" almost immediately — confirmed live, a quiet
                    # room's own background noise crossed the threshold
                    # within half a second on a fresh floor. These blocks
                    # are still kept below (not discarded) in case the user
                    # started talking with no leading silence at all.
                    collected.append(block)
                    if i >= _FLOOR_CALIBRATION_SKIP_BLOCKS:
                        calibration_samples.append(level)
                        if i == calibration_end - 1:
                            calibration_samples.sort()
                            mid = len(calibration_samples) // 2
                            self._noise_floor = calibration_samples[mid]
                    continue

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
        finally:
            # A bare statement here (as this used to be) would skip the
            # unsubscribe on any exception other than the one already
            # caught around q.get() above, permanently leaking this queue
            # as a registered consumer in Microphone._consumers — every
            # future audio block would then be appended to a queue nothing
            # is ever going to drain again.
            self.mic.unsubscribe(q)

        if not heard_speech:
            return None
        return np.concatenate(collected)

    def _threshold(self, level: float, heard_speech: bool) -> float:
        """Adaptive gate. Tracks the ambient floor while nothing is being said,
        so a noisy room doesn't make Jarvis deaf (or a silent one make it
        trigger-happy)."""
        if self._noise_floor is None:
            # Seeded at 0.0 (assume silence going in), not this block's own
            # level. Seeding from the block's own level is a deadlock if
            # speech starts on block one with no leading silence: threshold
            # becomes level * 3, "level >= level * 3" is false for that same
            # level, and steady-volume continuous speech barely nudges the
            # floor away from that self-referential trap on the blocks right
            # after — so heard_speech can stay false for the whole recording
            # instead of just missing one block. In practice _record()'s own
            # calibration burst seeds this before speech-detection ever
            # starts, so this branch is now just a safety net.
            self._noise_floor = 0.0
        updated = 0.9 * self._noise_floor + 0.1 * level
        if not heard_speech:
            # Slow decay toward the current ambient level.
            self._noise_floor = updated
        else:
            # While heard_speech is True the floor otherwise stays frozen at
            # whatever it was the instant speech started — if that value
            # ends up below the room's real ambient level (a bad
            # calibration, or the room got quieter), ordinary background
            # noise then permanently reads as "still speaking" for the rest
            # of the recording, since it never again reads as "returned to
            # silence." Letting the floor keep decaying *toward* quieter
            # blocks (never up, so loud speech itself still can't corrupt
            # it) gives a bad seed a chance to self-correct instead of
            # staying wrong for the rest of the call.
            self._noise_floor = min(self._noise_floor, updated)
        return max(self._noise_floor * 3.0, 0.012)


def _load_model():
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise MicrophoneUnavailable(
            "faster-whisper isn't installed. Run: pip install -r requirements.txt"
        ) from e

    try:
        # First call downloads the weights (~75MB for base.en) and caches them.
        return WhisperModel(config.STT_MODEL, device="auto", compute_type=config.STT_COMPUTE)
    except Exception as e:
        raise MicrophoneUnavailable(
            f"couldn't load the '{config.STT_MODEL}' speech model ({e})"
        ) from e
