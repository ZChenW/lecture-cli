import json

import pytest

from lecture_cli import runs


def meta(stem, course="课程"):
    return {"output": f"/notes/{stem}.md", "course": course, "started": "2026-10-07T14:30:00-04:00",
            "controller_pid": 123, "transcript": "不应出现的转录文字", "api_key": "sk-secret"}


def test_begin_writes_only_the_registry_fields(isolated_run_registry, tmp_path):
    identifier = runs.begin(meta("2026-10-07_143000-课堂笔记-a1b2c3"), tmp_path / "work")

    record = json.loads((isolated_run_registry / f"{identifier}.json").read_text())
    assert identifier == "2026-10-07_143000-课堂笔记-a1b2c3"
    assert record == {"run_id": identifier, "course": "课程", "output": "/notes/2026-10-07_143000-课堂笔记-a1b2c3.md",
                      "started": "2026-10-07T14:30:00-04:00", "controller_pid": 123,
                      "directory": str(tmp_path / "work"), "status": "running"}


def test_registry_keeps_only_the_newest_records(isolated_run_registry, tmp_path):
    for index in range(runs.KEEP + 5):
        runs.begin(meta(f"2026-10-07_{index:06d}-课堂笔记-a1b2c3"), tmp_path)

    names = sorted(path.stem for path in isolated_run_registry.glob("*.json"))
    assert len(names) == runs.KEEP
    assert names[0] == "2026-10-07_000005-课堂笔记-a1b2c3"


def test_update_merges_and_recovery_only_touches_existing_records(isolated_run_registry, tmp_path):
    identifier = runs.begin(meta("2026-10-07_143000-课堂笔记-a1b2c3"), tmp_path)
    runs.update(identifier, status="failed", exit_code=1)
    runs.mark_recovered(meta("2026-10-07_143000-课堂笔记-a1b2c3"))
    runs.mark_recovered(meta("2026-10-07_150000-课堂笔记-ffffff"))

    record = json.loads(runs.record_path(identifier).read_text())
    assert record["status"] == "recovered" and record["exit_code"] == 1 and record["course"] == "课程"
    assert not runs.record_path("2026-10-07_150000-课堂笔记-ffffff").exists()


@pytest.mark.parametrize("corrupt", [b"\\xff\\xfe", b"{", b'"text"', b"[]"])
def test_unreadable_record_is_unavailable_and_left_untouched(isolated_run_registry, tmp_path, corrupt):
    identifier = runs.begin(meta("2026-10-07_143000-课堂笔记-a1b2c3"), tmp_path)
    runs.record_path(identifier).write_bytes(corrupt)

    with pytest.raises(runs.Unavailable) as error:
        runs.mark_recovered(meta("2026-10-07_143000-课堂笔记-a1b2c3"))
    # Callers already treat every OSError from the registry as "registry unavailable".
    assert isinstance(error.value, OSError)
    with pytest.raises(runs.Unavailable):
        runs.update(identifier, status="done")
    assert runs.record_path(identifier).read_bytes() == corrupt
