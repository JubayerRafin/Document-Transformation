"""Private, content-free wall-clock spans for diagnosing runtime overhead."""
from contextlib import contextmanager
import json
from pathlib import Path
import time


@contextmanager
def timed(folder, stage, **metadata):
    started = time.monotonic()
    status = 'completed'
    try:
        yield
    except BaseException:
        status = 'failed'
        raise
    finally:
        record = {'stage': stage, 'seconds': round(time.monotonic() - started, 6),
                  'status': status, **metadata}
        # Profiling must not turn a completed job into a processing failure.
        try:
            with (Path(folder) / 'runtime-timing.jsonl').open('a', encoding='utf-8') as file:
                file.write(json.dumps(record) + '\n')
        except OSError:
            pass
