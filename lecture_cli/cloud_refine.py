"""Plan N4: after-class refinement through the cloud transcription service instead of local Qwen.

Segments come from refinement.segments (the 20–30 s rule); each upload, retry, response check and
filter is api_capture's own code. The success rules stay those of refinement.refine: any failed
segment, an empty result for a voiced segment, the controller's timeout or a skip falls back to
the live transcript.
"""
from __future__ import annotations

import asyncio
import os
import re

import httpx
import numpy as np

from .api_capture import API_TIMEOUT, consume_response, transcribe, verify_service
from .refinement import BYTES_PER_SECOND

DEFAULT_MODEL = "whisper-large-v3"
# A Chinese prompt written with punctuation leads Whisper to punctuate its Chinese output.
PUNCTUATION_PROMPT = "以下是普通话课堂录音的转写，使用简体中文，并加上逗号、句号和问号等标点符号。"
# The cloud prompt budget of live cloud transcription (cli.session loads 600 characters for it).
CONTEXT_LIMIT = 600
# api_capture.transcribe retries 429/5xx/network errors forever, which suits a live class. Here a
# segment that still fails after these many waits (5 + 10 + 20 s) fails the refinement instead.
RETRIES = 3
RETRY_FAILED = "转录服务多次重试仍不可用"
# Same threshold as api_capture and refinement: these segments are never uploaded.
NEAR_SILENT = 32
CJK = r"[\u3000-\u303f\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]"
FULL_WIDTH = str.maketrans(",.?!:;", "，。？！：；")
# Plan GUI-3 item 7: a half-width mark with Chinese on at least one side; never a run of dots.
HALF_WIDTH = re.compile(rf"(?:(?<={CJK})|(?=[,.?!:;]{CJK}))(?:[,?!:;]|(?<!\.)\.(?!\.)) *")
# GUI-4 fix: Whisper sometimes writes the small ideographic comma (U+FE51); it is Chinese wherever
# it appears, so it becomes a full-width comma without looking at its neighbours.
SMALL_COMMA = re.compile("\ufe51 *")


def full_width(text: str) -> str:
    """Chinese punctuation where Whisper wrote ASCII marks next to Chinese; English and numbers
    (3.14, e.g., 10:30) are left alone. A space after a converted mark goes with it. "﹑" (U+FE51)
    always becomes "，"."""
    text = SMALL_COMMA.sub("，", text)
    return HALF_WIDTH.sub(lambda m: m[0].strip().translate(FULL_WIDTH), text)


def context(meta: dict) -> str:
    """Prompt lines ahead of the previous segment's text: the punctuation sentence for Chinese, then
    the course terms cut at a term boundary to the cloud budget."""
    terms = meta.get("asr_context") or ""
    if len(terms) > CONTEXT_LIMIT:
        terms = terms[:CONTEXT_LIMIT]
        terms = terms[:terms.rfind(", ")] if ", " in terms else ""
    lines = [PUNCTUATION_PROMPT] if meta.get("language") == "zh" else []
    return "\n".join(filter(None, lines + [terms]))


def request_meta(meta: dict) -> dict:
    """The session settings as api_capture.transcribe reads them, with the refinement model."""
    return dict(meta, asr_api_model=meta.get("refine_api_model") or DEFAULT_MODEL, asr_context=context(meta))


class Texts:
    """Stands in for capture.Transcript in consume_response: collects the kept text in order."""
    def __init__(self):
        self.parts = []

    def append(self, text, start, end):
        self.parts.append(text)

    def text(self) -> str:
        joined = " ".join(part.strip() for part in self.parts if part.strip())
        # Whisper segments are joined with spaces; Chinese needs none between its characters.
        return re.sub(f"(?<={CJK}) (?={CJK})", "", joined)


class CloudRefiner:
    """One HTTP client for the whole refinement; transcribe(pcm) returns a segment's text."""
    def __init__(self, meta: dict, transport=None, sleep=asyncio.sleep):
        self.meta = request_meta(meta)
        self.sleep = sleep
        self.previous = ""
        self.loop = asyncio.new_event_loop()
        # The key reaches only this process (cli.session's spawn); it is never written anywhere.
        self.client = httpx.AsyncClient(timeout=API_TIMEOUT, transport=transport,
                                        headers={"Authorization": "Bearer " + os.environ["LECTURE_ASR_API_KEY"]})

    def __enter__(self):
        try:
            self.loop.run_until_complete(verify_service(self.client, self.meta))
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.client.aclose())
            self.loop.close()

    def transcribe(self, pcm: bytes) -> str:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        if np.max(np.abs(samples), initial=0) <= NEAR_SILENT:
            return ""  # refinement.refine records it as a near-silent segment.
        text = self.loop.run_until_complete(self._text(pcm))
        if text:
            self.previous = text
        return text

    async def _text(self, pcm: bytes) -> str:
        waits = 0

        async def sleep(delay):
            nonlocal waits
            waits += 1
            if waits > RETRIES:
                raise RuntimeError(RETRY_FAILED)
            await self.sleep(delay)

        result = await transcribe(self.client, self.meta, pcm, self.previous, {}, lambda: None, sleep=sleep)
        texts = Texts()
        consume_response(texts, result, 0, len(pcm) / BYTES_PER_SECOND)
        return full_width(texts.text()) if self.meta.get("language") == "zh" else texts.text()
