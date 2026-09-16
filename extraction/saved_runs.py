"""Find completed visual-review runs from local and shared experiment folders."""
import json
from pathlib import Path


def discover_saved_runs(root):
    root = Path(root)
    saved = []
    for folder in (root / "runs", root / "experiments"):
        for manifest in folder.rglob("manifest.json"):
            try:
                record = json.loads(manifest.read_text(encoding="utf-8-sig"))
                if not isinstance(record, dict) or record.get("status") != "completed":
                    continue
                if not (manifest.parent / "document.json").is_file():
                    continue
                if not all(key in record for key in ("source_name", "source_sha256", "backend", "seconds", "regions", "warnings")):
                    continue
                saved.append((manifest.parent, record, manifest.stat().st_mtime))
            except (OSError, ValueError):
                continue
    saved.sort(key=lambda item: (-item[2], item[0].as_posix()))
    return [(folder, record) for folder, record, _ in saved]
