"""Temporary journal with durable notes, transcript and review attachments."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

DETAIL_WARNING = "详细笔记未全部完成；已保留完成章节及剩余原始转录。"
CITATION = re.compile(r"\[L(\d+)(?:[–—-]L?(\d+))?(?: [0-9:.]+[–—-][0-9:.]+)?\](?!\()")
REVIEW_MARKER = "<!-- REVIEW -->"
# Attachments live beside the main note in a visible folder; Obsidian hides dot folders.
ATTACHMENT_DIR = "原文与记录"
ATTACHMENTS = ("transcript", "live", "review")


def attachment_path(output: Path, kind: str) -> Path:
    return output.parent / ATTACHMENT_DIR / f"{output.stem}.{kind}.md"


def legacy_attachment_path(output: Path, kind: str) -> Path:
    return output.with_suffix(f".{kind}.md")


def review_state_path(review: Path) -> Path:
    """Which review points the reader ticked: kept beside the .review.md it describes, never inside it."""
    return review.with_name(review.name.removesuffix(".review.md") + ".review-state.json")


def linked_sources(text: str, filename: str, version: str) -> str:
    return CITATION.sub(
        lambda m: f"[{version}-{m[0][1:-1]}]({quote(filename)}#{version}-L{m[1]})", text)


def owned_review(text: str, first: int, last: int) -> str:
    """A topic owns its source interval; neighboring topics own their own issues."""
    paragraphs = []
    for paragraph in text.split('\n\n'):
        citations = list(CITATION.finditer(paragraph))
        if not citations or any(int(m[1]) <= last and int(m[2] or m[1]) >= first for m in citations):
            paragraphs.append(paragraph)
    return '\n\n'.join(paragraphs).strip()


def atomic_text(path: Path, text: str) -> None:
    # Destination-side atomic replacement also works when /tmp is another filesystem.
    import tempfile
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path: Path, value: dict) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False))


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def has_content(text: str) -> bool:
    """Plan GUI-4 §1: text with at least one letter, digit or CJK character. A segment of only
    punctuation ("." "。" "…") is not something the lecturer said and never counts as text."""
    return any(ch.isalnum() for ch in text)


# Plan GUI-4 Q1.2: a recording that recognised nothing. The main note, the terminal and the GUI's
# end page say so instead of pretending notes were written; gui/library.py looks for EMPTY_TITLE.
EMPTY_TITLE = "这次录制没有识别出任何内容"
EMPTY_CAUSES = ("麦克风没有收到声音", "选错了输入设备", "麦克风被静音")
EMPTY_HINT = "可以运行 lecture doctor --mic-test 检查麦克风。"
TEXT_MARKER = re.compile(r"^\[[^\]]*\]\s*")


def no_content(directory: Path) -> bool:
    """True when neither the live transcript nor a complete refinement holds any content text; a
    near-silent placeholder or a 待核对 prefix alone is not content."""
    records = events(directory) + (refined_events(directory) or [])
    return not any(has_content(TEXT_MARKER.sub("", r.get("text", ""))) for r in records)


def events(directory: Path) -> list[dict]:
    try:
        lines = (directory / "transcript.jsonl").read_text().splitlines(keepends=True)
    except FileNotFoundError:
        return []
    # The producer may be in the middle of writing a record.
    return [json.loads(line) for line in lines if line.endswith("\n")]


def source_text(records: list[dict]) -> str:
    return "\n".join(f"[L{r['id']} {r['start']}–{r['end']}] {r['text']}" for r in records)


def refined_events(directory: Path) -> list[dict] | None:
    state = read_json(directory / "refinement-state.json")
    if state.get("complete"):
        try:
            records = [json.loads(line) for line in (directory / "refined.jsonl").read_text().splitlines()]
            if records and len(records) == state["count"] and all(
                r["id"] == i and all(isinstance(r[k], str) and r[k].strip() for k in ("start", "end", "text"))
                for i, r in enumerate(records, 1)
            ):
                return records
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return None


def final_events(directory: Path) -> list[dict]:
    corrected = refined_events(directory)
    return corrected if corrected is not None else events(directory)


def normalize_markdown(text: str) -> str:
    text = text.strip()
    if text.startswith("```markdown\n") and text.endswith("```"):
        text = text[12:-3].strip()
    return text.replace(r"\[", "$$").replace(r"\]", "$$").replace(r"\(", "$").replace(r"\)", "$")


def nested_headings(text: str, level: int) -> str:
    return re.sub(r"(?m)^#{1,6}\s+", "#" * level + " ", text)


class Journal:
    def __init__(self, directory: Path):
        self.directory = directory
        self.meta = read_json(directory / "session.json")
        self.db = sqlite3.connect(directory / "notes.sqlite", timeout=10)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS batches (first_id INTEGER PRIMARY KEY, last_id INTEGER, body TEXT, fallback INTEGER)")
        self.db.execute("CREATE TABLE IF NOT EXISTS info (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS details (first_id INTEGER PRIMARY KEY, last_id INTEGER, body TEXT, fallback INTEGER)")
        self.db.commit()

    @property
    def cursor(self) -> int:
        return self.db.execute("SELECT COALESCE(MAX(last_id), 0) FROM batches").fetchone()[0]

    def bodies(self) -> str:
        return "\n\n".join(row[0] for row in self.db.execute("SELECT body FROM batches ORDER BY first_id"))

    def save(self, records: list[dict], body: str, fallback: bool = False) -> None:
        if not records or records[0]["id"] != self.cursor + 1:
            raise ValueError("转录批次不连续，已停止推进进度")
        heading = f"### {records[0]['start']}–{records[-1]['end']} · L{records[0]['id']}–L{records[-1]['id']}\n\n"
        body = heading + nested_headings(normalize_markdown(body), 4)
        with self.db:
            self.db.execute("INSERT INTO batches VALUES (?, ?, ?, ?)",
                            (records[0]["id"], records[-1]["id"], body, int(fallback)))
        self.render()

    def set_info(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO info VALUES (?, ?)", (key, value))

    def add_warning(self, text: str) -> None:
        old = dict(self.db.execute("SELECT key, value FROM info")).get("warning", "")
        if text not in old:
            self.set_info("warning", (old + " " + text).strip())

    def remove_warning(self, text: str) -> None:
        old = dict(self.db.execute("SELECT key, value FROM info")).get("warning", "")
        self.set_info("warning", old.replace(text, "").strip())

    @property
    def detail_cursor(self) -> int:
        return self.db.execute("SELECT COALESCE(MAX(last_id), 0) FROM details").fetchone()[0]

    def save_detail(self, records, body, fallback=False):
        if not records or records[0]["id"] != self.detail_cursor + 1:
            raise ValueError("详细笔记章节不连续")
        body, _, review = body.partition(REVIEW_MARKER)
        review = owned_review(review, records[0]['id'], records[-1]['id'])
        with self.db:
            self.db.execute("INSERT INTO details VALUES (?, ?, ?, ?)",
                            (records[0]["id"], records[-1]["id"], normalize_markdown(body), int(fallback)))
            self.db.execute("INSERT OR REPLACE INTO info VALUES (?, ?)",
                            (f"review:{records[0]['id']}", review.strip()))
        self.render()

    def preserve_detail_tail(self):
        info = dict(self.db.execute("SELECT key, value FROM info"))
        if info.get("detail_status") == "complete":
            return
        pending = [r for r in final_events(self.directory) if r["id"] > self.detail_cursor]
        if pending:
            self.save_detail(pending, "> " + source_text(pending).replace("\n", "\n> "), fallback=True)
        if self.detail_cursor:
            self.set_info("detail_status", "incomplete")
            self.add_warning(DETAIL_WARNING)

    def fallback(self) -> None:
        pending = [r for r in events(self.directory) if r["id"] > self.cursor]
        if pending:
            raw = source_text(pending)
            body = "### 待整理：未完成 API 整理的转录\n\n" + "\n".join("> " + line for line in raw.splitlines())
            self.save(pending, body, fallback=True)

    def render(self, finished: bool = False) -> None:
        info = dict(self.db.execute("SELECT key, value FROM info"))
        if finished:
            self.set_info("finished", "yes")
            info["finished"] = "yes"
        fallback = self.db.execute("SELECT COUNT(*) FROM batches WHERE fallback=1").fetchone()[0]
        state = "已结束" if info.get("finished") else "记录中"
        if fallback:
            state += " · 含待整理原文"
        output = Path(self.meta["output"])
        transcript_path = attachment_path(output, "transcript")
        live_path = attachment_path(output, "live")
        review_path = attachment_path(output, "review")
        transcript_path.parent.mkdir(exist_ok=True)
        # Links from the main note go through the attachment folder; attachments link to siblings.
        main_transcript = f"{ATTACHMENT_DIR}/{transcript_path.name}"
        live_records = events(self.directory)
        refined = refined_events(self.directory)
        version = "refined" if refined is not None else "live"
        transcript = f"# {self.meta['course']} · 原始转录\n\n"
        transcript += f"> 正文来源版本：{version}。原始识别结果未经中文生成改写；时间不含暂停。\n"
        for name, records in (("live", live_records), ("refined", refined)):
            if records is None:
                continue
            transcript += f"\n## {name}\n"
            for record in records:
                transcript += (f'\n<a id="{name}-L{record["id"]}"></a>\n\n'
                               f"### {name}-L{record['id']} · {record['start']}–{record['end']}\n\n"
                               f"{record['text']}\n")
        # Attachments must be durable before the main document advertises them.
        atomic_text(transcript_path, transcript)
        live = self.bodies()
        if live:
            atomic_text(live_path, "# 随堂记录（临时整理，可能与课后正文有差异）\n\n"
                        + linked_sources(live, transcript_path.name, "live") + "\n")
        review = []
        if info.get("warning"):
            review.append("## 处理提示\n\n" + info["warning"])
        refinement = read_json(self.directory / "refinement-state.json")
        if self.meta.get("refine") and refined is None and refinement.get("reason"):
            review.append(f"## 离线校正\n\n阶段：{refinement.get('stage', '离线进程')}\n\n"
                          f"原因：{refinement['reason']}")
        for key, value in info.items():
            if key.startswith("review:") and value:
                review.append(linked_sources(value, transcript_path.name, version))
        for record in (refined if refined is not None else live_records):
            if "待核对" in record["text"]:
                review.append(linked_sources(f"- 未确认的转录 [L{record['id']}]", transcript_path.name, version))
        for line in live.splitlines():
            if "待核对" in line:
                review.append(linked_sources(line, transcript_path.name, "live"))
        body = f"# {self.meta['course']} · {self.meta['started']}\n\n"
        if self.meta.get("demo"):
            body += "> 演示：自造课堂文字，未录音，不是真实课程记录。\n\n"
        body += f"> {state}。自动课堂笔记；公式和听辨疑点需对照课件核实。\n"
        body += "> 时间为录入音频的相对时间（不含暂停），L 为本次转录片段编号。\n"
        if info.get("empty"):
            body += (f"\n> **{EMPTY_TITLE}。**\n> 可能的原因：{'、'.join(EMPTY_CAUSES)}。\n"
                     f"> {EMPTY_HINT.replace('lecture doctor --mic-test', '`lecture doctor --mic-test`')}\n")
        body += f"\n[原始转录]({quote(main_transcript)})"
        if live:
            body += f" · [随堂记录]({quote(f'{ATTACHMENT_DIR}/{live_path.name}')})"
        body += "\n"
        if refined is not None:
            source = (f"云端离线重转录（{self.meta.get('refine_api_model') or 'whisper-large-v3'}）"
                      if self.meta.get("refine_backend") == "api" else "Qwen 离线重转录")
            body += f"> 详细笔记依据{source}；live/refined 编号独立，时间戳为音频段范围。\n"
        if info.get("warning"):
            body += f"\n> 记录提示：{info['warning']}\n"
        if info.get("summary"):
            body += "\n## 课后梳理\n\n" + nested_headings(info["summary"], 3) + "\n"
        if info.get("outline"):
            body += "\n## 学习路线\n\n"
            for topic in json.loads(info["outline"]):
                body += linked_sources(
                    f"- **{topic['title']}**：{topic['question']} [L{topic['first']}–L{topic['last']}]\n",
                    main_transcript, version)
        details = self.db.execute("SELECT first_id, last_id, body, fallback FROM details ORDER BY first_id").fetchall()
        if details:
            label = "详细课堂笔记" if info.get("detail_status") == "complete" else "详细课堂笔记（未全部完成）"
            body += f"\n## {label}\n"
            for index, (first, last, chapter, raw) in enumerate(details, 1):
                if raw:
                    review.append(linked_sources(f"## 待整理原文 · [L{first}–L{last}]\n\n"
                                                 + chapter, transcript_path.name, version))
                    continue
                # Shift headings uniformly, retaining their relative hierarchy.
                chapter = re.sub(r"(?m)^(#{1,6})\s+", lambda m: '#' * min(6, len(m[1]) + 1) + ' ', chapter)
                body += "\n" + linked_sources(chapter, main_transcript, version) + "\n"
        elif not info.get("finished"):
            body += "\n## 随堂预览\n\n" + linked_sources(live or "尚无已整理内容。", main_transcript, "live") + "\n"
        if review:
            atomic_text(review_path, "# 待核对与处理记录\n\n" + "\n\n".join(review) + "\n")
            body += f"\n[待核对与处理记录]({quote(f'{ATTACHMENT_DIR}/{review_path.name}')})\n"
        elif review_path.exists():
            review_path.unlink()
        atomic_text(output, body)

    def close(self) -> None:
        self.db.close()
