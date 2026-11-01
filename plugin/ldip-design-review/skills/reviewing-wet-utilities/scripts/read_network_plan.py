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
__version__ = "0.1.0"

import argparse
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

# Node MTEXT: "MH-01 / CL=412.50 / IL=410.20 / E=456789.12 / N=2734567.89"
NODE_RE = re.compile(
    r"(?P<id>[A-Za-z0-9._-]+)\s*/\s*"
    r"(?:CL\s*=\s*(?P<cl>-?\d+(?:\.\d+)?)\s*/\s*)?"
    r"(?:IL\s*=\s*(?P<il>-?\d+(?:\.\d+)?)\s*/\s*)?"
    r"(?:E\s*=\s*(?P<e>-?\d+(?:\.\d+)?)\s*/\s*)?"
    r"(?:N\s*=\s*(?P<n>-?\d+(?:\.\d+)?)?)?",
    re.I,
)
# Pipe label fragments
L_RE = re.compile(r"\bL\s*=\s*(?P<l>-?\d+(?:\.\d+)?)\s*m?\b", re.I)
S_RE = re.compile(r"\bS\s*=\s*(?P<s>-?\d+(?:\.\d+)?)\s*%?\b", re.I)
D_RE = re.compile(
    r"(?:Ø|⌀|D\s*=?\s*|DN\s*)(?P<d>\d{2,4}(?:\.\d+)?)\s*(?:mm)?",
    re.I,
)
LAYER_D_RE = re.compile(r"(?:DN|D|Ø|⌀)?\s*(?P<d>\d{2,4})\s*(?:mm)?", re.I)


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


def parse_node_label(text):
    t = " ".join(str(text).split())
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
        "raw": t,
    }


def parse_pipe_label(text):
    t = " ".join(str(text).split())
    out = {"raw": t, "length_m": None, "slope_pct": None, "diameter_mm": None}
    m = L_RE.search(t)
    if m:
        out["length_m"] = float(m.group("l"))
    m = S_RE.search(t)
    if m:
        out["slope_pct"] = float(m.group("s"))
    m = D_RE.search(t)
    if m:
        out["diameter_mm"] = float(m.group("d"))
    if out["length_m"] is None and out["slope_pct"] is None and out["diameter_mm"] is None:
        return None
    return out


def diameter_from_layer(layer_name, meta):
    if meta and meta.get("diameter_mm") is not None:
        return float(meta["diameter_mm"])
    short = layer_short(layer_name)
    m = LAYER_D_RE.search(short.replace("-", " "))
    if m:
        d = float(m.group("d"))
        if 50 <= d <= 3000:
            return d
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
            d_mm = diameter_from_layer(layer, meta)
            length = None
            try:
                if dtype == "LINE":
                    length = e.dxf.start.distance(e.dxf.end)
                elif dtype == "LWPOLYLINE":
                    length = e.length()
                elif hasattr(e, "length"):
                    length = e.length()
            except Exception:
                length = None
            pipes.append({
                "raw": None,
                "length_m": float(length) if length is not None else None,
                "slope_pct": None,
                "diameter_mm": d_mm,
                "layer": layer,
                "utility": util or meta.get("utility"),
                "handle": e.dxf.handle,
                "e_m": None,
                "n_m": None,
                "source": "plan",
                "kind": "pipe_geometry",
            })
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
