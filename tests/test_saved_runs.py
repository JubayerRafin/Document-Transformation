import json
from pathlib import Path
from temp_workspace import temporary_workspace
import unittest
from extraction.saved_runs import discover_saved_runs


class SavedRunsTests(unittest.TestCase):
    def test_discovers_nested_experiments_and_skips_incomplete_runs(self):
        with temporary_workspace() as directory:
            root = Path(directory)
            record = dict(status="completed", source_name="sample.pdf", source_sha256="a" * 64,
                          backend="docling", seconds=1, regions={}, warnings=0)
            for relative in ("runs/local", "experiments/docling-fixed/run", "experiments/failed", "experiments/incomplete", "experiments/broken"):
                folder = root / relative
                folder.mkdir(parents=True)
                data = dict(record, status="failed") if relative.endswith("failed") else record
                (folder / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
                if not relative.endswith("incomplete"):
                    (folder / "document.json").write_text("{}", encoding="utf-8")
            (root / "experiments/broken/manifest.json").write_text("{", encoding="utf-8")
            actual = {p.relative_to(root).as_posix() for p, _ in discover_saved_runs(root)}
            self.assertEqual(actual, {"runs/local", "experiments/docling-fixed/run"})

    def test_missing_folders_are_empty(self):
        with temporary_workspace() as directory:
            self.assertEqual(discover_saved_runs(directory), [])
