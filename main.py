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
from core.speech_output import SpeechOutput

COMMANDS = """Commands:
  /mode voice   switch to voice mode
  /mode text    switch to text mode
  /reset        clear conversation memory
  /help         show this list
  /quit         exit"""


class Session:
    """Owns the run loop's mutable state: current mode and the (lazily
    created) microphone."""

    def __init__(self, speaker: SpeechOutput):
        self.speaker = speaker
        self.mode = "text"
        self.listener = None

    def set_mode(self, mode: str) -> str:
        if mode == "text":
            self.mode = "text"
            return "Switched to text mode."

        # Only claim to be in voice mode if a microphone actually came up.
        # The old code printed "Switched to voice mode" and then silently kept
        # reading typed input when the listener was None.
        if self.listener is None:
            try:
                from core.speech_input import SpeechInput

                self.listener = SpeechInput()
            except Exception as e:
                return f"I couldn't start the microphone ({e}). Staying in text mode."

        self.mode = "voice"
        return "Switched to voice mode."

    def read_input(self) -> str | None:
        """Return the next utterance, or None if nothing usable was captured."""
        if self.mode == "voice" and self.listener is not None:
            try:
                input("Press Enter, then speak (or type a command): ")
            except EOFError:
                return "/quit"
            text = self.listener.listen()
            if not text:
                print("Didn't catch that.")
                return None
            return text

        try:
            return input("You: ").strip()
        except EOFError:
            return "/quit"

    def confirm(self, skill, tool_input) -> bool:
        """Ask before running a destructive skill. Defaults to NO — a misheard
        'shut down' should never take the machine down on silence."""
        question = skill.confirmation_prompt(**tool_input)
        self.speaker.say(question)
        self.speaker.wait(timeout=15)
        try:
            answer = input(f"{question} [y/N]: ").strip().lower()
        except EOFError:
            return False
        return answer in ("y", "yes")


def handle_command(cmd: str, brain: Brain, session: Session) -> None:
    cmd = cmd.strip().lower()
    if cmd in ("/quit", "/exit"):
        print("Goodbye.")
        session.speaker.shutdown()
        sys.exit(0)
    if cmd == "/reset":
        brain.reset()
        print("Conversation memory cleared.")
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

    # Speech is driven by the brain's streaming callback, so sentences are
    # spoken as they generate rather than after the whole reply lands.
    brain = Brain(confirm=session.confirm, on_text=speaker.say)

    if config.DEFAULT_MODE == "voice":
        print(session.set_mode("voice"))

    print(f"=== {config.ASSISTANT_NAME} is online ({session.mode} mode) ===")
    print(f"Model: {config.CLAUDE_MODEL} (effort={config.EFFORT})")
    speaker.say(f"{config.ASSISTANT_NAME} online. How can I help?")

    while True:
        try:
            text = session.read_input()
        except KeyboardInterrupt:
            print("\nGoodbye.")
            speaker.shutdown()
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
