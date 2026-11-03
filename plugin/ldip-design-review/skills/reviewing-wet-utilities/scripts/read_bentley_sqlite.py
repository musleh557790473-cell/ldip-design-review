#!/usr/bin/env python3
"""Read SewerGEMS (.stsw.sqlite) / WaterGEMS (.wtg.sqlite) hydraulic models (skill 11).

SPC-0006 Rev Q §4.19 / DQ-3 / PR #28 R1–R2:
  - base alternative = lowest AlternativeID **per element**
  - storage is US feet **always** → SI (× 0.3048 for lengths/levels; × 304.8 for diameters mm)
  - real OpenFlows tables (HMIModelingElement / Conduit / IdahoPipe …) — no invented schema
  - optional SHP export cross-check (NMAR Co-17: D / L / inverts)

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


def _cols(cur, table):
    return [r[1] for r in cur.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _tables(cur):
    return [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]


def _has(tables, name):
    return name in tables


def _as_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _row_alt(row, cols):
    if row is None:
        return None
    alt_col = _col(cols or [], "AlternativeID")
    if not alt_col:
        return None
    try:
        return int(row[alt_col]) if row[alt_col] is not None else None
    except (TypeError, ValueError, KeyError):
        return None


def _col(cols, *names):
    lower = {c.casefold(): c for c in cols}
    for n in names:
        if n.casefold() in lower:
            return lower[n.casefold()]
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
    """Informational: global min AlternativeID across tables (per-element pick is in _index)."""
    alt_ids = set()
    for t in tables:
        cols = _cols(cur, t)
        col = _col(cols, "AlternativeID")
        if not col:
            continue
        for (aid,) in cur.execute(f'SELECT DISTINCT "{col}" FROM "{t}"').fetchall():
            if aid is None:
                continue
            try:
                alt_ids.add(int(aid))
            except (TypeError, ValueError):
                pass
    if not alt_ids:
        return None, []
    return min(alt_ids), sorted(alt_ids)


def _index_by_domain(cur, table, base_alt, required_cols=()):
    """Map DomainElementID → row with the **lowest AlternativeID for that element** (R1).

    ``base_alt`` is unused for filtering; kept for call-site compatibility.
    """
    if not table:
        return {}, []
    cols = _cols(cur, table)
    id_col = _col(cols, "DomainElementID", "ElementID")
    if not id_col:
        return {}, cols
    for need in required_cols:
        if not _col(cols, need):
            return {}, cols
    alt_col = _col(cols, "AlternativeID")
    out = {}
    best_alt = {}
    rows = cur.execute(f'SELECT * FROM "{table}"').fetchall()
    for r in rows:
        try:
            eid = int(r[id_col])
        except (TypeError, ValueError, KeyError):
            continue
        if alt_col is None:
            out[eid] = r
            continue
        try:
            aid = int(r[alt_col]) if r[alt_col] is not None else None
        except (TypeError, ValueError):
            aid = None
        if aid is None:
            continue
        prev = best_alt.get(eid)
        if prev is None or aid < prev:
            best_alt[eid] = aid
            out[eid] = r
    return out, cols


def _safe_index(cur, table, base_alt, required_cols=()):
    if not table:
        return {}, []
    try:
        return _index_by_domain(cur, table, base_alt, required_cols=required_cols)
    except sqlite3.Error:
        return {}, []


# --------------------------------------------------------------------------- X2 / A4 WKB (HMIRealGeometry)
# OGC WKB / ISO SQL-MM + PostGIS EWKB type-word flags:
#   Z = 0x80000000, M = 0x40000000, SRID = 0x20000000 (skip int32 after type).
# ISO dimension offsets: +1000 Z, +2000 M, +3000 ZM.
# Coordinates stored in US feet → convert to m.


def _wkb_endian(byte_order):
    if byte_order == 1:
        return "<"  # NDR little-endian
    if byte_order == 0:
        return ">"  # XDR big-endian
    return None


def _wkb_type_flags(gtype):
    """Return (base_type, has_z, has_m, has_srid) from the WKB/EWKB type word (A4)."""
    t = int(gtype) & 0xFFFFFFFF
    has_z = bool(t & 0x80000000)
    has_m = bool(t & 0x40000000)
    has_srid = bool(t & 0x20000000)
    # Strip EWKB high flags (Z/M/SRID) before ISO dimension decode
    base = t & 0x0FFFFFFF
    iso = base % 10000
    if 3000 <= iso < 4000:
        has_z = True
        has_m = True
        base -= 3000
    elif 2000 <= iso < 3000:
        has_m = True
        base -= 2000
    elif 1000 <= iso < 2000:
        has_z = True
        base -= 1000
    return base, has_z, has_m, has_srid


def _wkb_has_z(gtype):
    """Back-compat: (base_type, has_z). Prefer ``_wkb_type_flags`` (A4)."""
    base, has_z, _has_m, _srid = _wkb_type_flags(gtype)
    return base, has_z


def _wkb_read_point(buf, off, fmt, has_z, has_m=False):
    n = 2 + (1 if has_z else 0) + (1 if has_m else 0)
    need = 8 * n
    if off + need > len(buf):
        return None, off
    vals = struct.unpack(f"{fmt}{n}d", buf[off:off + need])
    return (float(vals[0]), float(vals[1])), off + need


def parse_wkb_vertices_ft(blob):
    """Parse WKB blob → list of (x_ft, y_ft). Endian-safe; Z/M/SRID type flags (A4).

    Supports Point, LineString, MultiLineString, MultiPoint. Returns [] on empty/unreadable.
    """
    if blob is None:
        return []
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, bytearray):
        blob = bytes(blob)
    if not isinstance(blob, (bytes,)):
        # Some drivers expose hex text
        try:
            s = str(blob).strip()
            if s[:2].casefold() == "0x":
                s = s[2:]
            blob = bytes.fromhex(s)
        except (ValueError, TypeError):
            return []
    if len(blob) < 5:
        return []

    def parse_geom(buf, off):
        if off >= len(buf):
            return [], off
        endian = _wkb_endian(buf[off])
        if endian is None:
            return [], off
        off += 1
        if off + 4 > len(buf):
            return [], off
        gtype = struct.unpack(f"{endian}I", buf[off:off + 4])[0]
        off += 4
        base, has_z, has_m, has_srid = _wkb_type_flags(gtype)
        if has_srid:
            if off + 4 > len(buf):
                return [], off
            off += 4  # skip SRID int32
        pts = []
        if base == 1:  # Point
            pt, off = _wkb_read_point(buf, off, endian, has_z, has_m)
            if pt:
                pts.append(pt)
        elif base == 2:  # LineString
            if off + 4 > len(buf):
                return pts, off
            n = struct.unpack(f"{endian}I", buf[off:off + 4])[0]
            off += 4
            for _ in range(n):
                pt, off = _wkb_read_point(buf, off, endian, has_z, has_m)
                if pt is None:
                    break
                pts.append(pt)
        elif base == 5:  # MultiLineString
            if off + 4 > len(buf):
                return pts, off
            n = struct.unpack(f"{endian}I", buf[off:off + 4])[0]
            off += 4
            for _ in range(n):
                sub, off = parse_geom(buf, off)
                pts.extend(sub)
        elif base == 4:  # MultiPoint
            if off + 4 > len(buf):
                return pts, off
            n = struct.unpack(f"{endian}I", buf[off:off + 4])[0]
            off += 4
            for _ in range(n):
                sub, off = parse_geom(buf, off)
                pts.extend(sub)
        else:
            # Unsupported type — stop
            return pts, len(buf)
        return pts, off

    pts, _ = parse_geom(blob, 0)
    return pts


def geometry_from_wkb_ft(blob):
    """X2: WKB in US feet → SI vertices / start E-N for a conduit or pipe."""
    verts_ft = parse_wkb_vertices_ft(blob)
    if not verts_ft:
        return {
            "vertices_m": [],
            "e_m": None,
            "n_m": None,
            "end_e_m": None,
            "end_n_m": None,
            "has_geometry": False,
        }
    verts_m = [
        [round(ft_to_m(x), 6), round(ft_to_m(y), 6)] for x, y in verts_ft
    ]
    return {
        "vertices_m": verts_m,
        "e_m": verts_m[0][0],
        "n_m": verts_m[0][1],
        "end_e_m": verts_m[-1][0],
        "end_n_m": verts_m[-1][1],
        "has_geometry": True,
    }


def _row_blob(row, col):
    """Return non-empty WKB blob from a sqlite3.Row / mapping, or None."""
    if row is None or not col:
        return None
    blob = None
    try:
        if hasattr(row, "keys") and col in row.keys():
            blob = row[col]
        else:
            blob = row[col]
    except (KeyError, IndexError, TypeError):
        blob = None
    if blob is None:
        return None
    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, bytearray):
        blob = bytes(blob)
    if isinstance(blob, bytes) and len(blob) == 0:
        return None
    if isinstance(blob, str) and not blob.strip():
        return None
    return blob


def _geometry_blob(row, cols):
    """B2: prefer HMIRealGeometry; fall back to HMIGeometry (2D point/line WKB, feet)."""
    preferred = ("HMIRealGeometry", "RealGeometry")
    fallback = ("HMIGeometry", "Geometry")
    for name in preferred:
        col = _col(cols, name)
        blob = _row_blob(row, col)
        if blob is not None:
            return blob, name
    for name in fallback:
        col = _col(cols, name)
        blob = _row_blob(row, col)
        if blob is not None:
            return blob, name
    return None, None


def _attach_link_geometry(conduit, geom_row, geom_cols):
    """Fill conduit e_m/n_m/vertices_m from HMIRealGeometry, else HMIGeometry (B2)."""
    empty = {
        "vertices_m": [], "e_m": None, "n_m": None,
        "end_e_m": None, "end_n_m": None, "has_geometry": False,
    }
    blob, _src = _geometry_blob(geom_row, geom_cols)
    if blob is None:
        conduit.update(empty)
        return conduit
    geo = geometry_from_wkb_ft(blob)
    conduit["vertices_m"] = geo["vertices_m"]
    conduit["e_m"] = geo["e_m"]
    conduit["n_m"] = geo["n_m"]
    conduit["end_e_m"] = geo["end_e_m"]
    conduit["end_n_m"] = geo["end_n_m"]
    conduit["has_geometry"] = geo["has_geometry"]
    return conduit


def read_live_elements(cur, tables):
    """HMIModelingElement where IsDeleted = 0, with Label."""
    if not _has(tables, "HMIModelingElement"):
        return None
    cols = _cols(cur, "HMIModelingElement")
    id_col = _col(cols, "ElementID", "DomainElementID")
    lab_col = _col(cols, "Label")
    del_col = _col(cols, "IsDeleted")
    if not id_col or not lab_col:
        return None
    q = f'SELECT * FROM "HMIModelingElement"'
    if del_col:
        q += f' WHERE "{del_col}" = 0'
    elements = {}
    for r in cur.execute(q):
        try:
            eid = int(r[id_col])
        except (TypeError, ValueError, KeyError):
            continue
        elements[eid] = {
            "element_id": eid,
            "label": str(r[lab_col]) if r[lab_col] is not None else str(eid),
            "is_deleted": 0,
            "source": "model",
        }
    return elements


def _ground_map(cur, tables, base_alt):
    """Ground / node elevations from BaseNode / BaseDirectedNode / BaseIdahoNode (US ft → m)."""
    ground = {}
    for table, elev_names in (
        ("BaseNode_Physical_Data", ("Physical_GroundElevation", "Physical_RimElevation")),
        ("BaseDirectedNode_Physical_Data", ("Physical_GroundElevation", "Physical_RimElevation")),
        ("BaseIdahoNode_Physical_Data", ("Physical_NodeElevation", "Physical_GroundElevation")),
    ):
        if not _has(tables, table):
            continue
        rows, cols = _safe_index(cur, table, base_alt)
        elev_col = _col(cols, *elev_names)
        if not elev_col:
            continue
        for eid, r in rows.items():
            v = ft_to_m(_as_float(r[elev_col]))
            if v is not None and eid not in ground:
                ground[eid] = v
    return ground


# OpenFlows element-type tables (A3): Outfall = Outfall rows only — never topology sinks.
_NODE_TYPE_TABLES = (
    ("Outfall", "outfall"),
    ("Manhole", "manhole"),
    ("CatchBasin", "catch_basin"),
    ("Transition", "transition"),
    ("WetWell", "wet_well"),
    ("PressureJunction", "junction"),
    ("Tap", "tap"),
    ("Pump", "pump"),
    ("Reservoir", "reservoir"),
    ("Tank", "tank"),
)


def _element_type_map(cur, tables, element_ids):
    """Map element_id → OpenFlows type from typed tables (Outfall / Manhole / …)."""
    out = {}
    want = set(element_ids or [])
    for table, kind in _NODE_TYPE_TABLES:
        if not _has(tables, table):
            continue
        cols = _cols(cur, table)
        id_col = _col(cols, "DomainElementID", "ElementID")
        if not id_col:
            continue
        for r in cur.execute(f'SELECT * FROM "{table}"'):
            try:
                eid = int(r[id_col])
            except (TypeError, ValueError, KeyError):
                continue
            if want and eid not in want:
                continue
            # First match wins; Outfall listed first so it is never overwritten
            out.setdefault(eid, kind)
    return out


def _node_geometry_map(cur, tables, base_alt):
    """Node E/N from HMIRealGeometry, else HMIGeometry 2D point WKB in feet (A4/B2)."""
    geo = {}
    for table in (
        "BaseNode_HmiDataSetGeometry_Data",
        "BaseDirectedNode_HmiDataSetGeometry_Data",
        "BaseNode_HMIDataSetGeometry_Data",
        "BaseIdahoNode_HmiDataSetGeometry_Data",
    ):
        if not _has(tables, table):
            continue
        rows, cols = _safe_index(cur, table, base_alt)
        # Need at least one of the geometry columns
        if not any(_col(cols, n) for n in (
            "HMIRealGeometry", "RealGeometry", "HMIGeometry", "Geometry",
        )):
            continue
        for eid, r in rows.items():
            if eid in geo:
                continue
            blob, _src = _geometry_blob(r, cols)
            parsed = geometry_from_wkb_ft(blob)
            if parsed.get("has_geometry"):
                geo[eid] = {
                    "e_m": parsed["e_m"],
                    "n_m": parsed["n_m"],
                    "has_geometry": True,
                }
    return geo


def _crown_cover_m(ground_m, invert_m, diameter_mm):
    """Crown cover / depth-to-crown = GL − (IL + D)."""
    if ground_m is None or invert_m is None or diameter_mm is None:
        return None
    return float(ground_m) - (float(invert_m) + float(diameter_mm) / 1000.0)


def _alternatives_list(cur, tables, base_alt, all_alts):
    alts = []
    if _has(tables, "Alternatives"):
        cols = _cols(cur, "Alternatives")
        id_col = _col(cols, "AlternativeID")
        lab_col = _col(cols, "Label")
        if id_col:
            for r in cur.execute(
                f'SELECT * FROM "Alternatives" ORDER BY "{id_col}"'
            ):
                try:
                    aid = int(r[id_col])
                except (TypeError, ValueError):
                    continue
                alts.append({
                    "alternative_id": aid,
                    "label": r[lab_col] if lab_col else None,
                })
    if not alts:
        alts = [{"alternative_id": a, "label": "base" if a == base_alt else None}
                for a in all_alts]
    return alts


def read_sewer_network(cur, tables, elements, base_alt, network):
    """Conduit DomainElementID live in HMIModelingElement + physical / topology / geometry."""
    if not _has(tables, "Conduit"):
        return None
    conduit_ids = set()
    c_cols = _cols(cur, "Conduit")
    id_col = _col(c_cols, "DomainElementID", "ElementID")
    if not id_col:
        return None
    for r in cur.execute('SELECT * FROM "Conduit"'):
        try:
            eid = int(r[id_col])
        except (TypeError, ValueError):
            continue
        if eid in elements:
            conduit_ids.add(eid)
    if not conduit_ids:
        return None

    diam, diam_cols = _safe_index(
        cur, "Conduit_Physical_Data" if _has(tables, "Conduit_Physical_Data") else None,
        base_alt, required_cols=("ConduitDiameter",),
    )
    diam_col = _col(diam_cols, "ConduitDiameter")

    inv, inv_cols = _safe_index(
        cur,
        "PhysicalLink_Physical_Data" if _has(tables, "PhysicalLink_Physical_Data") else None,
        base_alt,
    )
    inv_s_col = _col(inv_cols, "Physical_UpstreamInvert")
    inv_e_col = _col(inv_cols, "Physical_DownstreamInvert")

    topo, topo_cols = _safe_index(
        cur,
        "BaseLink_HMIDataSetTopology_Data"
        if _has(tables, "BaseLink_HMIDataSetTopology_Data") else None,
        base_alt,
    )
    start_col = _col(topo_cols, "HMITopologyStartNodeID")
    stop_col = _col(topo_cols, "HMITopologyStopNodeID")

    geom, geom_cols = _safe_index(
        cur,
        "BaseLink_HmiDataSetGeometry_Data"
        if _has(tables, "BaseLink_HmiDataSetGeometry_Data") else None,
        base_alt,
    )
    len_col = _col(geom_cols, "HMIGeometryScaledLength")

    grav, grav_cols = _safe_index(
        cur,
        "GravityLinkBase_Physical_Data"
        if _has(tables, "GravityLinkBase_Physical_Data") else None,
        base_alt,
    )
    mat_col = _col(grav_cols, "Physical_Material", "Material", "material")
    n_col = _col(grav_cols, "Physical_ManningsN", "ManningsN")

    ground = _ground_map(cur, tables, base_alt)
    node_geo = _node_geometry_map(cur, tables, base_alt)
    conduits = []
    for eid in sorted(conduit_ids):
        el = elements[eid]
        d_ft = _as_float(diam[eid][diam_col]) if eid in diam and diam_col else None
        l_ft = _as_float(geom[eid][len_col]) if eid in geom and len_col else None
        inv_s_ft = _as_float(inv[eid][inv_s_col]) if eid in inv and inv_s_col else None
        inv_e_ft = _as_float(inv[eid][inv_e_col]) if eid in inv and inv_e_col else None
        d_mm = ft_to_mm(d_ft)
        length_m = ft_to_m(l_ft)
        inv_s_m = ft_to_m(inv_s_ft)
        inv_e_m = ft_to_m(inv_e_ft)
        drop = (inv_s_m - inv_e_m) if inv_s_m is not None and inv_e_m is not None else None
        start_id = stop_id = None
        if eid in topo:
            if start_col and topo[eid][start_col] is not None:
                start_id = int(topo[eid][start_col])
            if stop_col and topo[eid][stop_col] is not None:
                stop_id = int(topo[eid][stop_col])
        # A1: crown cover / depth at BOTH ends = GL − (IL + D)
        g_start = ground.get(start_id) if start_id is not None else None
        g_stop = ground.get(stop_id) if stop_id is not None else None
        cover_start = _crown_cover_m(g_start, inv_s_m, d_mm)
        cover_stop = _crown_cover_m(g_stop, inv_e_m, d_mm)
        covers = [v for v in (cover_start, cover_stop) if v is not None]
        # Legacy cover_m = deeper end (max depth-to-crown); both ends kept separately
        cover = max(covers) if covers else None
        material = None
        roughness = None
        if eid in grav:
            if mat_col:
                material = grav[eid][mat_col]
            if n_col:
                roughness = _as_float(grav[eid][n_col])
        start_label = (elements.get(start_id) or {}).get("label")
        stop_label = (elements.get(stop_id) or {}).get("label")
        c_row = {
            "id": el["label"],
            "element_id": eid,
            "network": network,
            "source": "model",
            "kind": "conduit",
            "diameter_mm": round(d_mm, 3) if d_mm is not None else None,
            "length_m": round(length_m, 6) if length_m is not None else None,
            "invert_start_m": round(inv_s_m, 6) if inv_s_m is not None else None,
            "invert_stop_m": round(inv_e_m, 6) if inv_e_m is not None else None,
            "slope_pct": (
                round(slope_pct(length_m, drop), 6)
                if slope_pct(length_m, drop) is not None else None
            ),
            "cover_m": round(cover, 6) if cover is not None else None,
            "cover_start_m": round(cover_start, 6) if cover_start is not None else None,
            "cover_stop_m": round(cover_stop, 6) if cover_stop is not None else None,
            "ground_start_m": round(g_start, 6) if g_start is not None else None,
            "ground_stop_m": round(g_stop, 6) if g_stop is not None else None,
            "material": material,
            "roughness": roughness,
            "roughness_type": "Manning" if roughness is not None else None,
            "start_node_id": start_id,
            "stop_node_id": stop_id,
            "start_label": start_label,
            "stop_label": stop_label,
            "alternative_id": _row_alt(diam.get(eid) or inv.get(eid) or geom.get(eid),
                                       diam_cols or inv_cols or geom_cols) or base_alt,
            "units_source": "US_feet",
        }
        _attach_link_geometry(c_row, geom.get(eid), geom_cols)
        conduits.append(c_row)

    used = {c["element_id"] for c in conduits}
    node_ids = set()
    for c in conduits:
        if c["start_node_id"] is not None:
            node_ids.add(c["start_node_id"])
        if c["stop_node_id"] is not None:
            node_ids.add(c["stop_node_id"])
    # Include orphan ground nodes so Outfall table rows are typed even without links
    type_ids = set(node_ids) | set(ground.keys()) | set(node_geo.keys())
    type_map = _element_type_map(cur, tables, type_ids)

    # Per-node max depth-to-crown from incident conduit ends (A1 MH reporting)
    node_depth = {}
    for c in conduits:
        for nid, cov in (
            (c.get("start_node_id"), c.get("cover_start_m")),
            (c.get("stop_node_id"), c.get("cover_stop_m")),
        ):
            if nid is None or cov is None:
                continue
            prev = node_depth.get(nid)
            if prev is None or cov > prev:
                node_depth[nid] = cov

    nodes = []
    for eid in sorted(node_ids):
        el = elements.get(eid) or {"element_id": eid, "label": str(eid)}
        # Prefer native node WKB (A4); fall back to incident conduit endpoints
        e_m = n_m = None
        has_node_geom = False
        ng = node_geo.get(eid)
        if ng and ng.get("e_m") is not None:
            e_m, n_m = ng["e_m"], ng["n_m"]
            has_node_geom = True
        else:
            for c in conduits:
                if c.get("start_node_id") == eid and c.get("e_m") is not None:
                    e_m, n_m = c["e_m"], c["n_m"]
                    break
                if c.get("stop_node_id") == eid and c.get("end_e_m") is not None:
                    e_m, n_m = c["end_e_m"], c["end_n_m"]
                    break
        et = type_map.get(eid) or "manhole"
        nodes.append({
            "element_id": eid,
            "label": el.get("label") or str(eid),
            "element_type": et,
            "e_m": e_m,
            "n_m": n_m,
            "ground_m": ground.get(eid),
            "depth_to_crown_m": (
                round(node_depth[eid], 6) if eid in node_depth else None
            ),
            "has_geometry": has_node_geom or e_m is not None,
            "source": "model",
        })
    # Orphan live elements (typed Outfalls / MHs with ground or geometry, no link)
    for eid, el in elements.items():
        if eid in used or eid in node_ids:
            continue
        et = type_map.get(eid)
        if eid not in ground and eid not in node_geo and et is None:
            continue
        ng = node_geo.get(eid) or {}
        nodes.append({
            "element_id": eid,
            "label": el["label"],
            "element_type": et or "node",
            "e_m": ng.get("e_m"),
            "n_m": ng.get("n_m"),
            "ground_m": ground.get(eid),
            "depth_to_crown_m": (
                round(node_depth[eid], 6) if eid in node_depth else None
            ),
            "has_geometry": bool(ng.get("has_geometry")),
            "source": "model",
        })
    return {
        "conduits": conduits,
        "nodes": nodes,
        "loads": [],
        "schema": "openflows_conduit",
        "physical_tables": [
            t for t in (
                "Conduit", "Conduit_Physical_Data", "PhysicalLink_Physical_Data",
                "BaseLink_HMIDataSetTopology_Data", "BaseLink_HmiDataSetGeometry_Data",
                "GravityLinkBase_Physical_Data",
            ) if _has(tables, t)
        ],
    }


def read_pressure_network(cur, tables, elements, base_alt, network):
    """IdahoPipe DomainElementID live in HMIModelingElement + IdahoPipe_Physical_Data."""
    if not _has(tables, "IdahoPipe"):
        return None
    pipe_ids = set()
    p_cols = _cols(cur, "IdahoPipe")
    id_col = _col(p_cols, "DomainElementID", "ElementID")
    if not id_col:
        return None
    for r in cur.execute('SELECT * FROM "IdahoPipe"'):
        try:
            eid = int(r[id_col])
        except (TypeError, ValueError):
            continue
        if eid in elements:
            pipe_ids.add(eid)
    if not pipe_ids:
        return None

    phys, phys_cols = _safe_index(
        cur,
        "IdahoPipe_Physical_Data" if _has(tables, "IdahoPipe_Physical_Data") else None,
        base_alt,
    )
    diam_col = _col(phys_cols, "Physical_PipeDiameter")
    mat_col = _col(phys_cols, "Physical_PipeMaterial", "Physical_Material")
    c_col = _col(phys_cols, "Physical_HazenWilliamsC")

    topo, topo_cols = _safe_index(
        cur,
        "BaseLink_HMIDataSetTopology_Data"
        if _has(tables, "BaseLink_HMIDataSetTopology_Data") else None,
        base_alt,
    )
    start_col = _col(topo_cols, "HMITopologyStartNodeID")
    stop_col = _col(topo_cols, "HMITopologyStopNodeID")

    geom, geom_cols = _safe_index(
        cur,
        "BaseLink_HmiDataSetGeometry_Data"
        if _has(tables, "BaseLink_HmiDataSetGeometry_Data") else None,
        base_alt,
    )
    len_col = _col(geom_cols, "HMIGeometryScaledLength")

    ground = _ground_map(cur, tables, base_alt)
    conduits = []
    for eid in sorted(pipe_ids):
        el = elements[eid]
        d_ft = _as_float(phys[eid][diam_col]) if eid in phys and diam_col else None
        l_ft = _as_float(geom[eid][len_col]) if eid in geom and len_col else None
        d_mm = ft_to_mm(d_ft)
        length_m = ft_to_m(l_ft)
        material = phys[eid][mat_col] if eid in phys and mat_col else None
        hw_c = _as_float(phys[eid][c_col]) if eid in phys and c_col else None
        start_id = stop_id = None
        if eid in topo:
            if start_col and topo[eid][start_col] is not None:
                start_id = int(topo[eid][start_col])
            if stop_col and topo[eid][stop_col] is not None:
                stop_id = int(topo[eid][stop_col])
        g_start = ground.get(start_id) if start_id is not None else None
        cover = None
        # pressure: cover ≈ ground − node elevation when available; else None
        if g_start is not None and start_id is not None and ground.get(start_id) is not None:
            # Node elevation used as centreline; without diameter soffit skip cover
            if d_mm is not None and g_start is not None:
                # Prefer Idaho node elevation as invert proxy when present
                cover = None
        c_row = {
            "id": el["label"],
            "element_id": eid,
            "network": network,
            "source": "model",
            "kind": "pipe",
            "diameter_mm": round(d_mm, 3) if d_mm is not None else None,
            "length_m": round(length_m, 6) if length_m is not None else None,
            "invert_start_m": None,
            "invert_stop_m": None,
            "slope_pct": None,
            "cover_m": cover,
            "material": material,
            "roughness": hw_c,
            "roughness_type": "HazenWilliams" if hw_c is not None else None,
            "start_node_id": start_id,
            "stop_node_id": stop_id,
            "start_label": (elements.get(start_id) or {}).get("label"),
            "stop_label": (elements.get(stop_id) or {}).get("label"),
            "alternative_id": _row_alt(phys.get(eid) or geom.get(eid),
                                       phys_cols or geom_cols) or base_alt,
            "units_source": "US_feet",
        }
        _attach_link_geometry(c_row, geom.get(eid), geom_cols)
        conduits.append(c_row)

    node_ids = set()
    for c in conduits:
        if c["start_node_id"] is not None:
            node_ids.add(c["start_node_id"])
        if c["stop_node_id"] is not None:
            node_ids.add(c["stop_node_id"])
    nodes = []
    for eid in sorted(node_ids):
        el = elements.get(eid) or {"element_id": eid, "label": str(eid)}
        e_m = n_m = None
        for c in conduits:
            if c.get("start_node_id") == eid and c.get("e_m") is not None:
                e_m, n_m = c["e_m"], c["n_m"]
                break
            if c.get("stop_node_id") == eid and c.get("end_e_m") is not None:
                e_m, n_m = c["end_e_m"], c["end_n_m"]
                break
        nodes.append({
            "element_id": eid,
            "label": el.get("label") or str(eid),
            "element_type": "junction",
            "e_m": e_m,
            "n_m": n_m,
            "ground_m": ground.get(eid),
            "source": "model",
        })
    return {
        "conduits": conduits,
        "nodes": nodes,
        "loads": [],
        "schema": "openflows_idaho_pipe",
        "physical_tables": [
            t for t in (
                "IdahoPipe", "IdahoPipe_Physical_Data",
                "BaseLink_HMIDataSetTopology_Data", "BaseLink_HmiDataSetGeometry_Data",
                "BaseIdahoNode_Physical_Data",
            ) if _has(tables, t)
        ],
    }


def read_openflows(cur, base_alt, all_alts, network):
    """Read real OpenFlows tables; returns None if HMIModelingElement missing."""
    tables = _tables(cur)
    elements = read_live_elements(cur, tables)
    if elements is None:
        return None
    data = None
    if network in ("sewer", "storm", "force_main"):
        data = read_sewer_network(cur, tables, elements, base_alt, network)
    if data is None and network in ("water", "irrigation"):
        data = read_pressure_network(cur, tables, elements, base_alt, network)
    if data is None:
        # Fall back: try the other family when --network mismatched the tables
        data = read_sewer_network(cur, tables, elements, base_alt, network)
        if data is None:
            data = read_pressure_network(cur, tables, elements, base_alt, network)
    if data is None:
        return None
    data["alternatives"] = _alternatives_list(cur, tables, base_alt, all_alts)
    data["warnings"] = []
    return data


def read_shp_points(path):
    """Minimal SHP / JSON reader for Co-17-style cross-check.

    JSON may carry D / L / invert attributes (metres / mm). Binary SHP uses DBF
    fields when present. Coordinates in JSON are metres; binary SHP may be feet.
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
                out.append(_attrs_from_props(props, coords[0], coords[1]))
            return out, "metres"
        pts = data if isinstance(data, list) else data.get("points") or data.get("conduits") or []
        out = []
        for item in pts:
            out.append(_attrs_from_props(item, item.get("e"), item.get("n")))
        return out, "metres"
    with p.open("rb") as f:
        header = f.read(100)
        if len(header) < 100:
            raise ValueError("SHP too short")
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
                try:
                    n_parts, n_points = struct.unpack("<2i", payload[36:44])
                    off = 44 + 4 * n_parts
                    if len(payload) >= off + 16:
                        x, y = struct.unpack("<2d", payload[off:off + 16])
                        records.append({"label": "", "e": x, "n": y, "record": _num})
                except struct.error:
                    pass
        dbf = p.with_suffix(".dbf")
        if dbf.is_file() and records:
            rows = _read_dbf_rows(dbf)
            for i, rec in enumerate(records):
                if i < len(rows):
                    rec.update(_attrs_from_props(rows[i], rec.get("e"), rec.get("n")))
        return records, "shp"


def _attrs_from_props(props, e=None, n=None):
    def g(*names):
        for name in names:
            if name in props and props[name] not in (None, ""):
                return props[name]
            for k, v in props.items():
                if str(k).casefold() == name.casefold() and v not in (None, ""):
                    return v
        return None

    label = g("label", "Label", "ID", "Name", "TEXT", "Handle")
    return {
        "label": str(label) if label is not None else "",
        "e": e if e is not None else _as_float(g("e", "E", "easting")),
        "n": n if n is not None else _as_float(g("n", "N", "northing")),
        "diameter_mm": _as_float(g("diameter_mm", "Diameter_mm", "D", "DIA", "DIAMETER")),
        "length_m": _as_float(g("length_m", "Length_m", "L", "LENGTH", "Shape_Leng")),
        "invert_start_m": _as_float(g(
            "invert_start_m", "InvertStart_m", "US_Invert", "UpInvert", "INVERT_US",
        )),
        "invert_stop_m": _as_float(g(
            "invert_stop_m", "InvertStop_m", "DS_Invert", "DnInvert", "INVERT_DS",
        )),
    }


def _read_dbf_rows(path):
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
        out = []
        for _ in range(n_records):
            raw = f.read(rec_len)
            if not raw:
                break
            data = raw[1:]
            row = {}
            pos = 0
            for name, _t, flen in fields:
                row[name] = data[pos:pos + flen].decode("latin1", "replace").strip()
                pos += flen
            out.append(row)
        return out


def cross_check_shp(nodes, conduits, shp_path, label, tol_m=0.05, tol_d_mm=0.5, tol_l_m=0.05):
    """DQ-3 SHP cross-check — NMAR Co-17: D / L / inverts (and E/N when present)."""
    pts, units = read_shp_points(shp_path)
    target = (label or "Co-17").casefold()
    matches = [p for p in pts if str(p.get("label") or "").casefold() == target]
    if not matches and len(pts) == 1 and not any(p.get("label") for p in pts):
        matches = pts
    model_c = [c for c in conduits if str(c.get("id") or "").casefold() == target]
    model_n = [n for n in nodes if str(n.get("label") or "").casefold() == target]
    result = {
        "label": label or "Co-17",
        "shp_count": len(matches),
        "model_count": len(model_c) or len(model_n),
        "tolerance_m": tol_m,
        "result": "not_run",
        "delta_m": None,
        "detail": None,
        "checks": {},
    }
    if not matches:
        result["result"] = "flag"
        result["detail"] = f"label {label} not found in SHP export"
        return result
    if not model_c and not model_n:
        result["result"] = "flag"
        result["detail"] = f"label {label} not found in model"
        return result
    sp = matches[0]
    flags = []
    if model_c:
        mc = model_c[0]
        for key, model_v, shp_v, tol, unit in (
            ("diameter_mm", mc.get("diameter_mm"), sp.get("diameter_mm"), tol_d_mm, "mm"),
            ("length_m", mc.get("length_m"), sp.get("length_m"), tol_l_m, "m"),
            ("invert_start_m", mc.get("invert_start_m"), sp.get("invert_start_m"), tol_m, "m"),
            ("invert_stop_m", mc.get("invert_stop_m"), sp.get("invert_stop_m"), tol_m, "m"),
        ):
            if model_v is None or shp_v is None:
                result["checks"][key] = "not_run"
                continue
            diff = abs(float(model_v) - float(shp_v))
            ok = diff <= tol + 1e-9
            result["checks"][key] = {
                "model": model_v, "shp": shp_v, "delta": round(diff, 6),
                "tol": tol, "unit": unit, "result": "pass" if ok else "flag",
            }
            if not ok:
                flags.append(f"{key} Δ={diff:.4f}{unit}")
    # Optional E/N when both sides have coordinates
    mp = model_c[0] if model_c else model_n[0]
    if (mp.get("e_m") is not None and mp.get("n_m") is not None
            and sp.get("e") is not None and sp.get("n") is not None):
        se, sn = float(sp["e"]), float(sp["n"])
        if units == "shp" and abs(se) < 1e5 and abs(sn) < 1e5:
            se, sn = ft_to_m(se), ft_to_m(sn)
        delta = math.hypot(se - float(mp["e_m"]), sn - float(mp["n_m"]))
        result["delta_m"] = round(delta, 6)
        result["shp_en"] = [se, sn]
        result["model_en"] = [mp["e_m"], mp["n_m"]]
        if delta > tol_m + 1e-9:
            flags.append(f"en Δ={delta:.4f}m")
    if any(v == "not_run" for v in result["checks"].values()) and not flags and not result["checks"]:
        result["result"] = "not_run"
        result["detail"] = "no comparable Co-17 attributes on SHP or model"
        return result
    result["result"] = "flag" if flags else "pass"
    result["detail"] = (
        "; ".join(flags) if flags
        else "Co-17 SHP attributes match model (D / L / inverts)"
    )
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
        data = read_openflows(cur, base_alt, all_alts, network)
        conn.close()
        if data is None:
            return fail(
                EXIT_INPUT,
                "unrecognised Bentley sqlite schema "
                "(need HMIModelingElement + Conduit or IdahoPipe)",
            )
        shp_check = None
        if a.shp:
            try:
                shp_check = cross_check_shp(
                    data.get("nodes") or [], data["conduits"], a.shp, a.label,
                )
            except ValueError as exc:
                return fail(EXIT_INPUT, str(exc))
        doc = build_doc(path, network, data, base_alt, all_alts, shp_check=shp_check)
        if a.site:
            doc["meta"]["site"] = a.site.upper()
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
