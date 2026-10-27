"""Shared helpers for checking-design-document-consistency (skill 16, SPC-0006 Rev N §4.16).

I/O, exits, params, quantity comparison (DN-5 / DN-6). No rule IDs (DN-4). No network, no shell.
"""
__version__ = "0.1.0"

import json
import os
import re
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEP, EXIT_SCHEMA = 0, 2, 3, 4
MAX_PATH = 250  # SPC-0002 R1

SKILL_ROOT = Path(__file__).resolve().parents[1]
PARAMS_PATH = SKILL_ROOT / "references" / "params.json"

# Excel formula / cell error tokens
CELL_ERRORS = ("#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#N/A", "#NULL!", "#NUM!")
BASE_FACTOR_RE = re.compile(
    r"^=\s*(-?\d+(?:\.\d+)?)\s*\*\s*(-?\d+(?:\.\d+)?)\s*$"
)
RATIO_RE = re.compile(r"^=\s*[A-Z]{1,3}\d+\s*\*\s*(0?\.\d+|1(?:\.0+)?)\s*$", re.I)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def fail(code, detail, **extra):
    emit({"status": "error", "code": code, "detail": detail, **extra})
    return code


def path_ok(p):
    return len(str(Path(p).resolve())) <= MAX_PATH


def atomic_write_json(path, obj):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)


def read_json(path):
    if not path_ok(path):
        raise ValueError(f"path longer than {MAX_PATH} characters: {path}")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def load_params(path=None):
    """DN-5 starting tolerances from references/params.json (not rulebook values)."""
    p = Path(path) if path else PARAMS_PATH
    doc = read_json(p)
    tol = doc.get("tolerances") or {}
    return {
        "qty_pct": float(tol.get("quantity_pct", 0.10)),
        "qty_abs_m3": float(tol.get("quantity_abs_m3", 1000.0)),
        "c4_abs_m": float(tol.get("c4_pavement_abs_m", 0.05)),
        "c6_finding_from_pct": int((doc.get("stage") or {}).get("c6_finding_from_pct", 60)),
        "raw": doc,
    }


def stage_pct(stage):
    """Parse owner stage string → milestone percent (30 / 60 / …). Never guess beyond digits."""
    s = str(stage or "").strip()
    m = re.search(r"(\d{2,3})\s*%", s)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{2,3})\b", s)
    if m:
        return int(m.group(1))
    low = s.casefold()
    if "concept" in low or "pd" in low.split():
        return 30
    if "detailed" in low or "dd" in low.split():
        return 60
    return None


def pct_diff(a, b):
    if a is None or b is None:
        return None
    a, b = float(a), float(b)
    denom = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / denom


def exceeds_quantity_tol(a, b, unit, params):
    """DN-5: m³ → >10 % AND >1,000 m³; m² / m → >10 % only."""
    if a is None or b is None:
        return False, None, None
    diff = abs(float(a) - float(b))
    pd = pct_diff(a, b)
    u = (unit or "").strip().lower().replace("³", "3").replace("²", "2")
    if u in ("m3", "m³", "cu.m", "cu.m.", "cum"):
        flag = pd is not None and pd > params["qty_pct"] and diff > params["qty_abs_m3"]
    else:
        flag = pd is not None and pd > params["qty_pct"]
    return flag, diff, pd


def parse_base_factor(formula):
    """Return (base, factor) from `=base*factor`, else None (DN-6)."""
    if not formula or not isinstance(formula, str):
        return None
    m = BASE_FACTOR_RE.match(formula.strip().replace(",", ""))
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def is_cell_error(value):
    if value is None:
        return False
    s = str(value).strip().upper()
    return s in {e.upper() for e in CELL_ERRORS} or s.startswith("#")


def main_guard(fn):
    try:
        return fn()
    except ValueError as exc:
        return fail(EXIT_INPUT, str(exc))
    except FileNotFoundError as exc:
        return fail(EXIT_INPUT, str(exc))
