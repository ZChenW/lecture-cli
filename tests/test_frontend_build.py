"""The committed frontend build: in step with its sources and loadable under the strict CSP."""
import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "lecture_cli" / "gui" / "static"


def frontend_hash():
    spec = importlib.util.spec_from_file_location("frontend_hash", ROOT / "scripts" / "frontend_hash.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.inline = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        if self.tags and self.tags[-1][0] in ("script", "style") and data.strip():
            self.inline.append(data)


def test_committed_build_matches_the_frontend_sources():
    recorded = (STATIC / "build-hash.txt").read_text().strip()
    assert recorded == frontend_hash().source_hash(), "前端源码已改动：运行 scripts/build-frontend.sh 并提交产物"


def test_source_hash_ignores_node_modules_and_sees_every_source_change(tmp_path):
    module = frontend_hash()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("a")
    before = module.source_hash(tmp_path)
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "index.js").write_text("ignored")
    assert module.source_hash(tmp_path) == before
    (tmp_path / "src" / "main.ts").write_text("b")
    assert module.source_hash(tmp_path) != before


def test_index_html_has_no_inline_code_or_external_resources():
    parser = Tags()
    parser.feed((STATIC / "index.html").read_text())
    assert parser.inline == []
    scripts = [attrs for tag, attrs in parser.tags if tag == "script"]
    assert scripts and all(attrs.get("src", "").startswith("./assets/") for attrs in scripts)
    assert all("style" not in attrs and not any(name.startswith("on") for name in attrs) for _, attrs in parser.tags)
    assert not any(tag == "style" for tag, _ in parser.tags)
    for path in STATIC.rglob("*"):
        if path.suffix in (".html", ".css", ".js"):
            text = path.read_text()
            # Classrooms may be offline, and the CSP would refuse these anyway.
            assert not re.search(r"""url\(\s*['"]?(https?:|//|data:)""", text), path
            assert not re.search(r"""<link[^>]+href=["']https?:""", text), path


def test_fonts_ship_with_their_licences():
    fonts = STATIC / "fonts"
    for family in ("instrument-serif", "geist", "geist-mono", "source-serif-4", "dm-mono"):
        assert list(fonts.glob(f"{family}-latin-*.woff2")), family
        assert "SIL Open Font License" in (fonts / f"{family}-OFL.txt").read_text()
