"""A scripted stand-in for AsrClient with the same `transcribe` signature.

Tests queue what the recogniser "hears" — text or a failure — and read
`calls` to see what audio it was given.
"""

from collections import deque

from app.speech.asr import AsrError, Transcription


def hears(text: str) -> Transcription:
    return Transcription(text=text, audio_seconds=3.0, cost_usd=0.0001, latency_ms=40)


class FakeAsr:
    model = "fake-asr"

    def __init__(self, *script: Transcription | AsrError) -> None:
        self.script: deque[Transcription | AsrError] = deque(script)
        self.calls: list[tuple[bytes, str]] = []

    def queue(self, *script: Transcription | AsrError) -> None:
        self.script.extend(script)

    async def transcribe(self, audio: bytes, mime_type: str) -> Transcription:
        self.calls.append((audio, mime_type))
        if not self.script:
            return hears("hello")
        step = self.script.popleft()
        if isinstance(step, AsrError):
            raise step
        return step

    async def aclose(self) -> None:
        pass
