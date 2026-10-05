"""Shared helpers for the skill 9 drawing rules (GIS-008, GIS-009, GIS-004…007, 010, 011, 016).

Reads the common ingestion JSON written by skill 1 (`*.dwg.json`, SPC-0006 §2.4, §4.1.4) — never a
DWG or DXF. Rule values come only from the rules file written by skill 8 `get_rules.py`.
No network, no shell calls."""
__version__ = "0.1.0"

import json
import os
from pathlib import Path

MAX_SAMPLES = 10  # SPC-0004 §5.7


class InputError(Exception):
    """Exit 2 (SPC-0006 §2.3.3)."""


def need_shapely():
    try:
        import shapely  # noqa: F401
        return None
    except ImportError:
        return "shapely is not installed"


def check_out_path(out):
    if len(str(Path(out).resolve())) > 250:
        raise InputError("output path longer than 250 characters")


def read_json(path):
    if len(str(Path(path).resolve())) > 250:
        raise InputError(f"path longer than 250 characters: {path}")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc


def load_ingestion(path):
    d = read_json(path)
    if not isinstance(d, dict) or "content" not in d or "source" not in d:
        raise InputError(f"{path} is not an ingestion file (no source/content)")
    if d["source"].get("file_type") != "dwg":
        raise InputError(f"{path} is not a DWG ingestion file")
    return d


def load_rules(path):
    rb = read_json(path)
    if "rules" not in rb:
        raise InputError(f"{path} is not a rules file from get_rules.py")
    return rb, {r["rule_id"]: r for r in rb["rules"]}


def param(rule, name, site=None):
    """Value of a rule parameter, or None when absent, null or tagged GAP.
    With `site`, the value is a per-site table ({"QRWN": …, "NRJS": …}); the site's entry is returned
    (None when the site is not listed)."""
    if rule is None:
        return None
    p = rule.get("parameters", {}).get(name)
    if not p or p.get("tag") == "GAP" or p.get("value") is None:
        return None
    v = p["value"]
    if site is not None:
        return v.get(site) if isinstance(v, dict) else None
    return v


def dwg_source(ing, review_root=None):
    s = ing["source"]
    return {"source_type": "dwg_vector", "path": s["path"], "sha256": s["sha256"],
            "document_no": s.get("document_no"), "file_type": "dwg"}


def write_result(out, res):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(res, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)


def model_points(content):
    """(kind, layer, handle, (x, y)) for every model-space vertex, text and block insert point."""
    for p in content.get("polylines", []):
        for v in p["vertices"]:
            yield "polyline", p["layer"], p["handle"], (v[0], v[1])
    for t in content.get("texts", []):
        if t.get("space") == "model":
            yield "text", t["layer"], t["handle"], tuple(t["insert"][:2])
    for b in content.get("blocks", []):
        if b.get("space") == "model":
            yield "insert", b["layer"], b["handle"], tuple(b["insert"][:2])


def polygon(rec):
    from shapely.geometry import Polygon
    return Polygon([tuple(v) for v in rec["vertices"]])


def layer_list(v):
    """A layer parameter is a list of names (or None)."""
    if v is None:
        return None
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def norm(s):
    return " ".join(str(s or "").split()).casefold()


def pattern(name, message, samples, count, source):
    return {"pattern": name, "message": message, "count": count, "samples": samples[:MAX_SAMPLES], "source": source}
