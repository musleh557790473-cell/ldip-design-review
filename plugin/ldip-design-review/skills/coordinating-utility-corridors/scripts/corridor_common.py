"""Shared helpers for coordinating-utility-corridors (skill 14, SPC-0006 Rev O §4.17).

I/O, exits, params, utility_layers. No rule IDs in findings (DO-4). No network, no shell.
"""
__version__ = "0.1.1"

import json
import os
import re
from pathlib import Path

EXIT_OK, EXIT_INPUT, EXIT_DEP, EXIT_SCHEMA = 0, 2, 3, 4
MAX_PATH = 250  # SPC-0002 R1

SKILL_ROOT = Path(__file__).resolve().parents[1]
PARAMS_PATH = SKILL_ROOT / "references" / "params.json"
LAYERS_PATH = SKILL_ROOT / "references" / "utility_layers.json"
SCHEMA_PATH = SKILL_ROOT / "templates" / "corridors.schema.json"


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
    doc = read_json(path or PARAMS_PATH)
    tol = doc.get("tolerances") or {}
    band = doc.get("band") or {}
    er_pos = doc.get("c2_er_positions_m") or {}
    c2_tol = doc.get("c2_tolerance_m")
    if c2_tol is None:
        c2_tol = tol.get("c2_tolerance_m", 0.5)
    return {
        "required": list(doc.get("required_utilities") or []),
        "wet": set(doc.get("wet") or []),
        "dry": set(doc.get("dry") or []),
        "power": set(doc.get("power") or []),
        "c5_extra_m": float(tol.get("c5_extra_m", 0.5)),
        "c4_min_m": float(tol.get("c4_power_horizontal_min_m", 1.0)),
        "coord_abs_m": float(tol.get("coord_abs_m", 0.05)),
        "c2_er_positions_m": {str(k): float(v) for k, v in er_pos.items()},
        "c2_tolerance_m": float(c2_tol),
        "curb": doc.get("curb") or {},
        "section_label": doc.get("section_label") or {},
        "band": band,
        "raw": doc,
    }


def load_utility_layers(path=None, site=None):
    doc = read_json(path or LAYERS_PATH)
    site_key = (site or doc.get("default_site") or "NMAR").upper()
    sites = doc.get("sites") or {}
    site_doc = sites.get(site_key) or sites.get(doc.get("default_site")) or {}
    shared = doc.get("shared_layers") or {}
    raw_layers = site_doc.get("layers")
    if raw_layers == "shared" or raw_layers is None:
        layer_src = shared
    elif isinstance(raw_layers, dict) and raw_layers:
        layer_src = raw_layers
    else:
        layer_src = shared
    layers = {}
    for name, meta in layer_src.items():
        layers[name.casefold()] = dict(meta)
        layers[name] = dict(meta)
    return {
        "site": site_key,
        "layers": layers,
        "legend_colours": site_doc.get("legend_colours") or {},
        "raw": doc,
    }


def layer_short(name):
    """Layer name after the last '$' (bound-xref style), else the name itself."""
    if not name:
        return ""
    return name.rsplit("$", 1)[-1].strip()


def resolve_utility(layer_name, layers_map):
    """Return utility meta dict or None for a DWG / PDF layer or legend label."""
    if not layer_name:
        return None
    layers = layers_map.get("layers") or {}
    candidates = [layer_name, layer_short(layer_name), layer_name.strip()]
    for cand in candidates:
        if cand in layers:
            return dict(layers[cand])
        cf = cand.casefold()
        if cf in layers:
            return dict(layers[cf])
        for key, meta in layers.items():
            if isinstance(key, str) and key.casefold() == cf:
                return dict(meta)
            if isinstance(key, str) and layer_short(key).casefold() == cf:
                return dict(meta)
    return None


def band_from_insert(x, sx):
    """Band = [x − sx, x]; centre = x − sx/2 (Rev O §4.17.2)."""
    width = abs(float(sx))
    x = float(x)
    if sx >= 0:
        x_max, x_min = x, x - width
    else:
        x_min, x_max = x, x + width
    if x_min > x_max:
        x_min, x_max = x_max, x_min
    centre = (x_min + x_max) / 2.0
    return x_min, x_max, centre, width


def offset_to_curb(centre, curb_left, curb_right, cl_x=0.0):
    """Signed offset: verge + / carriageway −, to the nearest outer curb face.

    Right of CL uses curb_right; left of CL uses curb_left. When the band centre
    sits outside the curb (verge) the offset is positive; inside the carriageway
    it is negative. Returns (None, None) when curb is missing (R2).
    """
    if centre is None or curb_left is None or curb_right is None:
        return None, None
    c = float(centre)
    cl = float(cl_x or 0.0)
    left, right = float(curb_left), float(curb_right)
    if left > right:
        left, right = right, left
    if c >= cl:
        curb = right
        offset = c - curb  # + verge (outside), − carriageway (inside)
        side = "right"
    else:
        curb = left
        offset = curb - c  # + verge (outside to the left), − carriageway
        side = "left"
    return round(offset, 6), side


def side_of_cl(centre, cl_x=0.0):
    if centre is None:
        return None
    if abs(float(centre) - float(cl_x or 0.0)) < 1e-9:
        return "on_cl"
    return "right" if float(centre) > float(cl_x or 0.0) else "left"


def main_guard(fn):
    """Call fn(); on ValueError → exit 2; on ImportError → exit 3."""
    try:
        return fn()
    except ImportError as exc:
        return fail(EXIT_DEP, f"missing package: {exc}")
    except ValueError as exc:
        return fail(EXIT_INPUT, str(exc))
    except OSError as exc:
        return fail(EXIT_INPUT, str(exc))


# R3: labels must match ^[AB]\d+(-\d+)?$ only
_SECTION_LABEL_RE = re.compile(r"^([AB]\d+(?:-\d+)?)$", re.I)
_SECTION_LABEL_SEARCH_RE = re.compile(r"\b([AB]\d+(?:-\d+)?)\b", re.I)


def parse_section_label(text, strict=True):
    """Return section label or None. strict=True requires whole-string match (R3)."""
    if not text:
        return None
    s = str(text).strip()
    # Strip common mtext wrappers
    s_plain = re.sub(r"[{}\\].*$", "", s).strip() or s
    if strict:
        m = _SECTION_LABEL_RE.match(s_plain)
        if m:
            return m.group(1).upper()
        # Allow "SECTION A1" style: search then verify exact token
        m2 = _SECTION_LABEL_SEARCH_RE.search(s_plain)
        if m2 and _SECTION_LABEL_RE.match(m2.group(1)):
            # Only accept if the non-label remainder is filler words
            rest = _SECTION_LABEL_SEARCH_RE.sub("", s_plain).strip(" :-_")
            if rest.upper() in ("", "SECTION", "SEC", "TYPICAL", "TYP"):
                return m2.group(1).upper()
        return None
    m = _SECTION_LABEL_SEARCH_RE.search(s_plain)
    return m.group(1).upper() if m else None


def iter_section_bands(sec):
    """Yield band dicts for a section (prefers bands[]; falls back to utilities{})."""
    bands = sec.get("bands")
    if isinstance(bands, list) and bands:
        return list(bands)
    out = []
    for util, rec in (sec.get("utilities") or {}).items():
        row = dict(rec)
        row.setdefault("utility", util)
        row.setdefault("index", 0)
        row.setdefault("exclude_c3_c4", False)
        out.append(row)
    return out


def c1_presence(bands):
    """Set of utilities counted present for C1 (aliases + tree c1_as)."""
    present = set()
    for b in bands:
        for alias in b.get("c1_as") or []:
            present.add(alias)
        u = b.get("utility")
        if not u or u == "tree":
            continue
        present.add(u)
        for alias in b.get("c1_as") or []:
            present.add(alias)
    return present
