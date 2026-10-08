"""Sequential cloud file transcription with a bounded local PCM queue."""
from __future__ import annotations

import asyncio
import io
import math
import os
import signal
import time
import wave
from pathlib import Path

import httpx
import numpy as np

from .audio_buffer import AudioBuffer, drain_timeout
from .capture import Transcript
from .refinement import BYTES_PER_SECOND, segment_cut
from .storage import has_content, read_json, write_json
from .mic_gain import mic_gain
from .input_level import NOTICE as WEAK_NOTICE, WeakInput

API_TIMEOUT = httpx.Timeout(60, connect=10)
RETRY_WARNING = "转录服务暂不可用，正在重试；音频已暂存"


def wav_audio(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(pcm)
    return buffer.getvalue()


async def verify_service(client, meta):
    try:
        response = await client.get(meta["asr_api_base"].rstrip("/") + "/models")
    except httpx.RequestError as exc:
        raise RuntimeError("无法连接转录服务，请检查转录 key 与服务地址") from exc
    if response.status_code != 200:
        raise RuntimeError(f"转录服务验证失败（HTTP {response.status_code}），请检查转录 key 与服务地址")


async def transcribe(client, meta, pcm, previous, state, publish, sleep=asyncio.sleep):
    data = {"model": meta["asr_api_model"], "response_format": "verbose_json", "temperature": "0"}
    if meta["language"] != "auto":
        data["language"] = meta["language"]
    prompt = "\n".join(filter(None, [meta.get("asr_context", ""), previous[-200:]]))
    if prompt:
        data["prompt"] = prompt
    wav = wav_audio(pcm)
    delay = 5
    while True:
        try:
            response = await client.post(meta["asr_api_base"].rstrip("/") + "/audio/transcriptions",
                                         data=data, files={"file": ("audio.wav", wav, "audio/wav")})
        except httpx.RequestError:
            response = None
        if response is not None and response.status_code != 429 and response.status_code < 500:
            if response.status_code != 200:
                raise RuntimeError(f"转录请求失败（HTTP {response.status_code}），请检查转录 key 与请求配置")
            try:
                result = response.json()
            except ValueError as exc:
                raise RuntimeError("转录服务响应格式错误") from exc
            state.pop("warning", None)
            publish()
            return result
        state["warning"] = RETRY_WARNING
        publish()
        await sleep(delay)
        delay = min(60, delay * 2)


def response_segments(result, duration):
    # Validate the entire external response before publishing any of its records.
    if not isinstance(result, dict):
        raise RuntimeError("转录服务响应格式错误")
    if "segments" not in result:
        if not isinstance(result.get("text"), str):
            raise RuntimeError("转录服务响应格式错误")
        return [dict(start=0, end=duration, text=result["text"])]
    segments = result["segments"]
    if not isinstance(segments, list):
        raise RuntimeError("转录服务响应格式错误")
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            raise RuntimeError("转录服务响应格式错误")
        for key in ("start", "end", "no_speech_prob", "avg_logprob", "compression_ratio"):
            if key not in segment and key not in ("start", "end"):
                continue
            value = segment.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise RuntimeError("转录服务响应格式错误")
        if segment["start"] < 0 or segment["end"] < segment["start"]:
            raise RuntimeError("转录服务响应格式错误")
    return segments


def consume_response(transcript, result, offset, duration):
    pending = []
    start = end = 0
    for segment in response_segments(result, duration):
        if segment.get("no_speech_prob", 0) > 0.6 and segment.get("avg_logprob", 0) < -1.0:
            continue
        text = segment["text"].strip()
        if not text:
            continue
        # Plan GUI-4 Q1.1: checked before the 疑似重复 prefix, which itself contains CJK text.
        if not has_content(text):
            continue
        if segment.get("compression_ratio", 0) > 2.4:
            text = "[疑似重复，待核对] " + text
        if not pending:
            start = offset + segment["start"]
        end = offset + segment["end"]
        pending.append(text)
        joined = " ".join(pending)
        if text.endswith((".", "?", "!", "。", "？", "！")) or len(joined) >= 240:
            transcript.append(joined, start, end)
            pending.clear()
    if pending:
        transcript.append(" ".join(pending), start, end)


async def record(directory: Path, transport=None):
    meta = read_json(directory / "session.json")
    state_path = directory / "asr-state.json"
    transcript = Transcript(directory)
    state = dict(status="验证转录服务", seconds=0, captured=0, queued=0, level=0,
                 input_overflows=0, lag=0, last="", count=0, pending="", buffer="", asr_device="api")
    write_json(state_path, state)
    async with httpx.AsyncClient(timeout=API_TIMEOUT, transport=transport,
                                 headers={"Authorization": "Bearer " + os.environ["LECTURE_ASR_API_KEY"]}) as client:
        await verify_service(client, meta)
        if (directory / "stop").exists():
            state["status"] = "转录完成"
            write_json(state_path, state)
            return
        audio = AudioBuffer(directory)
        stream = decoder = feeder = consumer = None
        finished = asyncio.Event()
        extracted = 0.0
        last_write = 0.0
        gain = mic_gain(meta)
        weak = WeakInput()
        weak.levels_path = directory / "levels.jsonl"  # Plan GUI-4 Q3.2: read by weak_spans after class.

        def publish(force=False):
            nonlocal last_write
            if not force and time.monotonic() - last_write < 0.15:
                return
            if gain:
                state["gain_notice"] = gain.notice
                if gain.adjusted is not None:
                    state["gain_volume"] = gain.adjusted  # Plan N3.5: restored after the run when unchanged.
                if gain.change:
                    state["gain_change"] = gain.change  # Plan GUI-4 Q2.3: GUI banner only, never the note.
            state.update(audio.snapshot(), lag=max(0, extracted - state["seconds"]),
                         last=transcript.last, count=transcript.count)
            write_json(state_path, state)
            last_write = time.monotonic()

        def callback(indata, frames, timing, status):
            if gain:
                gain.observe(indata[:, 0])
            data = np.clip(indata[:, 0], -1, 1)
            pcm = (data * 32767).astype(np.int16).tobytes()
            audio.push(pcm, float(np.sqrt(np.mean(data * data))), bool(status.input_overflow))

        async def feed():
            paused = False
            while not (directory / "stop").exists() and not audio.snapshot()["capture_error"]:
                want_pause = (directory / "pause").exists()
                if want_pause != paused:
                    paused = want_pause
                    audio.pause(paused)
                    weak.pause()
                    if gain:
                        gain.pause(paused)
                    if stream:
                        stream.stop() if paused else stream.start()
                    state["status"] = "已暂停" if paused else "录制中"
                    publish(True)
                if gain:
                    gain.weak = weak.notice == WEAK_NOTICE  # Plan GUI-4 Q2.2: raises only while it shows.
                if gain and gain.poll():
                    publish(True)
                publish()
                # File input has no real-time pacing with --fast; do not outrun the bounded queue.
                if paused or not decoder or audio.snapshot()["queued"] > 60:
                    await asyncio.sleep(0.05)
                    continue
                pcm = await decoder.stdout.read(16000)
                if not pcm:
                    if await decoder.wait() != 0:
                        raise RuntimeError("无法解码输入音频")
                    break
                if not meta.get("fast"):
                    await asyncio.sleep(len(pcm) / BYTES_PER_SECOND)
                audio.push(pcm, 0, False)
                await asyncio.sleep(0)
            if stream:
                stream.stop()
            audio.pause(True)
            state["status"] = "转录收尾中"
            publish(True)
            finished.set()

        async def consume():
            nonlocal extracted
            pending = bytearray()
            offset = 0.0
            while True:
                pcm = audio.pop()
                if pcm:
                    pending.extend(pcm)
                    extracted += len(pcm) / BYTES_PER_SECOND
                    if weak.add(pcm, transcript.count):
                        # GUI-only, never "warning": retries clear that one, and the saved note reports it.
                        # Only a long total reaches the note, as one summary sentence.
                        state["weak_input"] = weak.notice
                        state["weak_input_seconds"] = weak.seconds
                        publish(True)
                    publish()
                elif not finished.is_set():
                    await asyncio.sleep(0.05)
                    continue
                while len(pending) >= 30 * BYTES_PER_SECOND or (finished.is_set() and not pcm and pending):
                    cut = segment_cut(pending)
                    part = bytes(pending[:cut])
                    del pending[:cut]
                    duration = cut / BYTES_PER_SECOND
                    samples = np.frombuffer(part, dtype="<i2").astype(np.float32)
                    if np.max(np.abs(samples), initial=0) > 32:
                        result = await transcribe(client, meta, part, transcript.last, state, lambda: publish(True))
                        consume_response(transcript, result, offset, duration)
                    offset += duration
                    state["seconds"] = offset
                    publish()
                if finished.is_set() and not pcm:
                    return

        try:
            if meta.get("audio_file"):
                decoder = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-nostdin", "-v", "error", "-i", meta["audio_file"],
                    "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            else:
                import sounddevice as sd
                stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=8000,
                                        device=meta.get("device"), callback=callback)
                stream.start()
            state["status"] = "录制中"
            publish(True)
            feeder = asyncio.create_task(feed())
            consumer = asyncio.create_task(consume())
            done, _ = await asyncio.wait((feeder, consumer), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            await feeder
            async with asyncio.timeout(drain_timeout(state)):
                await consumer
            if audio.snapshot()["capture_error"]:
                raise RuntimeError(audio.snapshot()["capture_error"])
            state["status"] = "转录完成"
            publish(True)
        finally:
            for task in (feeder, consumer):
                if task:
                    task.cancel()
            await asyncio.gather(*(task for task in (feeder, consumer) if task), return_exceptions=True)
            if stream:
                stream.close()
            audio.close()
            if decoder and decoder.returncode is None:
                decoder.terminate()
                await decoder.wait()


def run(directory: Path, *, transport=None) -> int:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        asyncio.run(record(directory, transport))
        return 0
    except Exception as exc:
        state = read_json(directory / "asr-state.json")
        message = str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else f"音频处理失败（{type(exc).__name__}）"
        state.update(status="转录失败", error=message or "转录收尾超时")
        write_json(directory / "asr-state.json", state)
        return 1
