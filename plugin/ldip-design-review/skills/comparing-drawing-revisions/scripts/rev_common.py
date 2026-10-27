"""Shared helpers for comparing-drawing-revisions (skill 7, SPC-0006 Rev B §4.4 + Rev M §4.15).

Number normalisation, revision ordering from PKG-010 method text, geometry hashing (not handle),
and I/O helpers. Rule values and rule state come only from a skill-8 get_rules.py file — never
hard-coded here. No network, no shell calls.
"""
__version__ = "0.1.0"

import hashlib
import json
import os
import re
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEP, EXIT_SCHEMA, EXIT_REFUSED = 0, 2, 3, 4, 5
MAX_PATH = 250  # SPC-0002 R1

# Revision-code shapes named in PKG-010 method text (series examples 0A<0B; 00<01; A0<A1).
REV_0LETTER = re.compile(r"^0[A-Z]$")
REV_DIGITS = re.compile(r"^\d{2}$")
REV_A_DIGIT = re.compile(r"^A\d$")
REV_ANY = re.compile(r"^(0[A-Z]|\d{2}|A\d)$")

# Single-letter fields that are not valid multi-character LDIP codes (S31 M1: e.g. -G-).
SINGLE_LETTER = re.compile(r"^[A-Z]$")


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


def load_rules(path):
    """Rules file written by applying-ldip-rulebook/get_rules.py."""
    rb = read_json(path)
    if "rules" not in rb:
        raise ValueError(f"{path} is not a rules file from get_rules.py")
    return rb, {r["rule_id"]: r for r in rb["rules"]}


def rule_pkg010(rules_by_id):
    return rules_by_id.get("PKG-010")


def is_revision_code(token):
    return bool(token and REV_ANY.match(str(token).strip().upper()))


def strip_spaces(number):
    return re.sub(r"\s+", "", str(number or "").strip()).upper()


def normalise_number(number):
    """Pairing key (a): strip spaces, stray single-letter fields (e.g. -G-), and revision suffix."""
    s = strip_spaces(number)
    if not s:
        return ""
    parts = [p for p in s.split("-") if p]
    if parts and is_revision_code(parts[-1]):
        parts = parts[:-1]
    parts = [p for p in parts if not SINGLE_LETTER.match(p)]
    return "-".join(parts)


def revision_from_number(number):
    """Last field when it looks like a revision code; else None."""
    parts = [p for p in strip_spaces(number).split("-") if p]
    if parts and is_revision_code(parts[-1]):
        return parts[-1].upper()
    return None


def revision_series_key(code, method_text=""):
    """Order key for a revision code using the series described in PKG-010 method text.

    Returns (series_id, rank) or None when the code is not in a recognised series.
    series_id keeps different series incomparable (advance only within one series).
    """
    c = str(code or "").strip().upper()
    # Prefer series that the method text names; fall back to the three documented shapes.
    method = (method_text or "").upper()
    if REV_0LETTER.match(c) and ("0A" in method or "0B" in method or not method):
        return ("0L", ord(c[1]))
    if REV_DIGITS.match(c) and ("00" in method or "01" in method or not method):
        return ("DD", int(c))
    if REV_A_DIGIT.match(c) and ("A0" in method or "A1" in method or not method):
        return ("A", int(c[1]))
    # Still accept the three shapes when method text is present but abbreviated.
    if REV_0LETTER.match(c):
        return ("0L", ord(c[1]))
    if REV_DIGITS.match(c):
        return ("DD", int(c))
    if REV_A_DIGIT.match(c):
        return ("A", int(c[1]))
    return None


def compare_revisions(old, new, method_text=""):
    """Return 'advanced' | 'same' | 'not_advanced' | 'incomparable' | 'missing'."""
    if not old or not new:
        return "missing"
    ko, kn = revision_series_key(old, method_text), revision_series_key(new, method_text)
    if ko is None or kn is None:
        return "incomparable"
    if ko[0] != kn[0]:
        return "incomparable"
    if kn[1] > ko[1]:
        return "advanced"
    if kn[1] == ko[1]:
        return "same"
    return "not_advanced"


def geometry_hash(kind, layer, points, extra=None):
    """Stable hash of geometry (kind + layer + rounded points). Never uses AutoCAD handle."""
    layer_short = (layer or "").rsplit("$", 1)[-1]
    pts = []
    for p in points or []:
        if len(p) >= 2:
            row = [round(float(p[0]), 3), round(float(p[1]), 3)]
            if len(p) >= 3 and p[2] is not None:
                row.append(round(float(p[2]), 3))
            pts.append(row)
    payload = {"kind": kind, "layer": layer_short, "points": pts, "extra": extra or {}}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def polyline_hashes(ingestion):
    """Map geometry_hash -> sample entity info from a skill-1 DWG ingestion file."""
    content = ingestion.get("content") or {}
    out = {}
    for p in content.get("polylines") or []:
        h = geometry_hash("polyline", p.get("layer"), p.get("vertices") or [],
                          {"closed": True, "elevation": p.get("elevation")})
        out.setdefault(h, []).append({"layer": p.get("layer"), "handle_ignored": True,
                                      "n_vertices": len(p.get("vertices") or [])})
    # Linework side-file records (open/closed) when embedded under content.linework.
    for rec in content.get("linework") or []:
        h = geometry_hash(rec.get("type") or "linework", rec.get("layer") or rec.get("layer_short"),
                          rec.get("points") or [], {"closed": rec.get("closed")})
        out.setdefault(h, []).append({"layer": rec.get("layer"), "handle_ignored": True,
                                      "n_vertices": len(rec.get("points") or [])})
    return out


def load_linework_side(path):
    """Optional *.linework.json beside an ingestion file."""
    d = read_json(path)
    recs = d.get("records") or d.get("linework") or []
    out = {}
    for rec in recs:
        h = geometry_hash(rec.get("type") or "linework", rec.get("layer") or rec.get("layer_short"),
                          rec.get("points") or [], {"closed": rec.get("closed")})
        out.setdefault(h, []).append({"layer": rec.get("layer"), "handle_ignored": True,
                                      "n_vertices": len(rec.get("points") or [])})
    return out


def sheet_key(sheet):
    """Normalise '01 OF 05' / '1 of 5' for proposed pairing."""
    s = re.sub(r"\s+", " ", str(sheet or "").strip().upper())
    m = re.match(r"^0*(\d+)\s*(?:OF|/)\s*0*(\d+)$", s)
    if m:
        return f"{int(m.group(1))} OF {int(m.group(2))}"
    return s


def title_key(title):
    return re.sub(r"\s+", " ", str(title or "").strip().upper())
