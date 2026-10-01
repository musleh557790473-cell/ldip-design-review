"""Shared helpers for skill 1 extracting-dwg-data (SPC-0006 §2.3). No network, no shell calls."""
__version__ = "0.1.0"

import hashlib
import json
import os
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEPENDENCY, EXIT_SCHEMA, EXIT_REFUSED = 0, 2, 3, 4, 5
MAX_PATH = 250   # SPC-0002 R1


class DwgError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def check_path(path, must_exist=False):
    p = Path(path)
    if len(str(p.resolve())) > MAX_PATH:
        raise DwgError(EXIT_INPUT, f"path longer than {MAX_PATH} characters: {path}")
    if must_exist and not p.is_file():
        raise DwgError(EXIT_INPUT, f"file not found: {path}")
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def r3(x):
    """Coordinates to 0.001 m (SPC-0006 §2.3.6)."""
    return None if x is None else round(float(x), 3)


def r2(x):
    """Areas to 0.01 m2."""
    return None if x is None else round(float(x), 2)
