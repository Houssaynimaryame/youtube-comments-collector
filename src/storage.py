"""Stockage : données brutes, préparées, analytiques et métadonnées."""
import json
from datetime import datetime, timezone
from pathlib import Path

SUBDIRS = ["data/raw", "data/processed", "data/analytical", "data/metadata", "logs"]


def ensure_dirs(base_dir):
    for sub in SUBDIRS:
        (Path(base_dir) / sub).mkdir(parents=True, exist_ok=True)


def timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def save_json(obj, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    return path


def save_csv(df, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path
