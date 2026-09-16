"""Temporary test directories with inherited Windows permissions."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile
import uuid


@contextmanager
def temporary_workspace():
    base = Path(os.environ.get("LAB_TEST_TMP", tempfile.gettempdir())).resolve()
    root = base / ("lab-test-" + uuid.uuid4().hex)
    root.mkdir(mode=0o755)
    try:
        yield root
    finally:
        if root.resolve().parent != base or not root.name.startswith("lab-test-"):
            raise RuntimeError("Refusing cleanup outside test workspace")
        shutil.rmtree(root)
