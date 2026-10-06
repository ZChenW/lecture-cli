from lecture_cli.batching import merged_source_text, next_batch, ready_batch


def records(texts):
    return [dict(id=i, start=f'00:00:{i:02}', end=f'00:00:{i+1:02}', text=text)
            for i, text in enumerate(texts, 1)]


def test_live_batch_defers_tiny_fragments_but_has_finite_wait():
    source = records(['A vector', ' must be nonzero.'])
    assert not ready_batch(source, 0, now=60, due=60, interval=60)
    assert ready_batch(source, 0, now=90, due=60, interval=60) == source
    assert ready_batch(source, 0, now=10, due=60, interval=60, finished=True) == source


def test_batch_stops_after_complete_sentence_and_preserves_unfinished_tail():
    source = records(['definition ' * 45 + '.', ' unfinished explanation'])
    assert next_batch(source, 0, flush=False) == source[:1]
    assert next_batch(source, 1) == source[1:]


def test_pause_can_end_unpunctuated_paragraph():
    source = records(['definition ' * 45, 'new topic'])
    source[1]['start'] = '00:00:05'
    source[1]['end'] = '00:00:06'
    assert next_batch(source, 0, flush=False) == source[:1]


def test_long_unpunctuated_backlog_drains_in_order_without_loss():
    source = records([str(i) + ' evidence ' * 50 for i in range(40)])
    processed, cursor = [], 0
    while batch := next_batch(source, cursor):
        assert sum(len(r['text']) for r in batch) <= 6000
        processed.extend(batch)
        cursor = batch[-1]['id']
    assert processed == source
    assert next_batch(source, 0, flush=False)


def test_merged_paragraph_preserves_all_source_ids_and_text():
    source = records(['Let v be', ' nonzero.', 'Then A v equals lambda v.'])
    text = merged_source_text(source)
    assert text.count('\n') == 1
    for record in source:
        assert f"[L{record['id']}] {record['text']}" in text


def test_retry_deadline_is_not_bypassed_by_new_backlog():
    source = records(['x' * 1000 for _ in range(20)])
    assert not ready_batch(source, 0, now=69, due=70, interval=60)
    assert ready_batch(source, 0, now=70, due=70, interval=60)
