"""A stand-in for TtsClient with the same `speak` signature: records the text, returns fake mp3."""

from app.speech.tts import Speech, TtsError

FAKE_MP3 = b"ID3\x04\x00fake-mp3"


class FakeTts:
    model = "fake-tts"
    voice = "fake-voice"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    async def speak(self, text: str) -> Speech:
        self.calls.append(text)
        if self.fail:
            raise TtsError("synth down")
        return Speech(audio=FAKE_MP3, latency_ms=30)

    async def aclose(self) -> None:
        pass
