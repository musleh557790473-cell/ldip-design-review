#!/usr/bin/env python3
"""Read SewerGEMS (.stsw.sqlite) / WaterGEMS (.wtg.sqlite) hydraulic models (skill 11).

SPC-0006 Rev Q §4.19 / DQ-3:
  - base alternative = lowest AlternativeID
  - internal US feet → SI (length / levels → m; diameters → mm)
  - optional SHP export cross-check (NMAR Co-17 style)
  - writes networks.json + source sha256

Supports (a) the skill's synthetic schema used by T11 tests and (b) discovery of
Haestad tables that carry DomainElementID + AlternativeID physical rows.

Usage:
  python read_bentley_sqlite.py --sqlite <model.stsw.sqlite|.wtg.sqlite>
      --out <networks.json> [--network sewer|water|storm|irrigation]
      [--shp <export.shp|export.json>] [--label Co-17]
      [--site NMAR]

Exit: 0 done · 2 input · 3 missing package · 4 schema. No network, no shell.
"""
__version__ = "0.1.0"

import argparse
import math
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wet_common import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, SKILL_ROOT, atomic_write_json, emit, fail,
    ft_to_m, ft_to_mm, main_guard, path_ok, sha256_file, slope_pct,
)

SCHEMA_PATH = SKILL_ROOT / "templates" / "networks.schema.json"

# Synthetic / documented column aliases (feet storage)
DIAM_COLS = ("Diameter_ft", "Diameter", "diameter", "DiameterIn", "InsideDiameter")
LEN_COLS = ("Length_ft", "Length", "length", "LengthUserDefined", "UserDefinedLength")
INV_S = ("InvertStart_ft", "InvertElevationStart", "StartInvert", "Invert (Start)", "InvertStart")
INV_E = ("InvertStop_ft", "InvertElevationStop", "StopInvert", "Invert (Stop)", "InvertStop")
GROUND = ("Ground_ft", "GroundElevation", "RimElevation", "GroundElevation_ft", "Rim_ft")
X_COLS = ("X_ft", "GeometryX", "X", "Easting", "x")
Y_COLS = ("Y_ft", "GeometryY", "Y", "Northing", "y")
MAT_COLS = ("Material", "material")
ROUGH_COLS = ("Roughness", "HazenWilliamsC", "ManningsN", "RoughnessCoefficient", "HW_C")
ROUGH_TYPE = ("RoughnessType", "roughness_type")


def _cols(cur, table):
    return [r[1] for r in cur.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _tables(cur):
    return [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]


def _pick(row, names, cols):
    lower = {c.casefold(): c for c in cols}
    for n in names:
        if n in row:
            return row[n]
        c = lower.get(n.casefold())
        if c is not None and c in row.keys():
            return row[c]
    return None


def _as_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def detect_kind(path, network_arg):
    if network_arg:
        return network_arg
    name = Path(path).name.casefold()
    if ".wtg" in name or "water" in name or "pwt" in name:
        return "water"
    if "irr" in name or "tse" in name:
        return "irrigation"
    if "sto" in name or "storm" in name:
        return "storm"
    return "sewer"


def choose_base_alternative(cur, tables):
    """DQ-3: base alternative = lowest AlternativeID present."""
    alt_ids = set()
    for t in tables:
        cols = _cols(cur, t)
        if any(c.casefold() == "alternativeid" for c in cols):
            col = next(c for c in cols if c.casefold() == "alternativeid")
            for (aid,) in cur.execute(f'SELECT DISTINCT "{col}" FROM "{t}"').fetchall():
                if aid is not None:
                    try:
                        alt_ids.add(int(aid))
                    except (TypeError, ValueError):
                        pass
    if not alt_ids:
        return None, []
    return min(alt_ids), sorted(alt_ids)


def read_synthetic(cur, base_alt, network):
    """Schema used by T11 synthetic fixtures (Elements / Links / Physical / Alternatives)."""
    tables = set(_tables(cur))
    if not {"Elements", "Physical"}.issubset(tables):
        return None
    alts = []
    if "Alternatives" in tables:
        for r in cur.execute("SELECT AlternativeID, Label FROM Alternatives ORDER BY AlternativeID"):
            alts.append({"alternative_id": int(r[0]), "label": r[1]})
    elements = {}
    for r in cur.execute("SELECT * FROM Elements"):
        cols = r.keys()
        eid = int(r["ElementID"])
        elements[eid] = {
            "element_id": eid,
            "element_type": r["ElementType"] if "ElementType" in cols else "unknown",
            "label": r["Label"] if "Label" in cols else str(eid),
            "e_m": ft_to_m(_as_float(_pick(r, X_COLS, cols))),
            "n_m": ft_to_m(_as_float(_pick(r, Y_COLS, cols))),
            "ground_m": ft_to_m(_as_float(_pick(r, GROUND, cols))),
            "source": "model",
        }
    links = {}
    if "Links" in tables:
        for r in cur.execute("SELECT * FROM Links"):
            links[int(r["ElementID"])] = {
                "start_node_id": int(r["StartNodeID"]) if r["StartNodeID"] is not None else None,
                "stop_node_id": int(r["StopNodeID"]) if r["StopNodeID"] is not None else None,
            }
    conduits = []
    phys_cols = _cols(cur, "Physical")
    q = 'SELECT * FROM Physical WHERE AlternativeID = ?' if base_alt is not None else 'SELECT * FROM Physical'
    rows = cur.execute(q, (base_alt,) if base_alt is not None else ()).fetchall()
    for r in rows:
        eid = int(r["DomainElementID"])
        el = elements.get(eid, {"element_id": eid, "label": str(eid), "element_type": "conduit"})
        link = links.get(eid, {})
        d_ft = _as_float(_pick(r, DIAM_COLS, phys_cols))
        l_ft = _as_float(_pick(r, LEN_COLS, phys_cols))
        inv_s = _as_float(_pick(r, INV_S, phys_cols))
        inv_e = _as_float(_pick(r, INV_E, phys_cols))
        d_mm = ft_to_mm(d_ft)
        length_m = ft_to_m(l_ft)
        inv_s_m = ft_to_m(inv_s)
        inv_e_m = ft_to_m(inv_e)
        drop = None
        if inv_s_m is not None and inv_e_m is not None:
            drop = inv_s_m - inv_e_m
        start_id = link.get("start_node_id")
        stop_id = link.get("stop_node_id")
        start = elements.get(start_id) if start_id is not None else None
        stop = elements.get(stop_id) if stop_id is not None else None
        cover = None
        if start and start.get("ground_m") is not None and inv_s_m is not None and d_mm is not None:
            # soffit cover ≈ ground − (invert + diameter)
            cover = start["ground_m"] - (inv_s_m + d_mm / 1000.0)
        conduits.append({
            "id": el.get("label") or str(eid),
            "element_id": eid,
            "network": network,
            "source": "model",
            "kind": el.get("element_type") or "conduit",
            "diameter_mm": round(d_mm, 3) if d_mm is not None else None,
            "length_m": round(length_m, 6) if length_m is not None else None,
            "invert_start_m": round(inv_s_m, 6) if inv_s_m is not None else None,
            "invert_stop_m": round(inv_e_m, 6) if inv_e_m is not None else None,
            "slope_pct": round(slope_pct(length_m, drop), 6) if slope_pct(length_m, drop) is not None else None,
            "cover_m": round(cover, 6) if cover is not None else None,
            "material": _pick(r, MAT_COLS, phys_cols),
            "roughness": _as_float(_pick(r, ROUGH_COLS, phys_cols)),
            "roughness_type": _pick(r, ROUGH_TYPE, phys_cols),
            "start_node_id": start_id,
            "stop_node_id": stop_id,
            "start_label": (start or {}).get("label"),
            "stop_label": (stop or {}).get("label"),
            "e_m": (start or {}).get("e_m"),
            "n_m": (start or {}).get("n_m"),
            "alternative_id": base_alt,
            "units_source": "US_feet",
        })
    nodes = [v for v in elements.values() if (v.get("element_type") or "").lower()
             in ("manhole", "junction", "node", "outfall", "catchbasin", "hydrant", "valve", "tank", "reservoir")]
    if not nodes:
        nodes = [v for v in elements.values() if v["element_id"] not in {c["element_id"] for c in conduits}]
    loads = []
    if "Loads" in tables:
        lq = 'SELECT * FROM Loads WHERE AlternativeID = ?' if base_alt is not None else 'SELECT * FROM Loads'
        for r in cur.execute(lq, (base_alt,) if base_alt is not None else ()).fetchall():
            loads.append({
                "element_id": int(r["DomainElementID"]),
                "load": _as_float(r["Load_cms"] if "Load_cms" in r.keys() else r[list(r.keys())[-1]]),
                "alternative_id": base_alt,
            })
    return {
        "conduits": conduits,
        "nodes": nodes,
        "loads": loads,
        "alternatives": alts,
        "schema": "synthetic",
    }


def read_haestad_discovery(cur, base_alt, network):
    """Best-effort discovery of Haestad DomainElementID / AlternativeID physical tables."""
    tables = _tables(cur)
    phys_tables = []
    for t in tables:
        cols = _cols(cur, t)
        cf = {c.casefold() for c in cols}
        if "domainElementid".casefold() in cf and "alternativeid" in cf:
            if any(x in cf for x in ("diameter", "diameter_ft", "length", "length_ft",
                                     "invertelevationstart", "invertstart_ft", "hazenwilliamsc")):
                phys_tables.append(t)
    if not phys_tables:
        return None
    # Element label tables: prefer Pipe / Conduit
    label_map = {}
    geom_map = {}
    for t in tables:
        cols = _cols(cur, t)
        cf = {c.casefold(): c for c in cols}
        id_col = cf.get("elementid") or cf.get("id") or cf.get("domainelementid")
        lab_col = cf.get("label")
        if not id_col or not lab_col:
            continue
        x_col = next((cf[k] for k in ("geometryx", "x", "easting", "x_ft") if k in cf), None)
        y_col = next((cf[k] for k in ("geometryy", "y", "northing", "y_ft") if k in cf), None)
        g_col = next((cf[k] for k in ("groundelevation", "rimelevation", "ground_ft", "rim_ft") if k in cf), None)
        for r in cur.execute(f'SELECT * FROM "{t}"'):
            try:
                eid = int(r[id_col])
            except (TypeError, ValueError, KeyError):
                continue
            label_map[eid] = str(r[lab_col])
            if x_col or y_col or g_col:
                geom_map[eid] = {
                    "e_m": ft_to_m(_as_float(r[x_col])) if x_col else None,
                    "n_m": ft_to_m(_as_float(r[y_col])) if y_col else None,
                    "ground_m": ft_to_m(_as_float(r[g_col])) if g_col else None,
                }
    # Start/stop from Conduit / Pipe tables
    ends = {}
    for t in tables:
        cols = _cols(cur, t)
        cf = {c.casefold(): c for c in cols}
        id_col = cf.get("elementid") or cf.get("id") or cf.get("domainelementid")
        s_col = cf.get("startnodeid") or cf.get("startnode") or cf.get("fromnodeid")
        e_col = cf.get("stopnodeid") or cf.get("stopnode") or cf.get("tonodeid")
        if not (id_col and s_col and e_col):
            continue
        for r in cur.execute(f'SELECT * FROM "{t}"'):
            try:
                eid = int(r[id_col])
                ends[eid] = {
                    "start_node_id": int(r[s_col]) if r[s_col] is not None else None,
                    "stop_node_id": int(r[e_col]) if r[e_col] is not None else None,
                }
            except (TypeError, ValueError, KeyError):
                continue
    conduits = []
    for t in phys_tables:
        cols = _cols(cur, t)
        cf = {c.casefold(): c for c in cols}
        id_col = cf["domainelementid"]
        alt_col = cf["alternativeid"]
        q = f'SELECT * FROM "{t}" WHERE "{alt_col}" = ?' if base_alt is not None else f'SELECT * FROM "{t}"'
        for r in cur.execute(q, (base_alt,) if base_alt is not None else ()):
            eid = int(r[id_col])
            d_ft = _as_float(_pick(r, DIAM_COLS, cols))
            # Haestad often stores diameter already in inches or feet — detect: if > 5 treat as inches? 
            # DQ-3 / probe: US feet internal. If value looks like mm (> 50 and < 5000) leave as mm.
            d_mm = None
            if d_ft is not None:
                if d_ft > 50:  # already mm-ish
                    d_mm = d_ft
                elif d_ft > 5:  # inches
                    d_mm = d_ft * 25.4
                else:
                    d_mm = ft_to_mm(d_ft)
            l_ft = _as_float(_pick(r, LEN_COLS, cols))
            length_m = ft_to_m(l_ft) if l_ft is not None and l_ft < 1e5 else (_as_float(l_ft) if l_ft else None)
            # if length already metres (typical Haestad SI project) values are hundreds–thousands
            if l_ft is not None and l_ft > 500 and d_ft is not None and d_ft > 50:
                length_m = float(l_ft)
            inv_s = _as_float(_pick(r, INV_S, cols))
            inv_e = _as_float(_pick(r, INV_E, cols))
            # elevations: if absolute > 1000 assume feet
            def elev_m(v):
                if v is None:
                    return None
                return ft_to_m(v) if abs(v) > 200 or abs(v) < 50 else float(v)
            # Prefer feet→m always for US models (DQ-3); if value looks like UTM Z in metres (~400–800) keep.
            def elev_auto(v):
                if v is None:
                    return None
                if 200 <= abs(v) <= 2000:
                    return float(v)  # metres elevation band for KSA
                return ft_to_m(v)
            inv_s_m = elev_auto(inv_s)
            inv_e_m = elev_auto(inv_e)
            drop = (inv_s_m - inv_e_m) if inv_s_m is not None and inv_e_m is not None else None
            end = ends.get(eid, {})
            geom = geom_map.get(end.get("start_node_id")) or geom_map.get(eid) or {}
            cover = None
            g = geom.get("ground_m")
            if g is not None and inv_s_m is not None and d_mm is not None:
                cover = g - (inv_s_m + d_mm / 1000.0)
            conduits.append({
                "id": label_map.get(eid, str(eid)),
                "element_id": eid,
                "network": network,
                "source": "model",
                "kind": "conduit" if "conduit" in t.casefold() else "pipe",
                "diameter_mm": round(d_mm, 3) if d_mm is not None else None,
                "length_m": round(length_m, 6) if length_m is not None else None,
                "invert_start_m": round(inv_s_m, 6) if inv_s_m is not None else None,
                "invert_stop_m": round(inv_e_m, 6) if inv_e_m is not None else None,
                "slope_pct": round(slope_pct(length_m, drop), 6) if slope_pct(length_m, drop) is not None else None,
                "cover_m": round(cover, 6) if cover is not None else None,
                "material": _pick(r, MAT_COLS, cols),
                "roughness": _as_float(_pick(r, ROUGH_COLS, cols)),
                "roughness_type": _pick(r, ROUGH_TYPE, cols),
                "start_node_id": end.get("start_node_id"),
                "stop_node_id": end.get("stop_node_id"),
                "start_label": label_map.get(end.get("start_node_id")) if end.get("start_node_id") else None,
                "stop_label": label_map.get(end.get("stop_node_id")) if end.get("stop_node_id") else None,
                "e_m": geom.get("e_m"),
                "n_m": geom.get("n_m"),
                "alternative_id": base_alt,
                "units_source": "US_feet_or_detected",
                "physical_table": t,
            })
    nodes = []
    for eid, lab in label_map.items():
        if any(c["element_id"] == eid for c in conduits):
            continue
        g = geom_map.get(eid) or {}
        nodes.append({
            "element_id": eid, "label": lab, "element_type": "node",
            "e_m": g.get("e_m"), "n_m": g.get("n_m"), "ground_m": g.get("ground_m"),
            "source": "model",
        })
    return {
        "conduits": conduits,
        "nodes": nodes,
        "loads": [],
        "alternatives": [{"alternative_id": base_alt, "label": "base"}] if base_alt is not None else [],
        "schema": "haestad_discovery",
        "physical_tables": phys_tables,
    }


def read_shp_points(path):
    """Minimal SHP point reader, or JSON [{label,e,n}, ...] (metres).

    Returns (points, units) where units is ``metres`` (JSON / GeoJSON) or
    ``shp`` (binary — may still be feet when coordinates are small).
    """
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"SHP / export not found: {path}")
    if p.suffix.casefold() in (".json", ".geojson"):
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "features" in data:
            out = []
            for f in data["features"]:
                props = f.get("properties") or {}
                geom = f.get("geometry") or {}
                coords = geom.get("coordinates") or [None, None]
                out.append({
                    "label": str(props.get("label") or props.get("Label") or props.get("ID") or ""),
                    "e": coords[0], "n": coords[1],
                })
            return out, "metres"
        pts = data if isinstance(data, list) else data.get("points") or []
        return pts, "metres"
    # Binary SHP — points only (shape type 1 / 11) or first vertex of polyline
    with p.open("rb") as f:
        header = f.read(100)
        if len(header) < 100:
            raise ValueError("SHP too short")
        shape_type = struct.unpack("<i", header[32:36])[0]
        records = []
        while True:
            rec_hdr = f.read(8)
            if len(rec_hdr) < 8:
                break
            _num, length_words = struct.unpack(">2i", rec_hdr)
            payload = f.read(length_words * 2)
            if len(payload) < 4:
                continue
            st = struct.unpack("<i", payload[0:4])[0]
            if st in (1, 11, 21) and len(payload) >= 20:
                x, y = struct.unpack("<2d", payload[4:20])
                records.append({"label": "", "e": x, "n": y, "record": _num})
            elif st in (3, 13, 23) and len(payload) >= 44:
                # polyline — take first vertex as representative
                # after box(32) + numParts(4) + numPoints(4) + parts...
                try:
                    _xmin, _ymin, _xmax, _ymax = struct.unpack("<4d", payload[4:36])
                    n_parts, n_points = struct.unpack("<2i", payload[36:44])
                    off = 44 + 4 * n_parts
                    if len(payload) >= off + 16:
                        x, y = struct.unpack("<2d", payload[off:off + 16])
                        records.append({"label": "", "e": x, "n": y, "record": _num})
                except struct.error:
                    pass
        # DBF labels when present
        dbf = p.with_suffix(".dbf")
        if dbf.is_file() and records:
            labels = _read_dbf_labels(dbf)
            for i, rec in enumerate(records):
                if i < len(labels):
                    rec["label"] = labels[i]
        return records, "shp"


def _read_dbf_labels(path):
    with open(path, "rb") as f:
        header = f.read(32)
        n_records = struct.unpack("<I", header[4:8])[0]
        header_len = struct.unpack("<H", header[8:10])[0]
        rec_len = struct.unpack("<H", header[10:12])[0]
        f.seek(32)
        fields = []
        while True:
            desc = f.read(32)
            if not desc or desc[0] == 0x0D:
                break
            name = desc[0:11].split(b"\x00", 1)[0].decode("latin1", "replace")
            ftype = chr(desc[11])
            flen = desc[16]
            fields.append((name, ftype, flen))
        f.seek(header_len)
        label_idx = 0
        for i, (name, _t, _l) in enumerate(fields):
            if name.casefold() in ("label", "id", "name", "handle", "text"):
                label_idx = i
                break
        out = []
        for _ in range(n_records):
            raw = f.read(rec_len)
            if not raw:
                break
            data = raw[1:]  # skip delete flag
            vals = []
            pos = 0
            for _name, _t, flen in fields:
                vals.append(data[pos:pos + flen].decode("latin1", "replace").strip())
                pos += flen
            out.append(vals[label_idx] if vals else "")
        return out


def cross_check_shp(nodes, conduits, shp_path, label, tol_m=0.05):
    """DQ-3 SHP cross-check — NMAR Co-17 style: match label, compare E/N."""
    pts, units = read_shp_points(shp_path)
    target = (label or "Co-17").casefold()
    matches = [p for p in pts if str(p.get("label") or "").casefold() == target]
    if not matches and len(pts) == 1 and not any(p.get("label") for p in pts):
        matches = pts  # single anonymous point — compare to model label
    model_pts = []
    for n in nodes:
        if str(n.get("label") or "").casefold() == target:
            model_pts.append(n)
    for c in conduits:
        if str(c.get("id") or "").casefold() == target:
            model_pts.append({"label": c["id"], "e_m": c.get("e_m"), "n_m": c.get("n_m")})
    result = {
        "label": label or "Co-17",
        "shp_count": len(matches),
        "model_count": len(model_pts),
        "tolerance_m": tol_m,
        "result": "not_run",
        "delta_m": None,
        "detail": None,
    }
    if not matches:
        result["result"] = "flag"
        result["detail"] = f"label {label} not found in SHP export"
        return result
    if not model_pts:
        result["result"] = "flag"
        result["detail"] = f"label {label} not found in model"
        return result
    sp, mp = matches[0], model_pts[0]
    if mp.get("e_m") is None or mp.get("n_m") is None or sp.get("e") is None or sp.get("n") is None:
        result["result"] = "not_run"
        result["detail"] = "coordinates missing on model or SHP"
        return result
    se, sn = float(sp["e"]), float(sp["n"])
    # JSON exports are metres; binary SHP with small coords may still be feet
    if units == "shp" and abs(se) < 1e5 and abs(sn) < 1e5:
        se, sn = ft_to_m(se), ft_to_m(sn)
    delta = math.hypot(se - float(mp["e_m"]), sn - float(mp["n_m"]))
    result["delta_m"] = round(delta, 6)
    result["shp_en"] = [se, sn]
    result["model_en"] = [mp["e_m"], mp["n_m"]]
    result["result"] = "pass" if delta <= tol_m + 1e-9 else "flag"
    result["detail"] = f"|Δ|={delta:.4f} m vs tol {tol_m} m"
    return result


def build_doc(path, network, data, base_alt, all_alts, shp_check=None):
    conduits = data["conduits"]
    total_m = sum(c["length_m"] or 0.0 for c in conduits)
    by_d = {}
    for c in conduits:
        d = c.get("diameter_mm")
        if d is None:
            key = "unknown"
        else:
            key = str(int(round(d))) if abs(d - round(d)) < 0.5 else f"{d:.1f}"
        by_d[key] = by_d.get(key, 0) + 1
    return {
        "networks_version": "1.0",
        "network": network,
        "source": {
            "path": str(path),
            "sha256": sha256_file(path),
            "kind": "bentley_sqlite",
            "schema": data.get("schema"),
        },
        "base_alternative_id": base_alt,
        "alternatives": data.get("alternatives") or (
            [{"alternative_id": a, "label": None} for a in all_alts]
        ),
        "units": {"length": "m", "diameter": "mm", "source_storage": "US_feet"},
        "conduits": conduits,
        "nodes": data.get("nodes") or [],
        "loads": data.get("loads") or [],
        "plan": [],
        "summary": {
            "conduit_count": len(conduits),
            "node_count": len(data.get("nodes") or []),
            "length_m": round(total_m, 3),
            "length_km": round(total_m / 1000.0, 3),
            "diameter_counts": by_d,
        },
        "shp_cross_check": shp_check,
        "warnings": data.get("warnings") or [],
        "meta": {
            "reader": f"read_bentley_sqlite/{__version__}",
            "physical_tables": data.get("physical_tables"),
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sqlite", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--network", choices=["sewer", "water", "storm", "irrigation", "force_main"])
    ap.add_argument("--shp", help="SHP or JSON export for Co-17-style cross-check (DQ-3)")
    ap.add_argument("--label", default="Co-17", help="Label to cross-check against SHP (default Co-17)")
    ap.add_argument("--site", default=None)
    a = ap.parse_args(argv)

    def run():
        path = Path(a.sqlite)
        if not path.is_file():
            return fail(EXIT_INPUT, f"sqlite not found: {path}")
        if not path_ok(path):
            return fail(EXIT_INPUT, f"path longer than 250 characters: {path}")
        network = detect_kind(path, a.network)
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            return fail(EXIT_INPUT, f"cannot open sqlite: {exc}")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        tables = _tables(cur)
        base_alt, all_alts = choose_base_alternative(cur, tables)
        data = read_synthetic(cur, base_alt, network)
        if data is None:
            data = read_haestad_discovery(cur, base_alt, network)
        conn.close()
        if data is None:
            return fail(EXIT_INPUT, "unrecognised Bentley sqlite schema (no Physical / Haestad tables)")
        shp_check = None
        if a.shp:
            try:
                shp_check = cross_check_shp(data.get("nodes") or [], data["conduits"], a.shp, a.label)
            except ValueError as exc:
                return fail(EXIT_INPUT, str(exc))
        doc = build_doc(path, network, data, base_alt, all_alts, shp_check=shp_check)
        if a.site:
            doc["meta"]["site"] = a.site.upper()
        # Soft schema check
        try:
            from jsonschema import Draft202012Validator
            schema = __import__("json").loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            errs = list(Draft202012Validator(schema).iter_errors(doc))
            if errs:
                return fail(EXIT_SCHEMA, "networks.json failed schema",
                            errors=[e.message for e in errs[:20]])
        except ImportError:
            pass
        atomic_write_json(a.out, doc)
        emit({
            "status": "ok",
            "code": EXIT_OK,
            "out": str(a.out),
            "network": network,
            "conduit_count": doc["summary"]["conduit_count"],
            "length_km": doc["summary"]["length_km"],
            "base_alternative_id": base_alt,
            "alternatives": all_alts,
            "source_sha256": doc["source"]["sha256"],
            "shp_cross_check": (shp_check or {}).get("result") if shp_check else None,
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
