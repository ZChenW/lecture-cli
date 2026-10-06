import sys
from types import SimpleNamespace

import pytest

from lecture_cli import asr


def test_qwen_engine_keeps_requested_size_language_and_context(monkeypatch):
    calls = []
    sentinel = object()
    def engine(**kwargs):
        calls.append(kwargs)
        return sentinel
    monkeypatch.setitem(sys.modules, 'whisperlivekit', SimpleNamespace(TranscriptionEngine=engine))
    monkeypatch.setattr(asr, 'select_qwen_device', lambda _: ('cuda', ''))
    for name in asr.QWEN_MODELS:
        meta = dict(asr_model=name, asr_device='cuda', language='en',
                    asr_context='eigenvalue', context='Chinese background ' * 1000)
        result = asr.build_engine(meta)
        assert asr.session_context(meta) is None
        assert result == (sentinel, 'cuda', '')
        assert calls[-1]['model_size'] == asr.QWEN_MODELS[name]
        assert calls[-1]['backend'] == 'qwen3-streaming'
        assert calls[-1]['qwen3_streaming_context'] == 'eigenvalue'
        assert calls[-1]['qwen3_streaming_device'] == 'cuda'
    with pytest.raises(ValueError, match='指定语言'):
        asr.build_engine(dict(asr_model=name, language='auto'))


def test_qwen_device_uses_torch_capability_and_explicit_cuda_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    assert asr.select_qwen_device('cpu') == ('cpu', '')
    assert asr.select_qwen_device('auto')[0] == 'cpu'
    with pytest.raises(RuntimeError, match='Qwen GPU 不可用'):
        asr.select_qwen_device('cuda')
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)))
    assert asr.select_qwen_device('auto') == ('cuda', '')


def test_qwen_interpreter_is_separate_and_does_not_inherit_whisper_library_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(asr, '__file__', str(tmp_path / 'lecture_cli/asr.py'))
    name = 'qwen3-asr-1.7b'
    with pytest.raises(ValueError, match='install-qwen.sh'):
        asr.capture_python(name)
    executable = tmp_path / '.venv-qwen/bin/python'
    executable.parent.mkdir(parents=True)
    executable.touch()
    assert asr.capture_python(name) == str(executable)
    assert asr.capture_python('small.en') == sys.executable
    monkeypatch.setattr(asr, 'runtime_environment', lambda *_: pytest.fail('must not inject Whisper libraries'))
    assert asr.capture_environment(name, {'CUDA_VISIBLE_DEVICES': '0'}) == {'CUDA_VISIBLE_DEVICES': '0'}


def test_qwen_aliases_are_available_for_menu_and_completion():
    assert asr.resolve_asr_model('QWEN3-ASR-1.7B') == 'qwen3-asr-1.7b'
    assert set(asr.QWEN_MODELS) <= set(asr.asr_models())
