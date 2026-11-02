#!/usr/bin/env python3
"""Read wet-network plan labels from skill-1 DXF (skill 11, SPC-0006 Rev Q §4.19).

DXF only (DQ-6): pipes by layer (diameter from layer name / label), node labels
(MTEXT ``ID / CL / IL / E / N``), pipe labels (``L= / S=``), valves, hydrants,
catch basins, outfalls by block / layer. Layer map: references/wet_layers.json.

Usage:
  python read_network_plan.py --dxf <plan.dxf> --out <wet_plan.json>
      [--site NMAR] [--network sewer|water|storm|irrigation]
      [--layers <wet_layers.json>] [--merge-networks <networks.json>]

Exit: 0 done · 2 input · 3 ezdxf missing · 4 schema. No network, no shell.
"""
__version__ = "0.1.1"

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wet_common import (  # noqa: E402
    EXIT_DEP, EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, SKILL_ROOT, atomic_write_json,
    emit, fail, layer_short, load_wet_layers, main_guard, path_ok, read_json,
    resolve_layer, sha256_file,
)

SCHEMA_PATH = SKILL_ROOT / "templates" / "networks.schema.json"

# Node MTEXT after plain_text(): ID then CL: / IL: / E: / N: on separate lines
# (or joined with " / "). Colon + optional `m` suffix. Example:
#   MH-01\nCL: 779.45m\nIL: 777.40m\nE: 656670.49\nN: 2714078.99
NODE_RE = re.compile(
    r"(?P<id>[A-Za-z0-9._-]+)"
    r"(?:\s*/\s*|\s+)"
    r"CL\s*:\s*(?P<cl>-?\d+(?:\.\d+)?)\s*m?"
    r"(?:\s*/\s*|\s+)"
    r"IL\s*:\s*(?P<il>-?\d+(?:\.\d+)?)\s*m?"
    r"(?:\s*/\s*|\s+)"
    r"E\s*:\s*(?P<e>-?\d+(?:\.\d+)?)"
    r"(?:\s*/\s*|\s+)"
    r"N\s*:\s*(?P<n>-?\d+(?:\.\d+)?)",
    re.I,
)
# Pipe labels: optional diameter prefix with NO space before L= ; then S=
#   "L=63.01\nS=1.50%"  or  "200L=7.58\nS=0.63%"
PIPE_RE = re.compile(
    r"(?:(?P<d>\d{2,4})(?=\s*L\s*=))?"
    r"\s*L\s*=\s*(?P<l>-?\d+(?:\.\d+)?)\s*m?"
    r"(?:\s*/\s*|\s+)"
    r"S\s*=\s*(?P<s>-?\d+(?:\.\d+)?)\s*%?",
    re.I,
)
L_RE = re.compile(r"(?<![A-Za-z0-9.])L\s*=\s*(?P<l>-?\d+(?:\.\d+)?)\s*m?\b", re.I)
S_RE = re.compile(r"(?<![A-Za-z0-9.])S\s*=\s*(?P<s>-?\d+(?:\.\d+)?)\s*%?", re.I)
D_PREFIX_RE = re.compile(r"^(?P<d>\d{2,4})(?=L\s*=)", re.I)
LAYER_PIPE_DIA_RE = re.compile(r"Pipe\s*Dia\s*(\d+)", re.I)
LAYER_PCT_C_RE = re.compile(r"%%c(\d+)\s*mm", re.I)


def _require_ezdxf():
    try:
        import ezdxf  # noqa: F401
    except ImportError as exc:
        raise ImportError("ezdxf is not installed") from exc


def _text_of(entity):
    if entity.dxftype() == "MTEXT":
        try:
            return entity.plain_text()
        except Exception:
            return entity.text
    return entity.dxf.text


def _insert_xy(entity):
    try:
        if entity.dxftype() == "MTEXT":
            p = entity.dxf.insert
        else:
            p = entity.dxf.insert
        return float(p[0]), float(p[1])
    except Exception:
        return None, None


def _normalize_label_text(text):
    """Join MTEXT paragraph breaks to spaces so slash-or-newline forms match."""
    t = str(text).replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\\P", "\n").replace("\\p", "\n")
    # Collapse whitespace but keep a single space between fields
    return " ".join(t.split())


def parse_node_label(text):
    raw = str(text)
    t = _normalize_label_text(text)
    m = NODE_RE.search(t)
    if not m:
        return None

    def num(g):
        v = m.group(g)
        return float(v) if v is not None else None

    return {
        "id": m.group("id"),
        "cl_m": num("cl"),
        "il_m": num("il"),
        "e_m": num("e"),
        "n_m": num("n"),
        "raw": raw.strip(),
    }


def parse_pipe_label(text):
    raw = str(text)
    t = _normalize_label_text(text)
    out = {"raw": raw.strip(), "length_m": None, "slope_pct": None, "diameter_mm": None}
    m = PIPE_RE.search(t)
    if m:
        if m.group("d"):
            out["diameter_mm"] = float(m.group("d"))
        out["length_m"] = float(m.group("l"))
        out["slope_pct"] = float(m.group("s"))
        return out
    # Partial labels (L= only or S= only) still count as pipe labels (W2)
    m = L_RE.search(t)
    if m:
        out["length_m"] = float(m.group("l"))
    m = S_RE.search(t)
    if m:
        out["slope_pct"] = float(m.group("s"))
    m = D_PREFIX_RE.search(t.replace(" ", ""))
    if m and out["diameter_mm"] is None:
        out["diameter_mm"] = float(m.group("d"))
    if out["length_m"] is None and out["slope_pct"] is None and out["diameter_mm"] is None:
        return None
    return out


def diameter_from_layer(layer_name, meta):
    if meta and meta.get("diameter_mm") is not None:
        return float(meta["diameter_mm"])
    short = layer_short(layer_name)
    for rx in (LAYER_PIPE_DIA_RE, LAYER_PCT_C_RE):
        m = rx.search(short)
        if m:
            return float(m.group(1))
    return None


def _bulge_arc_length(x1, y1, x2, y2, bulge):
    """Arc length for one LWPOLYLINE segment with DXF bulge (T2)."""
    dx, dy = x2 - x1, y2 - y1
    chord = math.hypot(dx, dy)
    if chord == 0.0:
        return 0.0
    if bulge is None or abs(bulge) < 1e-12:
        return chord
    # included angle = 4 * atan(bulge); radius from chord + bulge
    angle = 4.0 * math.atan(float(bulge))
    radius = chord * (1.0 + float(bulge) ** 2) / (4.0 * abs(float(bulge)))
    return abs(radius * angle)


def _polyline_length_from_points(points, closed=False):
    """Sum segment lengths; each point is (x, y[, bulge])."""
    if not points or len(points) < 2:
        return 0.0
    pts = list(points)
    if closed and pts[0][:2] != pts[-1][:2]:
        pts = pts + [pts[0]]
    total = 0.0
    for i in range(len(pts) - 1):
        x1, y1 = float(pts[i][0]), float(pts[i][1])
        x2, y2 = float(pts[i + 1][0]), float(pts[i + 1][1])
        bulge = pts[i][2] if len(pts[i]) > 2 else 0.0
        total += _bulge_arc_length(x1, y1, x2, y2, bulge)
    return total


SHEET_COORD_E_MAX = 100_000.0  # U2: legend / sheet-space geometry


def layer_pipe_set(layer_name):
    """U2: own layers → design; xref layers → xref:<name>."""
    if not layer_name or "$" not in layer_name:
        return "design"
    prefix = layer_name.rsplit("$", 1)[0]
    # Strip trailing $0 / $N index tokens
    parts = [p for p in prefix.split("$") if p and not p.isdigit()]
    xref_name = parts[0] if parts else prefix
    xref_name = xref_name.strip() or "xref"
    if xref_name.casefold() in ("", "0"):
        return "design"
    return f"xref:{xref_name}"


def entity_points_xy(e, dtype):
    """Return list of (x, y) for sheet-coord filter and length."""
    try:
        if dtype == "LINE":
            return [(float(e.dxf.start[0]), float(e.dxf.start[1])),
                    (float(e.dxf.end[0]), float(e.dxf.end[1]))]
        if dtype == "LWPOLYLINE":
            return [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
        if dtype == "POLYLINE":
            return [(float(v.dxf.location[0]), float(v.dxf.location[1])) for v in e.vertices]
    except Exception:
        return []
    return []


def is_sheet_coordinate_geometry(points):
    """U2: drop legend polylines in sheet space (all E < 100,000)."""
    if not points:
        return False
    return all(x < SHEET_COORD_E_MAX for x, _y in points)


def entity_path_length(e, dtype):
    """Compute plan length from vertices; bulges as arcs (T2). Never trust e.length() alone."""
    try:
        if dtype == "LINE":
            return float(e.dxf.start.distance(e.dxf.end))
        if dtype == "LWPOLYLINE":
            pts = []
            with_bulge = list(e.get_points("xyb"))
            for p in with_bulge:
                if len(p) >= 3:
                    pts.append((float(p[0]), float(p[1]), float(p[2])))
                else:
                    pts.append((float(p[0]), float(p[1]), 0.0))
            closed = bool(e.closed) if hasattr(e, "closed") else bool(e.dxf.flags & 1)
            length = _polyline_length_from_points(pts, closed=closed)
            if length > 0:
                return length
            try:
                fallback = float(e.length())
                return fallback if fallback > 0 else length
            except Exception:
                return length
        if dtype == "POLYLINE":
            pts = []
            for v in e.vertices:
                loc = v.dxf.location
                bulge = float(getattr(v.dxf, "bulge", 0.0) or 0.0)
                pts.append((float(loc[0]), float(loc[1]), bulge))
            closed = bool(e.is_closed) if hasattr(e, "is_closed") else False
            return _polyline_length_from_points(pts, closed=closed)
    except Exception:
        pass
    return None


def read_dxf(path, layers_map, network_hint=None):
    _require_ezdxf()
    import ezdxf
    from ezdxf import recover
    try:
        doc, _audits = recover.readfile(str(path))
    except Exception:
        doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    nodes, pipes, fixtures = [], [], []
    for e in msp:
        dtype = e.dxftype()
        if dtype not in ("TEXT", "MTEXT", "INSERT", "LINE", "LWPOLYLINE", "POLYLINE"):
            continue
        layer = e.dxf.layer if hasattr(e.dxf, "layer") else "0"
        # T2: drop layer 0 (title-block / unassigned geometry)
        if layer_short(layer) == "0" or str(layer).strip() == "0":
            continue
        meta = resolve_layer(layer, layers_map) or {}
        util = meta.get("utility") or network_hint
        if dtype in ("TEXT", "MTEXT"):
            text = _text_of(e)
            x, y = _insert_xy(e)
            kind = meta.get("feature") or "label"
            node = parse_node_label(text)
            if node and (kind in ("node", "manhole", "label", None) or "IL" in text.upper()):
                nodes.append({
                    **node,
                    "layer": layer,
                    "utility": util,
                    "handle": e.dxf.handle,
                    "e_m": node.get("e_m") if node.get("e_m") is not None else x,
                    "n_m": node.get("n_m") if node.get("n_m") is not None else y,
                    "source": "plan",
                })
                continue
            pipe = parse_pipe_label(text)
            if pipe:
                pipes.append({
                    **pipe,
                    "layer": layer,
                    "utility": util,
                    "handle": e.dxf.handle,
                    "e_m": x,
                    "n_m": y,
                    "diameter_mm": pipe.get("diameter_mm") or diameter_from_layer(layer, meta),
                    "source": "plan",
                    "kind": "pipe_label",
                    "set": layer_pipe_set(layer),
                })
                continue
            if kind in ("hydrant", "valve", "washout", "air_valve", "catch_basin", "outfall", "pond"):
                fixtures.append({
                    "kind": kind,
                    "label": text.strip(),
                    "layer": layer,
                    "utility": util,
                    "handle": e.dxf.handle,
                    "e_m": x,
                    "n_m": y,
                    "source": "plan",
                })
        elif dtype == "INSERT":
            name = e.dxf.name
            kind = meta.get("feature")
            if not kind:
                low = name.casefold()
                for key in ("hydrant", "valve", "washout", "air", "catch", "outfall", "pond", "manhole"):
                    if key in low:
                        kind = {"air": "air_valve", "catch": "catch_basin"}.get(key, key)
                        break
            if kind:
                x, y = float(e.dxf.insert[0]), float(e.dxf.insert[1])
                fixtures.append({
                    "kind": kind,
                    "label": name,
                    "layer": layer,
                    "utility": util,
                    "handle": e.dxf.handle,
                    "e_m": x,
                    "n_m": y,
                    "source": "plan",
                })
        elif dtype in ("LINE", "LWPOLYLINE", "POLYLINE"):
            if not meta.get("utility") and not meta.get("diameter_mm"):
                continue
            pts_xy = entity_points_xy(e, dtype)
            d_mm = diameter_from_layer(layer, meta)
            length = entity_path_length(e, dtype)
            e0 = pts_xy[0][0] if pts_xy else None
            n0 = pts_xy[0][1] if pts_xy else None
            pipes.append({
                "raw": None,
                "length_m": float(length) if length is not None else None,
                "slope_pct": None,
                "diameter_mm": d_mm,
                "layer": layer,
                "utility": util or meta.get("utility"),
                "handle": e.dxf.handle,
                "e_m": e0,
                "n_m": n0,
                "source": "plan",
                "kind": "pipe_geometry",
                "set": layer_pipe_set(layer),
                "_pts_xy": pts_xy,
            })
    # U2: drop sheet-coordinate legend geometry only when the drawing also
    # has site-coordinate pipes (E ≥ 100,000). Pure local-coord DXFs keep all.
    has_site = any(
        (p.get("e_m") is not None and p["e_m"] >= SHEET_COORD_E_MAX)
        or any(x >= SHEET_COORD_E_MAX for x, _y in (p.get("_pts_xy") or []))
        for p in pipes if p.get("kind") == "pipe_geometry"
    )
    if has_site:
        kept = []
        for p in pipes:
            if p.get("kind") == "pipe_geometry" and is_sheet_coordinate_geometry(
                    p.get("_pts_xy") or []):
                continue
            if p.get("kind") == "pipe_label" and p.get("e_m") is not None and (
                    p["e_m"] < SHEET_COORD_E_MAX):
                continue
            kept.append(p)
        pipes = kept
    for p in pipes:
        p.pop("_pts_xy", None)
    return nodes, pipes, fixtures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dxf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--site", default="NMAR")
    ap.add_argument("--network", choices=["sewer", "water", "storm", "irrigation"])
    ap.add_argument("--layers")
    ap.add_argument("--merge-networks", help="Existing networks.json to merge plan rows into")
    a = ap.parse_args(argv)

    def run():
        path = Path(a.dxf)
        if not path.is_file():
            return fail(EXIT_INPUT, f"DXF not found: {path}")
        if not path_ok(path):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {path}")
        try:
            _require_ezdxf()
        except ImportError as exc:
            return fail(EXIT_DEP, str(exc))
        layers_map = load_wet_layers(a.layers, site=a.site)
        nodes, pipes, fixtures = read_dxf(path, layers_map, network_hint=a.network)
        plan_doc = {
            "plan_version": "1.0",
            "source": {"path": str(path), "sha256": sha256_file(path), "kind": "dxf"},
            "site": a.site.upper(),
            "network": a.network,
            "nodes": nodes,
            "pipes": pipes,
            "fixtures": fixtures,
            "counts": {
                "nodes": len(nodes),
                "pipes": len(pipes),
                "fixtures": len(fixtures),
            },
            "meta": {"reader": f"read_network_plan/{__version__}", "route": "dxf"},
        }
        if a.merge_networks:
            net = read_json(a.merge_networks)
            net.setdefault("plan", [])
            # Flatten plan pipes into plan[] conduit-like rows for W2
            for p in pipes:
                net["plan"].append({
                    "id": None,
                    "source": "plan",
                    "network": p.get("utility") or a.network,
                    "diameter_mm": p.get("diameter_mm"),
                    "length_m": p.get("length_m"),
                    "slope_pct": p.get("slope_pct"),
                    "invert_start_m": None,
                    "invert_stop_m": None,
                    "e_m": p.get("e_m"),
                    "n_m": p.get("n_m"),
                    "layer": p.get("layer"),
                    "handle": p.get("handle"),
                    "raw": p.get("raw"),
                    "kind": p.get("kind"),
                })
            for n in nodes:
                net.setdefault("plan_nodes", []).append(n)
            net.setdefault("fixtures", []).extend(fixtures)
            net.setdefault("meta", {})["plan_reader"] = f"read_network_plan/{__version__}"
            try:
                from jsonschema import Draft202012Validator
                import json
                schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
                errs = list(Draft202012Validator(schema).iter_errors(net))
                if errs:
                    return fail(EXIT_SCHEMA, "merged networks.json failed schema",
                                errors=[e.message for e in errs[:20]])
            except ImportError:
                pass
            atomic_write_json(a.out, net)
            emit({
                "status": "ok", "code": EXIT_OK, "out": str(a.out),
                "merged": True,
                "plan_pipes": len(pipes), "plan_nodes": len(nodes),
                "fixtures": len(fixtures),
            })
            return EXIT_OK
        atomic_write_json(a.out, plan_doc)
        emit({
            "status": "ok", "code": EXIT_OK, "out": str(a.out),
            "plan_pipes": len(pipes), "plan_nodes": len(nodes),
            "fixtures": len(fixtures),
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
