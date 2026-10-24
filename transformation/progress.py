"""Read request telemetry without exposing document text or inventing a percentage."""
import json
import time
from pathlib import Path


def read_progress(run, processing=True):
    result = {"requests_completed": 0, "fragments_skipped": 0,
              "prompt_tokens": 0, "output_tokens": 0, "active_seconds": None}
    active = None
    events=[]
    try:
        lines = (Path(run) / "translation-progress.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # The worker may still be writing its final line.
        kind = event.get("event")
        events.append(event)
        if kind == "request_started":
            active = event["time"]
        elif kind == "request_completed":
            result["requests_completed"] += 1
            result["prompt_tokens"] += event.get("prompt_tokens", 0)
            result["output_tokens"] += event.get("output_tokens", 0)
            active = None
        elif kind == "skipped":
            result["fragments_skipped"] += 1
        elif kind == "translation_failed":
            active = None
    if active is not None and processing:
        result["active_seconds"] = round(max(0, time.time() - active), 1)
    from transformation.runtime_metrics import summarize
    result['performance']=summarize(events)
    return result
