import json
import re

import pytest

from lecture_cli import final_notes, worker
from lecture_cli.asr import session_context
from lecture_cli.glossary import load_glossary, notes_glossary, terminology_warnings
from lecture_cli.storage import Journal, linked_sources, write_json


@pytest.fixture
def lecture(tmp_path):
    directory = tmp_path / 'session'
    directory.mkdir()
    write_json(directory / 'session.json', dict(course='MATH481', started='synthetic',
               model='deepseek-flash', output=str(tmp_path / 'notes.md')))
    source = [dict(id=i, start=f'00:00:{i:02}', end=f'00:00:{i+1:02}',
                   text=f'Original statement {i}.') for i in range(1, 9)]
    (directory / 'transcript.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in source))
    journal = Journal(directory)
    yield journal, source
    journal.close()


def test_topic_continues_across_request_boundary_and_originals_reach_writer(lecture, monkeypatch):
    journal, source = lecture
    monkeypatch.setattr(final_notes, 'CHAPTER_CHARS', 250)
    batches = list(final_notes.chapters(source))
    assert len(batches) > 1
    seen = []
    planning = []
    def api(messages, model, tokens):
        prompt = messages[-1]['content']
        if tokens == final_notes.PLAN_TOKENS:
            ids = [int(n) for n in re.findall(r'\[L(\d+) ', prompt.split('本批完整原文：')[1])]
            planning.append(ids)
            return json.dumps(dict(continues_previous=len(planning) > 1, topics=[
                dict(title='平面同痕', question='允许哪些变形？', first=ids[0], last=ids[-1])]))
        text = prompt.split('本章完整原始转录：')[1].split('后文衔接')[0]
        ids = [int(n) for n in re.findall(r'\[L(\d+) ', text)]
        seen.extend(ids)
        return f'理解连续变形。[L{ids[0]}]\n<!-- REVIEW -->\n- 缺少图示。[L{ids[-1]}]'
    final_notes.generate(journal, source, api)
    outline = json.loads(dict(journal.db.execute('SELECT key, value FROM info'))['outline'])
    assert len(outline) == 1 and outline[0]['first'] == 1 and outline[0]['last'] == 8
    assert seen == list(range(1, 9))
    main = (journal.directory.parent / 'notes.md').read_text()
    assert main.count('**平面同痕**') == 1
    assert '缺少图示' not in main
    assert '缺少图示' in (journal.directory.parent / 'notes.review.md').read_text()
    assert 'notes.transcript.md#live-L1' in main
    # Saved plan and chapters allow a render-only resume, with no new model calls.
    final_notes.generate(journal, source, lambda *a: pytest.fail('unexpected API request'))


@pytest.mark.parametrize('ranges', [[(1, 3), (5, 8)], [(1, 5), (5, 8)], [(1, 9)]])
def test_invalid_outline_preserves_sources_without_pretending_complete(lecture, ranges):
    journal, source = lecture
    result = dict(continues_previous=False, topics=[
        dict(title='主题', question='问题？', first=a, last=b) for a, b in ranges])
    final_notes.generate(journal, source, lambda *a: json.dumps(result))
    info = dict(journal.db.execute('SELECT key, value FROM info'))
    assert info['detail_status'] == 'incomplete'
    assert all(r['text'] in (journal.directory.parent / 'notes.transcript.md').read_text() for r in source)


def test_live_and_refined_citations_resolve_to_distinct_records(lecture):
    journal, source = lecture
    journal.save(source, '实时预览 [L1]')
    corrected = [dict(id=1, start='00:00:01', end='00:00:09', text='Corrected English.')]
    (journal.directory / 'refined.jsonl').write_text(json.dumps(corrected[0]) + '\n')
    write_json(journal.directory / 'refinement-state.json', dict(complete=True, count=1))
    journal.save_detail(corrected, '## 中文\n校正结果 [L1]')
    journal.render(finished=True)
    root = journal.directory.parent
    assert 'notes.transcript.md#refined-L1' in (root / 'notes.md').read_text()
    assert 'notes.transcript.md#live-L1' in (root / 'notes.live.md').read_text()
    transcript = (root / 'notes.transcript.md').read_text()
    assert all(r['text'] in transcript for r in source)
    assert '<a id="live-L1">' in transcript and '<a id="refined-L1">' in transcript
    assert 'Corrected English.' in transcript
    assert '实时预览' not in (root / 'notes.md').read_text()
    assert not (root / 'notes.review.md').exists()


def test_citation_must_be_in_supplied_evidence():
    with pytest.raises(worker.APIError, match='未提供'):
        worker.checked_completion([], 'model', 99, lambda *a: '编造的引用 [L2]',
                                  allowed_sources={5, 6})
    citation = '[L10 00:03:25–00:03:35]'
    assert '#live-L10' in linked_sources(citation, 'notes.transcript.md', 'live')
    with pytest.raises(worker.APIError, match='未提供'):
        worker.validate_content(citation, 10, {5, 6})


def test_glossary_keeps_background_out_of_asr_and_rejects_oversized_hints(tmp_path):
    assert load_glossary(tmp_path) == dict(asr_context='', glossary=[])
    entries = [dict(term='planar isotopy', translation='平面同痕', source='讲义 p.31')]
    write_json(tmp_path / 'glossary.json', entries)
    data = load_glossary(tmp_path)
    meta = dict(asr_model='large-v3-turbo', context='教学背景' * 2000, **data)
    assert session_context(meta) == 'planar isotopy'
    assert '平面同痕' in notes_glossary(meta)
    assert meta['context'] not in session_context(meta)
    assert session_context(dict(meta, asr_model='qwen3-asr-1.7b')) is None
    write_json(tmp_path / 'glossary.json', [dict(term='x' * 1001, translation='词')])
    with pytest.raises(ValueError, match='1000'):
        load_glossary(tmp_path)


def test_markdown_section_keeps_latex_backslashes_and_separates_review():
    # These commands begin with JSON escape letters (\f \b \n \t \r) or are invalid escapes (\a).
    math = r'$$\frac{\beta}{\alpha} \neq \nabla \times \theta \rightarrow \tau$$'
    response = '## 定义\n\n' + r'定义 \(x=1\) [L1]' + f'\n\n{math}\n<!-- REVIEW -->\n缺图 [L2]'
    parsed = worker.checked_completion([], 'model', 2,
        lambda messages, model, tokens: final_notes.section_response(
            messages, model, tokens, lambda *a: response), 8000)
    assert '定义 $x=1$ [L1]' in parsed and math in parsed
    assert parsed.startswith('### 定义')
    assert parsed.endswith('\n<!-- REVIEW -->\n缺图 [L2]')


@pytest.mark.parametrize('response, hidden', [
    ('正文 [L1]\n<!-- REVIEW\n疑点 [L2]\n-->', False),   # review written inside the marker
    ('正文 [L1]\n<!-- 疑点 [L2] -->', True),
])
def test_html_comment_cannot_silently_hide_model_review(lecture, response, hidden):
    journal, records = lecture
    def api(messages, model, tokens):
        if tokens == final_notes.PLAN_TOKENS:
            return json.dumps(dict(continues_previous=False, topics=[
                dict(title='主题', question='内容？', first=1, last=8)]))
        return response
    final_notes.generate(journal, records, api)
    info = dict(journal.db.execute('SELECT key, value FROM info'))
    review = (journal.directory.parent / 'notes.review.md').read_text()
    assert '<!--' not in (journal.directory.parent / 'notes.md').read_text()
    if hidden:
        assert info['detail_status'] == 'incomplete' and '隐藏注释' in review
    else:
        assert info['detail_status'] == 'complete' and '疑点' in review


def test_heading_cannot_hide_drifting_translation_in_prose():
    meta = dict(glossary=[dict(term='oriented link', translation='有向链环')])
    assert terminology_warnings(meta, '### 有向链环\n\n定向链环（oriented link）')
    assert not terminology_warnings(meta, '有向链环（oriented link）')


def test_neighboring_context_does_not_duplicate_owned_review(lecture):
    journal, source = lecture
    journal.save_detail(source[:4], '## 第一主题\n正文 [L1]\n<!-- REVIEW -->\n'
                        '本主题缺图 [L4]\n\n邻接主题不清楚 [L8]')
    journal.save_detail(source[4:], '## 第二主题\n正文 [L5]\n<!-- REVIEW -->\n'
                        '本主题听不清 [L8 00:00:08–00:00:09]')
    review = (journal.directory.parent / 'notes.review.md').read_text()
    assert '本主题缺图' in review and '本主题听不清' in review
    assert '邻接主题不清楚' not in review
    assert review.count('#live-L8') == 1
