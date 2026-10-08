import json
import threading
from types import SimpleNamespace

import pytest

from lecture_cli import worker
from lecture_cli.capture import Transcript
from lecture_cli.cli import course_paths, preserve_tail, select_course
from lecture_cli.storage import ATTACHMENT_DIR, Journal, events, normalize_markdown, write_json


@pytest.fixture
def session(tmp_path):
    directory = tmp_path / "session"
    directory.mkdir()
    write_json(directory / "session.json", {"course": "MATH421", "started": "2026-09-14",
               "output": str(tmp_path / "notes.md"), "notes_model": "deepseek-flash", "interval": 1})
    return directory


def record(directory, text="A nonzero eigenvector."):
    ident = len(events(directory)) + 1
    row = {"id": ident, "start": "00:00:00", "end": "00:00:10", "text": text}
    with (directory / "transcript.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def test_failure_does_not_advance_then_retry_once(session):
    record(session)
    journal = Journal(session)
    def fail(*args):
        raise worker.APIError("offline")
    with pytest.raises(worker.APIError):
        worker.process_batch(journal, events(session), fail)
    assert journal.cursor == 0
    assert worker.process_batch(journal, events(session), lambda *args: "特征向量非零 [L1]")
    assert journal.cursor == 1
    assert not worker.process_batch(journal, events(session), lambda *args: pytest.fail("duplicate request"))
    journal.close()


def test_render_failure_recovers_committed_content_without_duplicate_api(session, monkeypatch):
    record(session)
    journal = Journal(session)
    monkeypatch.setattr(journal, "render", lambda: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        worker.process_batch(journal, events(session), lambda *args: "已写入事务 [L1]")
    journal.close()
    recovered = Journal(session)
    assert recovered.cursor == 1
    recovered.render()
    assert "已写入事务" in (session.parent / "notes.md").read_text()
    recovered.close()


def test_partial_json_line_waits_for_complete_record(session):
    record(session)
    with (session / "transcript.jsonl").open("a") as f:
        f.write('{"id":2,')
    assert len(events(session)) == 1


def test_committed_tokens_survive_line_growth_resegmentation_and_pruning(session):
    a = SimpleNamespace(start=0, end=1, text="A")
    b = SimpleNamespace(start=1, end=2, text=" vector.")
    c = SimpleNamespace(start=310, end=311, text=" Next.")
    line = lambda tokens: SimpleNamespace(speaker=1, tokens=tokens)
    transcript = Transcript(session)
    transcript.consume([line([a])])
    transcript.consume([line([a, b])])
    transcript.consume([line([a]), line([b])])
    transcript.consume([line([c])])
    transcript.flush()
    assert [r["text"] for r in events(session)] == ["A vector.", "Next."]


def test_final_partial_sentence_flushes_once(session):
    transcript = Transcript(session)
    transcript.consume([SimpleNamespace(speaker=1, tokens=[SimpleNamespace(start=0, end=1, text="unfinished")])])
    transcript.flush()
    transcript.flush()
    assert len(events(session)) == 1


def test_abnormal_exit_preserves_provisional_tail_once(session):
    record(session)
    write_json(session / "asr-state.json", {"pending": "last words", "buffer": "maybe", "seconds": 10})
    preserve_tail(session)
    preserve_tail(session)
    assert len(events(session)) == 2
    assert "待核对" in events(session)[1]["text"]


def test_killed_partial_write_does_not_corrupt_recovered_tail(session):
    record(session)
    with (session / "transcript.jsonl").open("a") as f:
        f.write('{"id":2,"text":"partial')
    write_json(session / "asr-state.json", {"pending": "recovered tail", "seconds": 10})
    preserve_tail(session)
    assert len(events(session)) == 2
    assert "recovered tail" in events(session)[1]["text"]


def test_api_outage_at_stop_keeps_unprocessed_text_in_final_note(session, monkeypatch):
    record(session, "Keep this evidence.")
    (session / "capture.done").touch()
    monkeypatch.setattr(worker, "complete", lambda *a: (_ for _ in ()).throw(worker.APIError("offline")))
    assert worker.run(session) == 0
    text = (session.parent / "notes.md").read_text()
    assert "Keep this evidence." in (session.parent / ATTACHMENT_DIR / 'notes.transcript.md').read_text()
    assert "含待整理原文" in text and "已结束" in text


def test_capture_gap_warning_survives_final_summary_failure(session, monkeypatch):
    record(session)
    journal = Journal(session)
    journal.save(events(session), "特征向量非零 [L1]")
    journal.close()
    warning = "音频设备报告 1 次输入丢帧，局部内容可能缺失；录制已继续。"
    write_json(session / "asr-state.json", {"warning": warning})
    (session / "capture.done").touch()
    monkeypatch.setattr(worker, "complete", lambda *a: (_ for _ in ()).throw(worker.APIError("offline")))
    assert worker.run(session) == 0
    text = (session.parent / "notes.md").read_text()
    assert warning in text
    assert "详细笔记未全部完成" in text
    assert "特征向量非零" in (session.parent / ATTACHMENT_DIR / 'notes.live.md').read_text()


def test_producer_can_append_while_api_waits_and_worker_drains_last_batch(session, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []
    errors = []
    record(session, "First statement.")
    def delayed(messages, model, *args):
        calls.append(messages[-1]["content"])
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        if args and args[0] == 4000:
            return json.dumps(dict(continues_previous=False, topics=[
                dict(title='课堂要点', question='讲了什么？', first=1, last=2)]))
        return '课堂要点 [L1]' if args and args[0] == 8000 else '课堂要点 [L1]'
    monkeypatch.setattr(worker, "complete", delayed)
    def run():
        try:
            worker.run(session)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(5)
    record(session, "Second statement while API is waiting.")
    (session / "capture.done").touch()
    assert len(events(session)) == 2
    release.set()
    thread.join(5)
    assert not thread.is_alive() and not errors
    assert len(calls) == 4  # two incremental batches + planning + final writing
    assert "Second statement" in calls[1]
    journal = Journal(session)
    assert journal.cursor == 2
    journal.close()


def test_course_aliases_and_no_escape(tmp_path):
    (tmp_path / "CS687_HW").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "outside").symlink_to(tmp_path.parent, target_is_directory=True)
    assert [p.name for p in course_paths(tmp_path)] == ["CS687_HW"]
    assert select_course(tmp_path, "cs687").name == "CS687_HW"
    with pytest.raises(ValueError):
        select_course(tmp_path, "../outside")


def test_obsidian_formula_normalization():
    assert normalize_markdown(r"\(Av=\lambda v\), \[A^TA\]") == r"$Av=\lambda v$, $$A^TA$$"


def test_malformed_formula_is_repaired_before_committing(session):
    record(session)
    journal = Journal(session)
    calls = []
    def reply(messages, model):
        calls.append(messages)
        return r"$\operatorname{diag}(2, )$ [L1]" if len(calls) == 1 else "对角元素为 2 和 3。 [L1]"
    worker.process_batch(journal, events(session), reply)
    assert len(calls) == 2 and "2 和 3" in journal.bodies()
    journal.close()


def test_invalid_citation_never_commits(session):
    record(session)
    journal = Journal(session)
    with pytest.raises(worker.APIError):
        worker.process_batch(journal, events(session), lambda *args: "内容 [L999]")
    assert journal.cursor == 0
    journal.close()


@pytest.mark.parametrize("reply", [
    {"choices": [{"finish_reason": "length", "message": {"content": "cut off"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
    {"choices": []},
])
def test_reject_truncated_or_empty_api_output(reply, monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            assert kwargs["json"]["thinking"] == {"type": "disabled"}
            return SimpleNamespace(status_code=200, json=lambda: reply)
    monkeypatch.setenv("LECTURE_NOTES_API_KEY", "test-only")
    monkeypatch.setattr(worker.httpx, "Client", Client)
    with pytest.raises(worker.APIError):
        worker.complete([], "deepseek-flash")


@pytest.mark.parametrize("failure, transient", [
    (503, True), (429, True), (400, False), (401, False),
    (worker.httpx.ReadTimeout("slow"), True), (worker.httpx.ConnectError("offline"), True),
])
def test_only_network_and_server_failures_are_marked_transient(failure, transient, monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            if isinstance(failure, Exception):
                raise failure
            return SimpleNamespace(status_code=failure)
    monkeypatch.setenv("LECTURE_NOTES_API_KEY", "test-only")
    monkeypatch.setattr(worker.httpx, "Client", Client)
    with pytest.raises(worker.APIError) as caught:
        worker.complete([], "deepseek-flash")
    assert isinstance(caught.value, worker.TransientAPIError) is transient
