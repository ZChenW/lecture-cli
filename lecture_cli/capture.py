"""WhisperLiveKit streaming adapter with microphone/file input and pause control."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path

from .storage import read_json, write_json
from .audio_buffer import AudioBuffer, drain_timeout
from .asr import build_engine, session_context
from .mic_gain import mic_gain


def timestamp(seconds: float) -> str:
    seconds = round(seconds, 2)  # 59.997 must carry into the minute, not print as 60.00
    return f"{int(seconds) // 3600:02}:{int(seconds) // 60 % 60:02}:{seconds % 60:05.2f}"


class Transcript:
    """Consume committed tokens, not mutable display-line indices.

    WLK can grow/resegment the last line and prune historical display lines.
    Token identity survives both operations within a session.
    """
    def __init__(self, directory: Path):
        self.directory = directory
        self.seen: set[tuple] = set()
        self.pending: list = []
        self.count = 0
        self.last = ""

    def append(self, text: str, start: float, end: float) -> None:
        if not text.strip():
            return
        self.count += 1
        record = {"id": self.count, "start": timestamp(start), "end": timestamp(end), "text": text.strip()}
        with (self.directory / "transcript.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
        self.last = text.strip()

    def consume(self, lines) -> None:
        for line in lines:
            if line.speaker == -2:
                continue
            for token in line.tokens or []:
                key = (token.start, token.end, token.text)
                if key in self.seen:
                    continue
                self.seen.add(key)
                self.pending.append(token)
                if token.text.rstrip().endswith((".", "?", "!", "。", "？", "！")) or len(self.pending_text) >= 240:
                    self.flush()

    @property
    def pending_text(self) -> str:
        return "".join(t.text for t in self.pending)

    def flush(self) -> None:
        if self.pending:
            self.append(self.pending_text, self.pending[0].start, self.pending[-1].end)
            self.pending.clear()


async def record(directory: Path) -> None:
    import numpy as np
    from whisperlivekit import AudioProcessor

    meta = read_json(directory / "session.json")
    state_path = directory / "asr-state.json"
    write_json(state_path, {"status": "加载语音模型"})
    engine, device, notice = build_engine(meta)
    if (directory / "stop").exists():
        write_json(state_path, {"status": "已取消，未打开麦克风"})
        return
    processor = AudioProcessor(transcription_engine=engine, context=session_context(meta))
    transcript = Transcript(directory)
    state = {"status": "准备输入", "seconds": 0, "level": 0, "buffer": "", "pending": "",
             "asr_device": device, "device_notice": notice}
    audio = AudioBuffer(directory)
    from .refinement import AudioArchive
    archive = AudioArchive(directory) if meta.get("refine") else None
    stream = None
    decoder = None
    collector = None
    paused = False
    last_write = 0.0
    gain = mic_gain(meta)

    def publish(force=False):
        nonlocal last_write
        if force or time.monotonic() - last_write > 0.15:
            if gain:
                state["gain_notice"] = gain.notice
            if not meta.get("audio_file"):
                state.update(audio.snapshot())
            if state.get("input_overflows"):
                state["warning"] = f"音频设备报告 {state['input_overflows']} 次输入丢帧，局部内容可能缺失；录制已继续。"
            if archive and archive.error:
                state["refinement_warning"] = archive.error + "；本次将使用实时转录生成最终笔记。"
            state.update(pending=transcript.pending_text, last=transcript.last, count=transcript.count)
            write_json(state_path, state)
            last_write = time.monotonic()

    def callback(indata, frames, timing, status):
        # Never call ASR or the network on PortAudio's callback thread.
        if gain:
            gain.observe(indata[:, 0])
        data = np.clip(indata[:, 0], -1, 1)
        pcm = (data * 32767).astype(np.int16).tobytes()
        level = float(np.sqrt(np.mean(data * data)))
        # Persist outside the asyncio loop: ASR backpressure cannot fill a 60-second RAM queue.
        # Only a small PCM block is written; no inference, API calls or fsync here.
        audio.push(pcm, level, bool(status.input_overflow))

    async def collect(generator):
        async for front in generator:
            if front.status == "error":
                raise RuntimeError("语音识别引擎报告错误，请检查模型和音频设备")
            transcript.consume(front.lines)
            state.update(buffer=front.buffer_transcription.strip(),
                         lag=front.remaining_time_transcription_processing)
            publish()

    try:
        generator = await processor.create_tasks()
        collector = asyncio.create_task(collect(generator))
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
        while not (directory / "stop").exists():
            if collector.done():
                collector.result()
                raise RuntimeError("语音识别提前结束")
            if audio.snapshot()["capture_error"]:
                break  # Drain accepted audio before reporting a storage/limit failure.
            want_pause = (directory / "pause").exists()
            if want_pause != paused:
                paused = want_pause
                audio.pause(paused)
                if gain:
                    gain.pause(paused)
                if stream:
                    stream.stop() if paused else stream.start()
                state["status"] = "已暂停" if paused else "录制中"
                state["level"] = 0
                publish(True)
            if gain and gain.poll():
                publish(True)
            if paused and not audio.snapshot()["queued"]:
                await asyncio.sleep(0.1)
                continue
            if decoder:
                pcm = await decoder.stdout.read(16000)
                if not pcm:
                    if await decoder.wait() != 0:
                        raise RuntimeError("无法解码输入音频")
                    break
                if not meta.get("fast"):
                    await asyncio.sleep(len(pcm) / 32000)
            else:
                pcm = audio.pop()
                if not pcm:
                    publish()
                    await asyncio.sleep(0.05)
                    continue
            if archive:
                archive.append(pcm)
            await processor.process_audio(pcm)
            state["seconds"] += len(pcm) / 32000
            publish()
        if stream:
            stream.stop()
        audio.pause(True)
        state["status"] = "转录收尾中"
        publish(True)
        async with asyncio.timeout(drain_timeout(state)):
            while pcm := audio.pop():
                if archive:
                    archive.append(pcm)
                await processor.process_audio(pcm)
                state["seconds"] += len(pcm) / 32000
                publish()
            await processor.process_audio(b"")
            await collector
        transcript.flush()
        if state["buffer"]:
            transcript.append("[未确认尾部，待核对] " + state["buffer"], state["seconds"], state["seconds"])
        state.update(status="转录完成", buffer="", lag=0)
        publish(True)
        if audio.snapshot()["capture_error"]:
            raise RuntimeError(audio.snapshot()["capture_error"])
    except Exception:
        transcript.flush()
        publish(True)
        raise
    finally:
        if stream:
            stream.close()
        audio.close()
        if archive:
            archive.close()
        if decoder and decoder.returncode is None:
            decoder.terminate()
            await decoder.wait()
        await processor.cleanup()
        if collector:
            collector.cancel()
            await asyncio.gather(collector, return_exceptions=True)


def run(directory: Path) -> int:
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    # The controller owns shutdown; terminal signals must not skip the EOF drain.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        asyncio.run(record(directory))
        return 0
    except Exception as exc:
        state = read_json(directory / "asr-state.json")
        message = str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else f"音频处理失败（{type(exc).__name__}）"
        state.update(status="转录失败", error=message or "转录收尾超时")
        write_json(directory / "asr-state.json", state)
        return 1
