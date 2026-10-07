import json
import re
import time

import pytest

from lecture_cli import final_notes, worker
from lecture_cli.storage import Journal, read_json, write_json


@pytest.fixture(autouse=True)
def fixed_outline(monkeypatch):
    # These tests isolate final writing/recovery. Planner responses are tested separately.
    def plan(journal, records, call):
        topics = [dict(title=f'主题 {i}', question='定义与证明是什么？',
                       first=chunk[0]['id'], last=chunk[-1]['id'])
                  for i, chunk in enumerate(final_notes.chapters(records), 1)]
        journal.set_info('outline', json.dumps(topics))
        return topics
    monkeypatch.setattr(final_notes, 'plan_topics', plan)


@pytest.fixture
def lecture(tmp_path):
    directory = tmp_path / 'session'
    directory.mkdir()
    write_json(directory / 'session.json', dict(course='MATH421', started='2026-09-14',
               output=str(tmp_path / 'notes.md'), model='deepseek-flash', interval=1))
    records = [dict(id=i, start=f'00:{i:02}:00', end=f'00:{i:02}:30',
                    text=f'UNIQUE_SOURCE_{i:03} ' + 'lecture evidence ' * 100) for i in range(1, 34)]
    (directory / 'transcript.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
    return directory, records


def test_all_original_source_including_late_lecture_reaches_detailed_requests(lecture):
    directory, records = lecture
    journal = Journal(directory)
    journal.save(records, 'Deliberately incomplete live summary [L1]')
    prompts = []
    def api(messages, model, max_tokens):
        assert '不是简短摘要' in messages[0]['content']
        assert max_tokens == 8000
        prompts.append(messages[-1]['content'])
        return f'### 详细概念 {len(prompts)}\n定义的条件及证明步骤\n#### 例题\n计算过程。'
    final_notes.generate(journal, records, api)
    assert len(prompts) > 1
    assert sum(len(r['text']) for r in records) > 40000
    for record in records:
        marker = record['text'].split()[0]
        # Each fragment must appear in a chapter's main source, not only its context.
        assert sum(marker in p.split('本章完整原始转录：\n')[1].split('\n\n后文衔接')[0] for p in prompts) == 1
    assert journal.detail_cursor == records[-1]['id']
    assert dict(journal.db.execute('SELECT key, value FROM info'))['detail_status'] == 'complete'
    text = (directory.parent / 'notes.md').read_text()
    assert '## 详细课堂笔记\n' in text
    assert '## 学习路线' in text and '## 随堂记录' not in text
    assert '#### 详细概念' in text and '##### 例题' in text
    assert 'Deliberately incomplete live summary' not in text
    assert 'Deliberately incomplete live summary' in (directory.parent / 'notes.live.md').read_text()
    journal.close()


def test_midway_api_failure_retains_successful_chapter_and_all_remaining_originals(lecture):
    directory, records = lecture
    journal = Journal(directory)
    journal.save(records, 'Lossy live summary [L1]')
    journal.add_warning('一次音频丢帧')
    chunks = list(final_notes.chapters(records))
    calls = []
    def api(*args):
        calls.append(args)
        if len(calls) == 1:
            return '## 已完成章节\n详细内容 [L1]'
        raise worker.APIError('truncated or offline')
    final_notes.generate(journal, records, api)
    journal.preserve_detail_tail()  # Controller cleanup must not replace completed chapters.
    journal.render(finished=True)
    text = (directory.parent / 'notes.md').read_text()
    assert '已完成章节' in text and '未全部完成' in text and '一次音频丢帧' in text
    for record in records[len(chunks[0]):]:
        assert record['text'].strip() in (directory.parent / 'notes.transcript.md').read_text()
    assert 'truncated or offline' in (directory.parent / 'notes.review.md').read_text()
    assert len(calls) == 2
    assert journal.detail_cursor == records[-1]['id']
    journal.close()


def test_resume_after_render_failure_does_not_repeat_successful_request(lecture, monkeypatch):
    directory, records = lecture
    journal = Journal(directory)
    def disk_failure(*args, **kwargs):
        raise OSError('output unavailable')
    monkeypatch.setattr(journal, 'render', disk_failure)
    with pytest.raises(OSError):
        final_notes.generate(journal, records, lambda *a: 'Committed chapter [L1]')
    first_end = journal.detail_cursor
    journal.close()
    recovered = Journal(directory)
    calls = []
    def api(*args):
        calls.append(args)
        return 'Remaining chapter'
    final_notes.generate(recovered, records, api)
    assert first_end > 0
    assert len(calls) == len(list(final_notes.chapters(records))) - 1
    assert 'Committed chapter' in (directory.parent / 'notes.md').read_text()
    recovered.close()


def test_controller_recovery_preserves_raw_details_already_compressed_in_live_notes(lecture):
    directory, records = lecture
    journal = Journal(directory)
    journal.save(records, 'Only a brief live summary [L1]')
    journal.preserve_detail_tail()
    journal.add_warning('capture warning')
    journal.render(finished=True)
    text = (directory.parent / 'notes.md').read_text()
    transcript = (directory.parent / 'notes.transcript.md').read_text()
    assert all(record['text'].strip() in transcript for record in records)
    assert '详细笔记未全部完成' in text and 'capture warning' in text
    journal.close()


def test_finished_lecture_budget_scales_with_all_source_even_when_live_notes_are_done(lecture):
    _, records = lecture
    assert final_notes.finish_timeout(records, records[-1]['id']) == 1620 * len(list(final_notes.chapters(records)))
    assert final_notes.finish_timeout(records, 0) > final_notes.finish_timeout(records, records[-1]['id'])


def test_resume_retries_raw_fallback_instead_of_marking_it_complete(lecture):
    directory, records = lecture
    journal = Journal(directory)
    journal.add_warning('capture warning remains')
    def fail(*args):
        raise worker.APIError('offline')
    final_notes.generate(journal, records, fail)
    calls = []
    def recovered(*args):
        calls.append(args)
        return '### 详细笔记\n定义和证明'
    final_notes.generate(journal, records, recovered)
    assert len(calls) == len(list(final_notes.chapters(records)))
    assert not journal.db.execute('SELECT COUNT(*) FROM details WHERE fallback=1').fetchone()[0]
    text = (directory.parent / 'notes.md').read_text()
    assert 'capture warning remains' in text and '详细笔记未全部完成' not in text
    journal.close()


def test_final_generation_runs_even_if_incremental_api_failed(lecture, monkeypatch):
    directory, records = lecture
    (directory / 'capture.done').touch()
    calls = []
    def api(messages, model, max_tokens=2000):
        calls.append(max_tokens)
        if max_tokens == 2000:
            raise worker.APIError('temporary outage')
        return '### 恢复后生成的详细笔记\n内容'
    monkeypatch.setattr(worker, 'complete', api)
    assert worker.run(directory) == 0
    assert 8000 in calls
    assert '## 详细课堂笔记\n' in (directory.parent / 'notes.md').read_text()
    assert read_json(directory / 'notes-state.json')['status'] == '完成'


def test_transient_failures_are_retried_before_any_chapter_falls_back(lecture, monkeypatch):
    directory, records = lecture
    journal = Journal(directory)
    waits = []
    monkeypatch.setattr(worker.time, 'sleep', waits.append)
    calls = []
    def api(*args):
        calls.append(args)
        if len(calls) in (1, 2, 4):
            raise worker.TransientAPIError('DeepSeek HTTP 503')
        return '### 详细笔记\n定义和证明'
    final_notes.generate(journal, records, api)
    chapters = len(list(final_notes.chapters(records)))
    assert len(calls) == chapters + 3 and waits == [5, 20, 5]
    assert dict(journal.db.execute('SELECT key, value FROM info'))['detail_status'] == 'complete'
    assert not journal.db.execute('SELECT COUNT(*) FROM details WHERE fallback=1').fetchone()[0]
    journal.close()


def test_persistent_transient_failure_still_falls_back(lecture, monkeypatch):
    directory, records = lecture
    journal = Journal(directory)
    monkeypatch.setattr(worker.time, 'sleep', lambda delay: None)
    calls = []
    def api(*args):
        calls.append(args)
        raise worker.TransientAPIError('DeepSeek HTTP 429')
    final_notes.generate(journal, records, api)
    assert len(calls) == 3
    assert dict(journal.db.execute('SELECT key, value FROM info'))['detail_status'] == 'incomplete'
    assert journal.detail_cursor == records[-1]['id']
    journal.close()


def test_concurrent_chapters_overlap_requests_and_are_saved_in_lecture_order(lecture, monkeypatch):
    import threading
    directory, records = lecture
    monkeypatch.setattr(final_notes, 'CHAPTER_CHARS', 9000)
    chunks = list(final_notes.chapters(records))
    assert len(chunks) >= 6
    journal = Journal(directory)
    together = threading.Barrier(3)
    def api(messages, model, max_tokens):
        chapter = int(re.search(r'当前第 (\d+)/', messages[-1]['content'])[1])
        if chapter <= 3:
            together.wait(5)  # Raises unless three requests are in flight at once.
        if chapter == 1:
            time.sleep(0.2)   # The first chapter finishes last; later results must wait for it.
        return f'CHAPTER_BODY_{chapter:02}'
    final_notes.generate(journal, records, api, workers=3)
    text = (directory.parent / 'notes.md').read_text()
    assert re.findall(r'CHAPTER_BODY_(\d+)', text) == [f'{i:02}' for i in range(1, len(chunks) + 1)]
    assert journal.detail_cursor == records[-1]['id']
    journal.close()


def test_concurrent_failure_keeps_earlier_chapters_and_raw_source_for_the_rest(lecture, monkeypatch):
    directory, records = lecture
    monkeypatch.setattr(final_notes, 'CHAPTER_CHARS', 9000)
    journal = Journal(directory)
    def api(messages, model, max_tokens):
        chapter = int(re.search(r'当前第 (\d+)/', messages[-1]['content'])[1])
        if chapter == 3:
            raise worker.APIError('truncated')
        return f'CHAPTER_BODY_{chapter:02}'
    final_notes.generate(journal, records, api, workers=3)
    text = (directory.parent / 'notes.md').read_text()
    assert re.findall(r'CHAPTER_BODY_(\d+)', text) == ['01', '02']
    assert journal.detail_cursor == records[-1]['id']
    assert journal.db.execute('SELECT COUNT(*) FROM details WHERE fallback=0').fetchone()[0] == 2
    journal.close()
