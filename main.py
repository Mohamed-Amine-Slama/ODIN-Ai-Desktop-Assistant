"""
Jarvis - a Windows desktop AI assistant.

Run:  python main.py

Modes:
  - Text mode: just type and press Enter.
  - Voice mode: press Enter, then speak after 'Listening...' appears.

Commands (work in either mode):
  /mode voice   - switch to voice mode
  /mode text    - switch to text mode
  /reset        - clear conversation memory
  /help         - show this list
  /quit         - exit
"""
import sys

import anthropic

import config
from core.brain import Brain, friendly_error
from core.scheduler import ReminderScheduler
from core.speech_output import SpeechOutput
from core.store import get_store

COMMANDS = """Commands:
  /mode voice   switch to voice mode
  /mode text    switch to text mode
  /reset        clear conversation memory (including saved history)
  /forget       same as /reset
  /help         show this list
  /quit         exit"""


YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "do it", "go ahead",
             "confirm", "affirmative", "please do"}
NO_WORDS = {"no", "nope", "nah", "cancel", "stop", "don't", "dont", "negative", "abort"}


class Session:
    """Owns the run loop's mutable state: mode, microphone, wake word.

    The voice path is a state machine:
        IDLE --wake word--> LISTENING --silence--> THINKING --> SPEAKING --> IDLE
    With WAKE_WORD=off (or if the model won't load) it degrades to
    press-Enter-to-talk rather than refusing to run.
    """

    def __init__(self, speaker: SpeechOutput):
        self.speaker = speaker
        self.mode = "text"
        self.mic = None
        self.listener = None
        self.wake = None

    # -- mode switching ----------------------------------------------------

    def set_mode(self, mode: str) -> str:
        if mode == "text":
            self.mode = "text"
            return "Switched to text mode."

        # Only claim to be in voice mode if a microphone actually came up.
        # The old code printed "Switched to voice mode" and then silently kept
        # reading typed input when the listener was None.
        if self.listener is None:
            try:
                from core.audio import Microphone
                from core.speech_input import SpeechInput
                from core.wake import make_detector

                print("Starting microphone and loading speech models...")
                self.mic = Microphone()
                self.mic.start()
                self.listener = SpeechInput(mic=self.mic)
                self.wake = make_detector(self.mic)
            except Exception as e:
                self._teardown_audio()
                return f"I couldn't start voice mode ({e}). Staying in text mode."

        self.mode = "voice"
        if self.wake is not None:
            return f"Voice mode on. Say '{config.WAKE_WORD.replace('_', ' ')}' to wake me."
        return "Voice mode on (push-to-talk — press Enter, then speak)."

    def _teardown_audio(self) -> None:
        if self.mic is not None:
            try:
                self.mic.stop()
            except Exception:
                pass
        self.mic = None
        self.listener = None
        self.wake = None

    def shutdown(self) -> None:
        self._teardown_audio()
        self.speaker.shutdown()

    # -- input -------------------------------------------------------------

    def read_input(self) -> str | None:
        """Return the next utterance, or None if nothing usable was captured."""
        if self.mode != "voice" or self.listener is None:
            try:
                return input("You: ").strip()
            except EOFError:
                return "/quit"

        if self.wake is not None:
            print(f"[idle] waiting for '{config.WAKE_WORD.replace('_', ' ')}'...  (Ctrl-C to quit)")
            if not self.wake.wait():
                return None
            # Short cue so the user knows it heard them before they start.
            print("[wake]")
        else:
            try:
                input("Press Enter, then speak (or type a command): ")
            except EOFError:
                return "/quit"

        text = self.listener.listen()
        if not text:
            print("Didn't catch that.")
            return None
        return text

    # -- confirmation ------------------------------------------------------

    def confirm(self, skill, tool_input) -> bool:
        """Ask before running a destructive skill. Defaults to NO — a misheard
        'shut down' should never take the machine down on silence or on an
        answer we couldn't parse."""
        question = skill.confirmation_prompt(**tool_input)
        self.speaker.say(question)
        self.speaker.wait(timeout=20)

        if self.mode == "voice" and self.listener is not None:
            answer = self.listener.listen(max_seconds=6).strip().lower().rstrip(".!")
            if not answer:
                self.speaker.say("I didn't catch that, so I'll leave it.")
                return False
            if any(word in answer for word in NO_WORDS):
                return False
            if any(word in answer for word in YES_WORDS):
                return True
            self.speaker.say("I'll take that as a no.")
            return False

        try:
            return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes")
        except EOFError:
            return False


def handle_command(cmd: str, brain: Brain, session: Session) -> None:
    cmd = cmd.strip().lower()
    if cmd in ("/quit", "/exit"):
        print("Goodbye.")
        session.shutdown()
        sys.exit(0)
    if cmd in ("/reset", "/forget"):
        brain.reset()
        print("Conversation memory cleared (notes and reminders kept).")
        return
    if cmd == "/mode voice":
        print(session.set_mode("voice"))
        return
    if cmd == "/mode text":
        print(session.set_mode("text"))
        return
    if cmd == "/help":
        print(COMMANDS)
        return
    print(f"Unknown command '{cmd}'.\n{COMMANDS}")


def main() -> None:
    config.ensure_dirs()

    problem = config.missing_key_message()
    if problem:
        print(problem)
        return

    speaker = SpeechOutput()
    session = Session(speaker)
    store = get_store()

    # Speech is driven by the brain's streaming callback, so sentences are
    # spoken as they generate rather than after the whole reply lands.
    brain = Brain(confirm=session.confirm, on_text=speaker.say, store=store)
    restored = brain.load_history()

    # Fire anything that came due while Jarvis was closed, then keep polling.
    scheduler = ReminderScheduler(store)
    missed = scheduler.fire_due()
    scheduler.start()

    if config.DEFAULT_MODE == "voice":
        print(session.set_mode("voice"))

    print(f"=== {config.ASSISTANT_NAME} is online ({session.mode} mode) ===")
    print(f"Model: {config.MODEL} (effort={config.EFFORT}, voice={speaker.engine_name})")
    if restored:
        print(f"Restored {restored} messages from your last session.")
    if missed:
        print(f"Fired {missed} reminder(s) that came due while I was closed.")
    speaker.say(f"{config.ASSISTANT_NAME} online. How can I help?")

    while True:
        try:
            text = session.read_input()
        except KeyboardInterrupt:
            print("\nGoodbye.")
            scheduler.stop()
            session.shutdown()
            return

        if not text:
            continue

        if text.startswith("/"):
            handle_command(text, brain, session)
            continue

        try:
            reply = brain.ask(text)
        except KeyboardInterrupt:
            print("\n[interrupted]")
            continue
        except anthropic.APIError as e:
            speaker.say(friendly_error(e))
            continue
        except Exception as e:  # noqa: BLE001 - never kill the loop
            speaker.say(friendly_error(e))
            continue

        # The streaming callback already spoke the reply. Only speak here if
        # nothing was streamed (e.g. a tool-only turn with no text).
        if not brain.spoke_during_last_turn:
            speaker.say(reply)
        speaker.wait(timeout=60)


if __name__ == "__main__":
    main()
