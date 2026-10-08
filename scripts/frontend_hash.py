"""Hash of the frontend sources, written into the committed build so a test notices a stale build."""
import hashlib
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def source_files(root: Path = FRONTEND) -> list[Path]:
    return sorted((path for path in root.rglob("*")
                   if path.is_file() and "node_modules" not in path.relative_to(root).parts),
                  key=lambda path: path.relative_to(root).as_posix())


def source_hash(root: Path = FRONTEND) -> str:
    digest = hashlib.sha256()
    for path in source_files(root):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    print(source_hash())
