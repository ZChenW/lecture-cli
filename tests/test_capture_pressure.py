"""Drive real capture.record with deterministic microphone and slow ASR boundaries."""
import asyncio
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from lecture_cli.capture import record
from lecture_cli.storage import read_json, write_json


@pytest.mark.parametrize('host_overflow,chunks', [(False, 140), (False, 720), (True, 3)])
def test_record_does_not_abort_on_backpressure_or_single_host_overflow(tmp_path, monkeypatch, host_overflow, chunks):
    sounddevice = SimpleNamespace(InputStream=None)
    whisperlivekit = SimpleNamespace(TranscriptionEngine=None, AudioProcessor=None)
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    monkeypatch.setitem(sys.modules, "whisperlivekit", whisperlivekit)
    write_json(tmp_path / 'session.json', dict(asr_model='base.en', language='en', refine=True))
    fed = []
    eof = asyncio.Event()
    class Processor:
        def __init__(self, **kwargs): pass
        async def create_tasks(self):
            async def results():
                await eof.wait()
                if False: yield None
            return results()
        async def process_audio(self, pcm):
            if pcm:
                await asyncio.sleep(0.002)
                fed.append(pcm)
            else:
                eof.set()
        async def cleanup(self): pass
    class Stream:
        def __init__(self, callback, **kwargs): self.callback = callback
        def start(self):
            # Burst represents accumulated callbacks when ASR backpressures its caller.
            for i in range(chunks):
                self.callback(np.full((8000, 1), i / chunks, dtype=np.float32), 8000, None,
                              SimpleNamespace(input_overflow=host_overflow and i == 0))
        def stop(self): pass
        def close(self): pass
    monkeypatch.setattr('lecture_cli.capture.build_engine', lambda meta: (None, 'cpu', ''))
    monkeypatch.setattr(whisperlivekit, 'AudioProcessor', Processor)
    monkeypatch.setattr(sounddevice, 'InputStream', Stream)
    async def exercise():
        async def stop():
            await asyncio.sleep(0.08)
            (tmp_path / 'stop').touch()
        stopper = asyncio.create_task(stop())
        try:
            await record(tmp_path)
        finally:
            await stopper
    asyncio.run(exercise())
    assert len(fed) == chunks, 'accepted microphone blocks must drain through EOF'
    expected = [(np.full(8000, i / chunks, dtype=np.float32) * 32767).astype(np.int16).tobytes() for i in range(chunks)]
    assert fed == expected, 'capture pressure must not reorder, duplicate or alter accepted samples'
    assert (tmp_path / 'refinement.pcm').read_bytes() == b''.join(expected)
    assert read_json(tmp_path / 'archive.json')['complete']
    state = read_json(tmp_path / 'asr-state.json')
    assert state['status'] == '转录完成'
    assert state['captured'] == chunks / 2
    assert state['queued'] == 0
    assert state['level'] == 0
    if host_overflow:
        assert state.get('warning'), 'a hardware gap must be visible, not silently ignored'


def test_ring_wrap_preserves_order_and_bounds_disk(tmp_path):
    from lecture_cli.audio_buffer import AudioBuffer
    audio = AudioBuffer(tmp_path, capacity=32)
    try:
        audio.push(b'a' * 24, 0.1, False)
        assert audio.pop(16) == b'a' * 16
        audio.push(b'b' * 20, 0.2, False)
        assert audio.pop(32) == b'a' * 8 + b'b' * 20
        assert audio.snapshot()['queued'] == 0
        assert audio.file.seek(0, 2) <= 32
    finally:
        audio.close()


def test_audio_temporary_file_is_anonymous_and_closed(tmp_path):
    from lecture_cli.audio_buffer import AudioBuffer
    audio = AudioBuffer(tmp_path)
    audio.push(b'private audio', 0.1, False)
    assert list(tmp_path.iterdir()) == []
    audio.close()
    assert audio.file.closed


def test_ring_full_reports_error_without_overwriting_accepted_audio(tmp_path):
    from lecture_cli.audio_buffer import AudioBuffer
    audio = AudioBuffer(tmp_path, capacity=16)
    try:
        audio.push(b'a' * 16, 0.1, False)
        audio.push(b'b' * 16, 0.1, False)
        assert audio.snapshot()['capture_error']
        assert audio.pop(32) == b'a' * 16
    finally:
        audio.close()


def test_pause_excludes_new_audio_but_drains_existing(tmp_path):
    from lecture_cli.audio_buffer import AudioBuffer
    audio = AudioBuffer(tmp_path, capacity=32)
    try:
        audio.push(b'a' * 8, 0.1, False)
        audio.pause(True)
        audio.push(b'x' * 8, 0.1, False)
        assert audio.pop() == b'a' * 8
        audio.pause(False)
        audio.push(b'b' * 8, 0.1, False)
        assert audio.pop() == b'b' * 8
    finally:
        audio.close()


def test_shutdown_budget_includes_audio_not_yet_fed_to_asr():
    from lecture_cli.audio_buffer import drain_timeout
    assert drain_timeout(dict(queued=70, lag=30)) == 390


def test_disk_error_is_reported_and_previous_audio_survives(tmp_path, monkeypatch):
    from lecture_cli.audio_buffer import AudioBuffer
    audio = AudioBuffer(tmp_path, capacity=32)
    try:
        audio.push(b'a' * 8, 0.1, False)
        def fail(*args):
            raise OSError('disk full')
        monkeypatch.setattr('lecture_cli.audio_buffer.os.pwrite', fail)
        audio.push(b'b' * 8, 0.1, False)
        assert '/tmp' in audio.snapshot()['capture_error']
        assert audio.pop() == b'a' * 8
    finally:
        audio.close()
