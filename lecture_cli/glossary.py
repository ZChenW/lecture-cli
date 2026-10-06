"""Course terminology: short ASR hints and a bilingual notes vocabulary."""
import json
from pathlib import Path


def validate_asr_context(text: str, limit: int = 1000) -> str:
    if len(text) > limit or "\x00" in text:
        raise ValueError(f"ASR 词表须不超过 {limit} 字符且不含 NUL；请缩减英文术语")
    return text


def load_glossary(course: Path, limit: int = 1000) -> dict:
    path = course / "glossary.json"
    if not path.exists():
        return {"asr_context": "", "glossary": []}
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict) or any(
            not isinstance(entry.get(key), str) or not entry[key].strip()
            for key in ("term", "translation"))
        or ("source" in entry and not isinstance(entry["source"], str))
        for entry in entries
    ):
        raise ValueError("glossary.json 须为 term、translation 和可选 source 组成的条目列表")
    terms = ", ".join(dict.fromkeys(entry["term"].strip() for entry in entries))
    return {"asr_context": validate_asr_context(terms, limit), "glossary": entries}


def notes_glossary(meta: dict) -> str:
    return "\n".join(
        f"- {entry['term']}：{entry['translation']}"
        + (f"（术语来源：{entry['source']}）" if entry.get("source") else "")
        for entry in meta.get("glossary", [])
    )


def terminology_warnings(meta: dict, body: str) -> list[str]:
    # A canonical translation elsewhere (for example in a heading) must not
    # conceal a different translation next to the English term in the prose.
    return [f"- 术语译名需检查：{entry['term']} → {entry['translation']}"
            for entry in meta.get("glossary", [])
            if any(entry['term'].casefold() in paragraph.casefold()
                   and entry['translation'] not in paragraph
                   for paragraph in body.split('\n\n'))]
