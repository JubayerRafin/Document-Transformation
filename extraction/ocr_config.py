"""Verify the team's exact OCR weights before initializing an OCR engine."""
import hashlib
import json
from pathlib import Path

DEFAULT_LOCK = Path(__file__).resolve().parents[1] / "config" / "ocr-models.json"
MODEL_KEYS = {"det_model_path", "rec_model_path", "cls_model_path"}


def resolve_ocr_models(model_dir, lock_path=DEFAULT_LOCK):
    raw = Path(lock_path).read_bytes()
    config = json.loads(raw)
    if config.get("engine") != "rapidocr" or config.get("runtime") != "onnxruntime":
        raise ValueError("This adapter requires rapidocr with onnxruntime.")
    if set(config.get("models", {})) != MODEL_KEYS:
        raise ValueError("OCR configuration must specify detection, recognition and classification models.")
    paths, verified = {}, {}
    for key, entry in config["models"].items():
        filename = entry["filename"]
        if not filename or "/" in filename or "\\" in filename or ":" in filename or filename in {".", ".."}:
            raise ValueError("OCR filenames must be plain filenames inside the model folder.")
        expected = entry["sha256"].lower()
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise ValueError(f"Invalid SHA-256 in OCR configuration: {filename}")
        path = Path(model_dir) / filename
        if not path.is_file():
            raise FileNotFoundError(f"Pinned OCR model missing: {filename}. Supply the exact model set in config/ocr-models.json via ocr_model_dir.")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"OCR model checksum mismatch: {filename}. This file differs from the team's pinned weights; extraction stopped.")
        paths[key] = str(path.resolve())
        verified[key] = {"filename": filename, "sha256": actual}
    record = {"profile": config["profile"], "engine": config["engine"], "runtime": config["runtime"],
              "config_sha256": hashlib.sha256(raw).hexdigest(), "models": verified}
    return paths, record
