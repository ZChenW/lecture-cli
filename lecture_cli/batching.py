"""Merge source fragments without changing their identity or dropping their text."""
import re

MAX_BATCH_CHARS = 6000
MIN_BATCH_CHARS = 400


def seconds(value):
    hours, minutes, secs = value.split(':')
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)


def boundary(record, following=None):
    if re.search(r'[.!?。！？][\"\'”’）)]*$', record['text'].rstrip()):
        return True
    return following is not None and seconds(following['start']) - seconds(record['end']) >= 1.5


def next_batch(records, cursor, limit=MAX_BATCH_CHARS, *, flush=True):
    pending = [record for record in records if record['id'] > cursor]
    batch, size = [], 0
    for record in pending:
        if batch and size + len(record['text']) > limit:
            break
        batch.append(record)
        size += len(record['text'])
    if flush or not batch:
        return batch
    consumed, cut = 0, 0
    for i, record in enumerate(batch):
        consumed += len(record['text'])
        following = pending[i + 1] if i + 1 < len(pending) else None
        if consumed >= max(MIN_BATCH_CHARS, size / 2) and boundary(record, following):
            cut = i + 1
    if cut:
        return batch[:cut]
    # Bounded fallback for unpunctuated continuous speech; do not wait indefinitely.
    return batch if len(batch) < len(pending) or size >= limit else []


def ready_batch(records, cursor, *, now, due, interval, finished=False):
    if not finished and now < due:
        return []
    # A short tail waits at most 30 extra seconds (less with a shorter interval).
    flush = finished or now >= due + min(30, interval)
    return next_batch(records, cursor, flush=flush)


def merged_source_text(records):
    paragraphs, group, size = [], [], 0
    def flush():
        nonlocal group, size
        if group:
            heading = f"{group[0]['start']}–{group[-1]['end']}"
            paragraphs.append(heading + '\n' + ' '.join(f"[L{r['id']}] {r['text']}" for r in group))
            group, size = [], 0
    for i, record in enumerate(records):
        if group and seconds(record['start']) - seconds(group[-1]['end']) >= 1.5:
            flush()
        group.append(record)
        size += len(record['text'])
        following = records[i + 1] if i + 1 < len(records) else None
        if size >= 1200 or (size >= MIN_BATCH_CHARS and boundary(record, following)):
            flush()
    flush()
    return '\n\n'.join(paragraphs)
