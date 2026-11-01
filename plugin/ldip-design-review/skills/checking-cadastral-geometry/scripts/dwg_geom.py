"""Shared helpers for the skill 9 drawing / coordinate rules (GIS-008, 009, 004…007, 010, 011, 016, 017).

Reads ingestion JSON written by skill 1 (`*.dwg.json`), skill 3 (LandXML) or skill 13 IFC surface
side files — never a DWG/DXF/IFC binary. Rule values come only from the rules file written by
skill 8 `get_rules.py`. No network, no shell calls."""
__version__ = "0.2.0"

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
    """DWG ingestion only (GIS-009 / match_plots)."""
    d = read_json(path)
    if not isinstance(d, dict) or "content" not in d or "source" not in d:
        raise InputError(f"{path} is not an ingestion file (no source/content)")
    if d["source"].get("file_type") != "dwg":
        raise InputError(f"{path} is not a DWG ingestion file")
    return d


def load_model_file(path):
    """DWG / LandXML ingestion, or IFC / LandXML surface side file (GIS-008 C3–C5)."""
    d = read_json(path)
    if not isinstance(d, dict):
        raise InputError(f"{path} is not a JSON object")
    if d.get("source") and d.get("content") is not None:
        ft = d["source"].get("file_type")
        if ft not in ("dwg", "landxml"):
            raise InputError(f"{path} file_type {ft!r} is not supported for coordinate checks")
        return d
    # Surface side file (LandXML surface template or IFC surface with points / extent)
    if d.get("template") in ("landxml-surface-1.0",) or "points" in d or "extent" in d or "extents" in d:
        return {"_kind": "surface_side", "path": str(path), "doc": d}
    raise InputError(f"{path} is not a DWG/LandXML ingestion or surface side file")


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


def model_source(ing_or_side):
    """Source dict for findings (dwg_vector / landxml / script_output)."""
    if ing_or_side.get("_kind") == "surface_side":
        doc = ing_or_side["doc"]
        p = ing_or_side["path"]
        return {"source_type": "landxml" if doc.get("template") == "landxml-surface-1.0" else "script_output",
                "path": doc.get("source_file") or p, "sha256": doc.get("tin_sha256") or ("0" * 64),
                "document_no": doc.get("document_no") or Path(p).stem, "file_type": "surface"}
    s = ing_or_side["source"]
    ft = s.get("file_type")
    st = "dwg_vector" if ft == "dwg" else ("landxml" if ft == "landxml" else "script_output")
    return {"source_type": st, "path": s["path"], "sha256": s["sha256"],
            "document_no": s.get("document_no"), "file_type": ft}


def write_result(out, res):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(res, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)


def model_points(content):
    """(kind, layer, handle, (x, y), via_block) for every model-space vertex, text and insert point.

    Expanded block entities (via_block set) are included; paper-space texts/inserts are skipped.
    """
    for p in content.get("polylines", []):
        chain = p.get("via_block") or []
        for v in p["vertices"]:
            yield "polyline", p["layer"], p["handle"], (v[0], v[1]), chain
    for t in content.get("texts", []):
        if t.get("space") not in (None, "model"):
            continue
        if t.get("space") is None and not t.get("via_block"):
            # legacy / synthetic: treat as model unless explicitly paper
            pass
        elif t.get("space") == "paper":
            continue
        chain = t.get("via_block") or []
        yield "text", t["layer"], t["handle"], tuple(t["insert"][:2]), chain
    for b in content.get("blocks", []):
        if b.get("space") == "model":
            chain = b.get("via_block") or []
            yield "insert", b["layer"], b["handle"], tuple(b["insert"][:2]), chain


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


def is_context_layer(layer, via_block, context_names):
    """True when layer or any via_block / xref name equals or starts with a context entry (casefold)."""
    if not context_names:
        return False
    keys = [norm(x) for x in context_names if x]
    candidates = [norm(layer)] + [norm(x) for x in (via_block or [])]
    for c in candidates:
        if not c:
            continue
        for k in keys:
            if c == k or c.startswith(k):
                return True
    return False


def envelope_geom(env, buffer):
    from shapely.geometry import Polygon, box
    if isinstance(env, dict) and env.get("bbox") and len(env["bbox"]) == 4:
        g = box(*map(float, env["bbox"]))
    elif isinstance(env, dict) and env.get("polygon") and len(env["polygon"]) >= 3:
        g = Polygon([tuple(map(float, p)) for p in env["polygon"]])
    elif isinstance(env, list) and len(env) >= 3 and isinstance(env[0], (list, tuple)):
        g = Polygon([tuple(map(float, p)) for p in env])
    else:
        raise InputError("parameter site_envelope must hold 'bbox' [E0, N0, E1, N1] or 'polygon' [[E, N], ...]")
    if not g.is_valid or g.area <= 0:
        raise InputError("parameter site_envelope is not a valid area")
    return g.buffer(float(buffer), join_style=2) if buffer else g


def boundary_geom(bnd):
    """site_boundary as polygon vertices list or {polygon: ...}."""
    from shapely.geometry import Polygon
    if bnd is None:
        return None
    if isinstance(bnd, dict) and bnd.get("polygon"):
        verts = bnd["polygon"]
    elif isinstance(bnd, list):
        verts = bnd
    else:
        raise InputError("site_boundary must be [[E, N], ...] or {polygon: [[E, N], ...]}")
    g = Polygon([tuple(map(float, p)) for p in verts])
    if not g.is_valid or g.area <= 0:
        raise InputError("site_boundary is not a valid area")
    return g


def displacement_vectors(raw):
    """Normalise known_displacements[SITE] to a list of (dE, dN)."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)) and len(raw) == 2 and not isinstance(raw[0], (list, tuple)):
        return [(float(raw[0]), float(raw[1]))]
    out = []
    for item in raw:
        if isinstance(item, dict):
            out.append((float(item.get("dE", item.get("e", item.get("dx")))),
                        float(item.get("dN", item.get("n", item.get("dy"))))))
        else:
            out.append((float(item[0]), float(item[1])))
    return out


def surface_extent_box(ext):
    from shapely.geometry import box
    if not ext:
        return None
    if isinstance(ext, dict):
        keys = ("e_min", "e_max", "n_min", "n_max")
        if all(ext.get(k) is not None for k in keys):
            return box(float(ext["e_min"]), float(ext["n_min"]), float(ext["e_max"]), float(ext["n_max"]))
    return None


def is_survey_surface(ing, surf):
    """Survey / existing: provenance employer, or role / name NGL or EGL."""
    prov = (ing.get("source") or {}).get("provenance")
    if prov == "employer":
        return True
    role = str(surf.get("role") or "")
    name = str(surf.get("name") or surf.get("surface") or "")
    blob = f"{role} {name}".upper()
    return any(tok in blob for tok in ("NGL", "EGL", "EXISTING", "SURVEY"))
