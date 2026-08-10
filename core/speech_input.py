"""Microphone listening and speech-to-text."""
import speech_recognition as sr


class SpeechInput:
    def __init__(self):
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = 0.8
        self.mic = sr.Microphone()
        with self.mic as source:
            print("Calibrating microphone for ambient noise...")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)

    def listen(self) -> str:
        """Blocks until the user speaks, then returns the recognized text
        (empty string if nothing understandable was heard)."""
        with self.mic as source:
            print("Listening...")
            try:
                audio = self.recognizer.listen(source, timeout=8, phrase_time_limit=15)
            except sr.WaitTimeoutError:
                return ""
        try:
            text = self.recognizer.recognize_google(audio)
            print(f"You said: {text}")
            return text
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            print(f"Speech recognition service error: {e}")
            return ""
