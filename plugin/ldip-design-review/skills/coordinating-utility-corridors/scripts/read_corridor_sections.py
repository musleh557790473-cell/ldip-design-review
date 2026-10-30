#!/usr/bin/env python3
"""Read utilities-corridor cross sections (skill 14, SPC-0006 Rev O §4.17).

Primary: ezdwg 0.16.0 **raw** INSERT route (DO-2 / G-O1) — never `to_dxf` for bands.
Fallback: PDF legend-colour stripes (pdfplumber); status capped at Probable.

Usage:
  python read_corridor_sections.py --dwg <file.dwg> --out <sections.json>
      [--site NMAR] [--utility-layers <utility_layers.json>] [--params <params.json>]
  python read_corridor_sections.py --pdf <file.pdf> --out <sections.json> [...]
  python read_corridor_sections.py --sections-json <prebuilt.json> --out <sections.json>

Exit: 0 done · 2 input · 3 missing package · 4 schema. No network, no shell.
"""
__version__ = "0.1.0"

import argparse
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corridor_common import (  # noqa: E402
    EXIT_DEP, EXIT_INPUT, EXIT_OK, PARAMS_PATH, LAYERS_PATH, atomic_write_json,
    band_from_insert, emit, fail, load_params, load_utility_layers, layer_short,
    main_guard, offset_to_curb, parse_section_label, path_ok, read_json,
    resolve_utility, side_of_cl,
)

EZDWG_PINNED = "0.16.0"
_ROW_DIM_RE = re.compile(
    r"(?P<m>\d+(?:\.\d+)?)\s*(?:m)?\s*(?:\(?\s*R\.?\s*O\.?\s*W\.?\s*\)?|RIGHT\s+OF\s+WAY)",
    re.I,
)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_ezdwg():
    try:
        import ezdwg  # noqa: F401
        from importlib.metadata import version
    except ImportError as exc:
        raise ImportError("ezdwg is not installed") from exc
    ver = version("ezdwg")
    if ver != EZDWG_PINNED:
        return ver, True
    return ver, False


def _layer_match(name, substrings):
    short = layer_short(name).casefold()
    full = (name or "").casefold()
    for s in substrings or []:
        sf = s.casefold()
        if sf in short or sf in full:
            return True
    return False


def _combine_parent_transform(local_x, local_y, parents):
    """Apply nested INSERT parent chain (translation + uniform-ish scale, rotation)."""
    x, y = float(local_x), float(local_y)
    for p in reversed(parents):
        px, py = p["x"], p["y"]
        sx, sy = p.get("sx", 1.0), p.get("sy", 1.0)
        rot = p.get("rot", 0.0)
        x, y = x * sx, y * sy
        if abs(rot) > 1e-12:
            c, s = math.cos(rot), math.sin(rot)
            x, y = c * x - s * y, s * x + c * y
        x, y = x + px, y + py
    return x, y


def _header_name_map(block_headers):
    """Map every block-header handle → name (names may repeat across handles)."""
    return {h: n for h, n in block_headers}


def _insert_target_map(path):
    """INSERT handle → target block-header handle (F2).

    Prefer `decode_insert_write_properties` (extrusion, block_header_handle, …).
    """
    from ezdwg.raw import decode_insert_write_properties
    targets = {}
    for row in decode_insert_write_properties(path):
        # (insert_handle, extrusion, block_header_handle, attribs)
        if len(row) >= 3:
            targets[row[0]] = row[2]
    return targets


def _build_owner_index(inserts, owners, block_headers, insert_targets):
    """Map insert handle → parent insert chain keyed by target header handle (F2)."""
    owner_of = {h: o for h, o in owners}
    header_name = _header_name_map(block_headers)
    insert_by_h = {}
    # header handle → inserts that target it
    targeting = defaultdict(list)
    for row in inserts:
        h, x, y, z, sx, sy, sz, rot, name = row
        tgt = insert_targets.get(h)
        insert_by_h[h] = {
            "handle": h, "x": x, "y": y, "z": z,
            "sx": sx, "sy": sy, "sz": sz, "rot": rot, "name": name,
            "owner": owner_of.get(h), "target": tgt,
        }
        if tgt is not None:
            targeting[tgt].append(h)

    def parent_chain(handle):
        """Walk INSERT owner → parent INSERT targeting that header (G1).

        On a repeated owner header, truncate to parents kept at the first visit
        and warn OWNER_CYCLE (ezdwg can report a false owner that loops).
        Stop at *Model_Space / *Paper_Space or a non-header owner.
        """
        chain = []
        # owner header → chain length after its parent was appended
        seen_at = {}
        owner = owner_of.get(handle)
        warnings = []
        while owner is not None:
            if owner in seen_at:
                chain = chain[: seen_at[owner]]
                warnings.append({
                    "code": "OWNER_CYCLE",
                    "detail": (
                        f"owner chain cycle at header {owner} "
                        f"('{header_name.get(owner)}'); dropped parents after "
                        "first visit"
                    ),
                    "block": header_name.get(owner),
                    "block_handle": owner,
                })
                break
            bname = header_name.get(owner)
            if bname is None:
                break  # owner is not a block header
            if bname.casefold() in ("*model_space", "*paper_space"):
                break
            parent_hs = targeting.get(owner) or []
            parents = [insert_by_h[ph] for ph in parent_hs if ph in insert_by_h]
            if not parents:
                break
            # R6: warn when this parent header is inserted more than once
            if len(parents) > 1:
                warnings.append({
                    "code": "MULTI_INSERT_PARENT",
                    "detail": (
                        f"block header {owner} ('{bname}') inserted "
                        f"{len(parents)} times; using handle {parents[0]['handle']}"
                    ),
                    "block": bname,
                    "block_handle": owner,
                    "count": len(parents),
                })
            parent = parents[0]
            chain.append({
                "x": parent["x"], "y": parent["y"],
                "sx": parent["sx"], "sy": parent["sy"], "rot": parent["rot"],
                "handle": parent["handle"], "name": parent["name"],
                "target": parent.get("target"),
            })
            seen_at[owner] = len(chain)
            owner = owner_of.get(parent["handle"])
        return chain, warnings

    return insert_by_h, parent_chain


def _lw_const_width(row):
    """LWPOLYLINE row: (handle, flags, points, bulges, widths, const_width)."""
    handle, flags, points, bulges, widths, const_width = row
    if const_width is not None:
        return float(const_width)
    if widths:
        vals = []
        for w in widths:
            if isinstance(w, (list, tuple)):
                vals.extend(float(x) for x in w)
            else:
                vals.append(float(w))
        if vals and max(vals) - min(vals) < 1e-6:
            return vals[0]
    return None


def _is_band_marker_lw(row, marker_x=-0.5, marker_w=1.0, tol=0.05):
    handle, flags, points, bulges, widths, const_width = row
    w = _lw_const_width(row)
    if w is None or abs(w - marker_w) > tol:
        return False
    if not points:
        return False
    xs = [float(p[0]) for p in points]
    return all(abs(x - marker_x) <= tol for x in xs)


def _band_block_handles(lw_rows, lw_owners, block_headers, params):
    """Block-header handles with exactly one width-1 marker LWPOLYLINE at x=−0.5.

    F1: anonymous `*U*` blocks are included. F2: keyed by handle, not name.
    """
    band_cfg = params.get("band") or {}
    marker_x = float(band_cfg.get("marker_local_x", -0.5))
    marker_w = float(band_cfg.get("marker_width", 1.0))
    owner_of = {h: o for h, o in lw_owners}
    markers_by_header = defaultdict(list)
    for row in lw_rows:
        if not _is_band_marker_lw(row, marker_x, marker_w):
            continue
        owner = owner_of.get(row[0])
        if owner is None:
            continue
        # Accept any header including anonymous *U* (F1) — no startswith("*") skip
        markers_by_header[owner].append(row[0])
    return {h for h, hs in markers_by_header.items() if len(hs) == 1}


def _near_vertical_segs(lw_rows, layer_names, ent_layers, substrings):
    """Near-vertical LWPOLYLINE segments → {x, y_mid, y_min, y_max, handle}."""
    segs = []
    for row in lw_rows:
        h, flags, points, bulges, widths, cw = row
        lname = layer_names.get(ent_layers.get(h), "")
        if not _layer_match(lname, substrings):
            continue
        if not points or len(points) < 2:
            continue
        xs_p = [float(p[0]) for p in points]
        ys_p = [float(p[1]) for p in points]
        if abs(max(xs_p) - min(xs_p)) > abs(max(ys_p) - min(ys_p)):
            continue  # horizontal-ish
        segs.append({
            "x": sum(xs_p) / len(xs_p),
            "y_mid": sum(ys_p) / len(ys_p),
            "y_min": min(ys_p), "y_max": max(ys_p),
            "handle": h,
        })
    return segs


def _near_vertical_line_segs(lines, layer_names, ent_layers, substrings):
    segs = []
    for h, x1, y1, z1, x2, y2, z2 in lines:
        lname = layer_names.get(ent_layers.get(h), "")
        if not _layer_match(lname, substrings):
            continue
        if abs(x2 - x1) > abs(y2 - y1):
            continue
        ys = (float(y1), float(y2))
        segs.append({
            "x": (float(x1) + float(x2)) / 2.0,
            "y_mid": (ys[0] + ys[1]) / 2.0,
            "y_min": min(ys), "y_max": max(ys),
            "handle": h,
        })
    return segs


def _collect_lane_dim_edges(path, layer_names, params):
    """Outer endpoints of DRIVING LANE / SC-RD-DIM linear dimensions."""
    from ezdwg.raw import (
        decode_dim_linear_entities, decode_object_entity_layer_handles,
    )
    curb = params.get("curb") or {}
    layer_subs = curb.get("lane_dim_layer_substrings") or ["SC-RD-DIM"]
    text_subs = curb.get("lane_dim_text_substrings") or ["DRIVING"]
    dims = list(decode_dim_linear_entities(path))
    if not dims:
        return []
    handles = [r[0] for r in dims]
    ent_layers = dict(decode_object_entity_layer_handles(path, handles))
    edges = []
    for row in dims:
        h, text, defpoint, p1, p2 = row[0], row[1], row[2], row[3], row[4]
        lname = layer_names.get(ent_layers.get(h), "")
        text_s = str(text or "")
        layer_ok = _layer_match(lname, layer_subs)
        text_ok = any(s.casefold() in text_s.casefold() for s in text_subs)
        try:
            meas = abs(float(p2[0]) - float(p1[0]))
        except (TypeError, ValueError, IndexError):
            meas = None
        length_ok = meas is not None and 2.0 <= meas <= 5.0
        # Accept: text mentions driving/lane, or SC-RD-DIM numeric lane-ish length
        if not (text_ok or (layer_ok and length_ok)):
            continue
        edges.append((float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]), meas, text_s))
    return edges


def read_dwg_raw(dwg_path, layers_map, params):
    ver, unpinned = _require_ezdwg()
    from ezdwg.raw import (
        decode_insert_entities, decode_insert_owner_handles, decode_layer_names,
        decode_object_entity_layer_handles, decode_line_entities,
        decode_text_entities, decode_mtext_entities, decode_block_header_names,
        decode_lwpolyline_entities, decode_lwpolyline_owner_handles,
        decode_attrib_entities,
    )

    path = str(Path(dwg_path).resolve())
    inserts = list(decode_insert_entities(path))
    owners = list(decode_insert_owner_handles(path))
    layer_names = dict(decode_layer_names(path))
    block_headers = list(decode_block_header_names(path))
    header_name = _header_name_map(block_headers)
    insert_targets = _insert_target_map(path)
    insert_by_h, parent_chain = _build_owner_index(
        inserts, owners, block_headers, insert_targets
    )

    lw_rows = list(decode_lwpolyline_entities(path))
    lw_owners = list(decode_lwpolyline_owner_handles(path))
    lw_handles = [r[0] for r in lw_rows]
    lw_layers = dict(decode_object_entity_layer_handles(path, lw_handles)) if lw_handles else {}

    band_handles = _band_block_handles(lw_rows, lw_owners, block_headers, params)
    band_names = {header_name[h] for h in band_handles if h in header_name}

    handles = [r[0] for r in inserts]
    ent_layers = dict(decode_object_entity_layer_handles(path, handles)) if handles else {}

    lines = list(decode_line_entities(path))
    line_handles = [r[0] for r in lines]
    line_layers = dict(decode_object_entity_layer_handles(path, line_handles)) if line_handles else {}

    curb_subs = (params.get("curb") or {}).get("limit_of_work_layer_substrings") or ["LIMIT OF WORK"]
    cl_subs = (params.get("curb") or {}).get("center_line_layer_substrings") or ["Center line"]

    # R2: LWPOLYLINE primary; LINE as supplemental — keep y for per-section filter (F3)
    curb_segs = _near_vertical_segs(lw_rows, layer_names, lw_layers, curb_subs)
    curb_segs += _near_vertical_line_segs(lines, layer_names, line_layers, curb_subs)
    cl_segs = _near_vertical_segs(lw_rows, layer_names, lw_layers, cl_subs)
    cl_segs += _near_vertical_line_segs(lines, layer_names, line_layers, cl_subs)

    lane_edges = _collect_lane_dim_edges(path, layer_names, params)

    # Texts / mtext / attrib for section labels (R3 — strict pattern)
    texts = []
    for row in decode_text_entities(path):
        h, text, insert = row[0], row[1], row[2]
        texts.append((h, text, insert[0], insert[1]))
    for row in decode_mtext_entities(path):
        h, text, insert = row[0], row[1], row[2]
        texts.append((h, text, insert[0], insert[1]))
    for row in decode_attrib_entities(path):
        # (handle, text, tag, ...)
        h, text = row[0], row[1]
        insert = row[4] if len(row) > 4 else (0.0, 0.0, 0.0)
        texts.append((h, text, float(insert[0]), float(insert[1])))

    labels = []
    for h, text, tx, ty in texts:
        lab = parse_section_label(text, strict=True)
        if not lab:
            continue
        labels.append({"label": lab, "x": tx, "y": ty, "handle": h, "text": text})

    warnings = []
    if unpinned:
        warnings.append({
            "code": "ENGINE_VERSION_UNPINNED",
            "detail": f"ezdwg {ver} installed; pinned {EZDWG_PINNED}",
        })
    # Band inserts: utility/tree layer AND band-block content by handle (R5 / F1 / F2)
    bands = []
    seen_chain_warn = set()
    for h, x, y, z, sx, sy, sz, rot, name in inserts:
        target = insert_targets.get(h)
        unresolved = target is None
        if unresolved:
            # Probe fallback: target not resolvable by handle — coords already world
            # when parent is (0,0) scale 1. Accept by name membership among band handles.
            if name not in band_names:
                continue
        elif target not in band_handles:
            continue
        lh = ent_layers.get(h)
        lname = layer_names.get(lh, "")
        meta = resolve_utility(lname, layers_map)
        if not meta:
            continue
        if unresolved:
            wkey = ("INSERT_TARGET_UNRESOLVED", name)
            if wkey not in seen_chain_warn:
                warnings.append({
                    "code": "INSERT_TARGET_UNRESOLVED",
                    "detail": (
                        f"INSERT {h} ('{name}') has no block-header target; "
                        "using world coords with parents (0,0) scale 1"
                    ),
                    "block": name,
                })
                seen_chain_warn.add(wkey)
            chain, chain_warns = [], []
            wx, wy, wsx = float(x), float(y), float(sx)
        else:
            chain, chain_warns = parent_chain(h)
            for w in chain_warns:
                key = (w.get("code"), w.get("block_handle"), w.get("block"))
                if key not in seen_chain_warn:
                    warnings.append(w)
                    seen_chain_warn.add(key)
            wx, wy = _combine_parent_transform(x, y, chain)
            wsx = float(sx)
            for p in reversed(chain):
                wsx *= float(p.get("sx", 1.0))
        x_min, x_max, centre, width = band_from_insert(wx, wsx)
        bands.append({
            "handle": format(h, "X"),
            "block": name,
            "block_handle": target,
            "layer": lname,
            "layer_short": layer_short(lname),
            "utility": meta["utility"],
            "class": meta.get("class"),
            "power": bool(meta.get("power") or meta["utility"] in (params.get("power") or [])),
            "c1_as": list(meta.get("c1_as") or []),
            "exclude_c3_c4": bool(meta.get("exclude_c3_c4")),
            "combined": bool(meta.get("combined")),
            "x_min": x_min, "x_max": x_max, "centre": centre, "width": width,
            "y": wy,
            "nested": bool(chain),
            "parent_chain": [p["handle"] for p in chain],
            "raw_x": x, "raw_sx": sx,
        })

    section_warnings = []
    sections = _group_sections(
        bands, labels, curb_segs, cl_segs, lane_edges, params, section_warnings
    )
    warnings.extend(section_warnings)

    return {
        "sections_version": "1.0",
        "route": "dwg_raw",
        "source": {
            "path": path, "ezdwg": ver, "insert_count": len(inserts),
            "band_insert_count": len(bands),
            "band_block_count": len(band_handles),
        },
        "status_cap": None,
        "sections": sections,
        "warnings": warnings,
        "meta": {"generated_at": _now(), "reader": f"read_corridor_sections/{__version__}"},
    }


def _cluster_by_y(bands, tol):
    if not bands:
        return []
    ordered = sorted(bands, key=lambda b: (b["y"], b["centre"]))
    clusters = [[ordered[0]]]
    for b in ordered[1:]:
        if abs(b["y"] - clusters[-1][0]["y"]) <= tol:
            clusters[-1].append(b)
        else:
            clusters.append([b])
    return clusters


def _split_x_gaps(band_list, gap_m):
    if not band_list:
        return []
    ordered = sorted(band_list, key=lambda b: b["centre"])
    groups = [[ordered[0]]]
    for b in ordered[1:]:
        prev = groups[-1][-1]
        gap = b["x_min"] - prev["x_max"]
        if gap > gap_m:
            groups.append([b])
        else:
            groups[-1].append(b)
    return groups


def _dedupe_xs(xs, tol=0.05):
    if not xs:
        return []
    out = []
    for x in sorted(xs):
        if not out or abs(x - out[-1]) > tol:
            out.append(x)
    return out


def _filter_segs_to_section(segs, sec_y, row_left, row_right, y_tol, x_pad=1.0):
    """Restrict LIMIT OF WORK / CL segs to section y-band and x-span (F3)."""
    out = []
    for s in segs or []:
        if abs(s["y_mid"] - sec_y) > y_tol:
            # Also accept if the vertical seg overlaps the section y
            if not (s["y_min"] - y_tol <= sec_y <= s["y_max"] + y_tol):
                continue
        x = s["x"]
        if row_left is not None and x < row_left - x_pad:
            continue
        if row_right is not None and x > row_right + x_pad:
            continue
        out.append(s)
    return out


def _lane_chain_side(start, pool, direction, lane_lo, lane_hi):
    """Walk outward from start through xs spaced lane_lo…lane_hi metres."""
    chain = [start]
    cur = start
    remaining = list(pool)
    while True:
        best = None
        for x in remaining:
            gap = (x - cur) * direction
            if lane_lo - 1e-6 <= gap <= lane_hi + 1e-6:
                if best is None or abs(x - cur) < abs(best - cur):
                    best = x
        if best is None:
            break
        chain.append(best)
        remaining = [x for x in remaining if x != best]
        cur = best
    return chain


def _pick_curbs(cl_x, row_left, row_right, curb_segs, lane_edges, sec_y, params):
    """Lane-chain curb faces per section (F3 / N1).

    Returns (curb_left, curb_right, curb_method, chain_left, chain_right).
    """
    if cl_x is None:
        return None, None, "none", [], []
    curb_cfg = params.get("curb") or {}
    lane_range = curb_cfg.get("lane_width_m") or [3.0, 3.65]
    lane_lo, lane_hi = float(lane_range[0]), float(lane_range[1])
    y_tol = float(curb_cfg.get("y_band_tol_m", 25.0))
    median_max = float(curb_cfg.get("median_max_from_cl_m", 3.0))
    # G3: 0.40 m median curb face pair — start at carriageway-facing edge
    median_pair = float(curb_cfg.get("median_face_pair_m", 0.40))
    coord_tol = float(params.get("coord_abs_m") or 0.05)
    pair_tol = max(coord_tol, 0.05)

    segs = _filter_segs_to_section(curb_segs, sec_y, row_left, row_right, y_tol)
    xs = _dedupe_xs([s["x"] for s in segs], tol=coord_tol)

    left = sorted([x for x in xs if x < cl_x - coord_tol], reverse=True)
    right = sorted([x for x in xs if x > cl_x + coord_tol])

    has_median = (
        bool(left) and bool(right)
        and (cl_x - left[0]) <= median_max + 1e-6
        and (right[0] - cl_x) <= median_max + 1e-6
    )

    if has_median:
        # Carriageway-facing edge of the ~0.40 m median pair (outer of the two)
        start_l = left[0]
        if (
            len(left) >= 2
            and abs((left[0] - left[1]) - median_pair) <= pair_tol
            and (cl_x - left[1]) <= median_max + 1e-6
        ):
            start_l = left[1]
        start_r = right[0]
        if (
            len(right) >= 2
            and abs((right[1] - right[0]) - median_pair) <= pair_tol
            and (right[1] - cl_x) <= median_max + 1e-6
        ):
            start_r = right[1]
        chain_l = _lane_chain_side(
            start_l, [x for x in left if x < start_l - coord_tol], -1, lane_lo, lane_hi
        )
        chain_r = _lane_chain_side(
            start_r, [x for x in right if x > start_r + coord_tol], +1, lane_lo, lane_hi
        )
        curb_left = chain_l[-1] if chain_l else None
        curb_right = chain_r[-1] if chain_r else None
        method = "lane_chain" if (curb_left is not None and curb_right is not None) else "none"
        if method == "lane_chain":
            return curb_left, curb_right, method, chain_l, chain_r
    else:
        # Start at CL; first LOW must sit one lane-width out
        chain_l = _lane_chain_side(cl_x, left, -1, lane_lo, lane_hi)
        chain_r = _lane_chain_side(cl_x, right, +1, lane_lo, lane_hi)
        # Drop the CL seed from reported chain xs
        report_l = chain_l[1:] if len(chain_l) > 1 else []
        report_r = chain_r[1:] if len(chain_r) > 1 else []
        curb_left = report_l[-1] if report_l else None
        curb_right = report_r[-1] if report_r else None
        if curb_left is not None and curb_right is not None:
            return curb_left, curb_right, "lane_chain", report_l, report_r

    # Fallback: lane-dimension outer edges inside this section's span (N1 lane_dims)
    lane_left = lane_right = None
    for x1, y1, x2, y2, meas, text in lane_edges or []:
        ym = (y1 + y2) / 2.0
        if abs(ym - sec_y) > y_tol and abs(y1 - sec_y) > y_tol and abs(y2 - sec_y) > y_tol:
            continue
        for xv in (x1, x2):
            if row_left is not None and xv < row_left - 1.0:
                continue
            if row_right is not None and xv > row_right + 1.0:
                continue
            if xv < cl_x:
                lane_left = xv if lane_left is None else min(lane_left, xv)
            elif xv > cl_x:
                lane_right = xv if lane_right is None else max(lane_right, xv)

    def nearest_low(target, pool):
        if target is None or not pool:
            return None
        return min(pool, key=lambda x: abs(x - target))

    fb_l = nearest_low(lane_left, left) if lane_left is not None else None
    fb_r = nearest_low(lane_right, right) if lane_right is not None else None
    if fb_l is not None and fb_r is not None:
        return fb_l, fb_r, "lane_dims", [fb_l], [fb_r]
    return None, None, "none", [], []


def _cl_in_band_span(row_left, row_right, cl_segs, sec_y, y_tol, pad=1.0):
    """F2: band span must contain a Center line within ±1 m; else skip section."""
    cl_near = _filter_segs_to_section(
        cl_segs, sec_y, row_left - pad, row_right + pad, y_tol, x_pad=pad
    )
    if not cl_near:
        # Also try without x filter — CL might sit just inside
        cl_near = [
            s for s in (cl_segs or [])
            if abs(s["y_mid"] - sec_y) <= y_tol
            or (s["y_min"] - y_tol <= sec_y <= s["y_max"] + y_tol)
        ]
    if not cl_near:
        return False, None, "no Center line in section y-band"
    mid = (row_left + row_right) / 2.0
    cl_x = min((s["x"] for s in cl_near), key=lambda x: abs(x - mid))
    if not (row_left - pad <= cl_x <= row_right + pad):
        return False, cl_x, (
            f"Center line x={cl_x:.3f} outside band span "
            f"[{row_left:.3f},{row_right:.3f}] ±{pad} m"
        )
    return True, cl_x, None


def _label_fits_section(lab, row_left, row_right, sec_bottom, x_pad, below_min, below_max):
    """G2: label x in section span ±pad; label 0–8 m below section bottom."""
    if not (row_left - x_pad - 1e-9 <= lab["x"] <= row_right + x_pad + 1e-9):
        return False
    below = sec_bottom - lab["y"]
    return below_min - 1e-9 <= below <= below_max + 1e-9


def _assign_labels_global(pending, label_list, params):
    """G2: global closest-first section↔label pairs (x-span + below filter)."""
    lab_cfg = params.get("section_label") or {}
    x_pad = float(lab_cfg.get("x_span_pad_m", 2.0))
    below_min = float(lab_cfg.get("below_min_m", 0.0))
    below_max = float(lab_cfg.get("below_max_m", 8.0))

    pairs = []
    for si, pend in enumerate(pending):
        for li, lab in enumerate(label_list):
            if not _label_fits_section(
                lab, pend["row_left"], pend["row_right"], pend["gy"],
                x_pad, below_min, below_max,
            ):
                continue
            dist = math.hypot(lab["x"] - pend["gcx"], lab["y"] - pend["gy"])
            pairs.append((dist, abs(lab["y"] - pend["gy"]), abs(lab["x"] - pend["gcx"]),
                          si, li))
    pairs.sort()
    used_sec, used_lab = set(), set()
    assigned = {}
    for _dist, _dy, _dx, si, li in pairs:
        if si in used_sec or li in used_lab:
            continue
        used_sec.add(si)
        used_lab.add(li)
        assigned[si] = label_list[li]
    return assigned


def _group_sections(bands, labels, curb_segs, cl_segs, lane_edges, params,
                    section_warnings=None):
    """Group by insert y + x-gaps > 3 m; attach ^[AB]\\d+(-\\d+)?$ labels (R3/R4/G2)."""
    if section_warnings is None:
        section_warnings = []
    band_cfg = params.get("band") or {}
    curb_cfg = params.get("curb") or {}
    y_tol = float(band_cfg.get("y_cluster_tol_m", 1.0))
    gap_m = float(band_cfg.get("x_gap_split_m", 3.0))
    coord_tol = float(params.get("coord_abs_m") or 0.05)
    curb_y_tol = float(curb_cfg.get("y_band_tol_m", 25.0))

    # Deduplicate labels by value, keep first
    by_label = {}
    for lab in labels:
        by_label.setdefault(lab["label"], lab)
    label_list = list(by_label.values())

    clusters = []
    for y_group in _cluster_by_y(bands, y_tol):
        clusters.extend(_split_x_gaps(y_group, gap_m))

    pending = []
    for group in clusters:
        if not group:
            continue
        gy = sum(b["y"] for b in group) / len(group)
        gcx = sum(b["centre"] for b in group) / len(group)
        row_left = min(b["x_min"] for b in group)
        row_right = max(b["x_max"] for b in group)
        mid_row = (row_left + row_right) / 2.0

        # F2: band span must lie within ±1 m of Center line (CL inside span)
        ok, cl_near, span_detail = _cl_in_band_span(
            row_left, row_right, cl_segs, gy, curb_y_tol, pad=1.0
        )
        if not ok:
            section_warnings.append({
                "code": "SECTION_SPAN_MISMATCH",
                "detail": span_detail or "band span vs Center line mismatch",
                "row_left": row_left, "row_right": row_right,
                "y": gy, "band_count": len(group),
            })
            continue

        # CL = mid-ROW, cross-checked with Center line inside span (R2)
        cl_x = mid_row
        in_span = [
            s["x"] for s in _filter_segs_to_section(
                cl_segs, gy, row_left, row_right, curb_y_tol, x_pad=0.5
            )
        ]
        if not in_span and cl_near is not None:
            in_span = [cl_near]
        cl_line = None
        if in_span:
            cl_line = min(in_span, key=lambda x: abs(x - mid_row))
            if abs(cl_line - mid_row) <= coord_tol:
                cl_x = cl_line
            else:
                cl_x = mid_row

        # F3 lane chain starts at the Center line entity when present (not mid-ROW
        # when they disagree on asymmetric synthetic / probe sheets).
        chain_origin = cl_line if cl_line is not None else cl_x
        curb_left, curb_right, curb_method, chain_l, chain_r = _pick_curbs(
            chain_origin, row_left, row_right, curb_segs, lane_edges, gy, params
        )

        # R4: each band its own row — no same-utility merge
        band_rows = []
        side_index = defaultdict(int)
        for b in sorted(group, key=lambda r: r["centre"]):
            side = side_of_cl(b["centre"], cl_x) or "right"
            off, side2 = offset_to_curb(b["centre"], curb_left, curb_right, cl_x)
            if side2:
                side = side2
            idx = side_index[(b["utility"], side)]
            side_index[(b["utility"], side)] += 1
            band_rows.append({
                "utility": b["utility"],
                "side": side,
                "index": idx,
                "x_min": b["x_min"], "x_max": b["x_max"],
                "centre": b["centre"], "width": b["width"],
                "offset_to_curb": off,
                "layer": b["layer"], "handle": b["handle"],
                "block": b.get("block"),
                "block_handle": b.get("block_handle"),
                "class": b["class"], "power": b["power"],
                "c1_as": b.get("c1_as") or [],
                "exclude_c3_c4": bool(b.get("exclude_c3_c4")),
                "combined": bool(b.get("combined")),
                "nested": b.get("nested"),
            })

        # Presence map (first band per utility — for legacy consumers)
        utilities = {}
        for br in band_rows:
            utilities.setdefault(br["utility"], {
                k: br[k] for k in (
                    "x_min", "x_max", "centre", "width", "offset_to_curb", "side",
                    "layer", "handle", "class", "power", "c1_as", "exclude_c3_c4",
                    "combined", "index",
                )
            })

        pending.append({
            "gy": gy, "gcx": gcx,
            "cl_x": cl_x,
            "curb_left": curb_left, "curb_right": curb_right,
            "curb_method": curb_method,
            "curb_chain_left": list(chain_l), "curb_chain_right": list(chain_r),
            "row_left": row_left, "row_right": row_right,
            "band_rows": band_rows, "utilities": utilities,
        })

    # G2: global closest-first label assignment (x-span ±2 m, 0–8 m below)
    assigned = _assign_labels_global(pending, label_list, params)

    sections = []
    for si, pend in enumerate(pending):
        lab = assigned.get(si)
        if lab is None:
            label = f"UNLABELED-{si + 1}"
            lab = {
                "label": label, "x": pend["gcx"], "y": pend["gy"],
                "handle": None, "text": label,
            }
        else:
            label = lab["label"]
        sections.append({
            "label": label,
            "cl_x": pend["cl_x"],
            "curb_left": pend["curb_left"],
            "curb_right": pend["curb_right"],
            "curb_method": pend["curb_method"],
            "curb_chain_left": pend["curb_chain_left"],
            "curb_chain_right": pend["curb_chain_right"],
            "row_left": pend["row_left"],
            "row_right": pend["row_right"],
            "row_width": round(pend["row_right"] - pend["row_left"], 6),
            "bands": pend["band_rows"],
            "utilities": pend["utilities"],
            "band_count": len(pend["band_rows"]),
            "label_handle": lab.get("handle"),
            "label_text": lab.get("text"),
            "curb_found": (
                pend["curb_left"] is not None and pend["curb_right"] is not None
            ),
        })

    # Stable order by label then y
    sections.sort(key=lambda s: (s["label"], s.get("cl_x") or 0))
    return sections


def _pdf_legend_colours(page):
    """Map legend label text → RGB from nearby filled swatches (R1 / R8)."""
    # Heuristic: coloured small rects near text containing utility keywords
    keywords = {
        "potable": "water", "water": "water", "pw": "water",
        "storm": "storm", "sewer": "sewer", "foul": "sewer",
        "irrigation": "irrigation", "telecom": "telecom",
        "street lighting": "street_lighting", "lighting": "street_lighting",
        "pedestrian": "street_lighting",
        "electrical (lv)": "lv", "electrical (mv)": "mv", "electrical (hv)": "hv",
        "(lv)": "lv", "(mv)": "mv", "(hv)": "hv",
        "house connection": "house_connection",
    }
    words = page.extract_words() or []
    rects = [r for r in (page.rects or []) if r.get("fill")]
    rgb_to_u = {}
    for w in words:
        t = (w.get("text") or "").casefold()
        util = None
        for key, u in keywords.items():
            if key in t:
                util = u
                break
        if not util:
            continue
        wx, wy = float(w["x0"]), float(w["top"])
        best, best_d = None, 1e18
        for fig in rects:
            fx = (float(fig["x0"]) + float(fig["x1"])) / 2.0
            fy = (float(fig["top"]) + float(fig["bottom"])) / 2.0
            d = (fx - wx) ** 2 + (fy - wy) ** 2
            if d < best_d:
                best, best_d = fig, d
        if best is None or best_d > 80 ** 2:
            continue
        fill = best.get("non_stroking_color")
        rgb = _as_rgb(fill)
        if rgb:
            rgb_to_u[rgb] = util
    return rgb_to_u


def _as_rgb(fill):
    if fill is None:
        return None
    if isinstance(fill, (int, float)):
        return (int(fill * 255),) * 3 if fill <= 1 else (int(fill),) * 3
    if isinstance(fill, (list, tuple)):
        if len(fill) == 1:
            v = fill[0]
            return (int(v * 255),) * 3 if v <= 1 else (int(v),) * 3
        vals = list(fill[:3])
        if all(isinstance(v, (int, float)) and v <= 1 for v in vals):
            return tuple(int(v * 255) for v in vals)
        return tuple(int(v) for v in vals)
    return None


def _parse_row_m(text):
    m = _ROW_DIM_RE.search(text or "")
    if m:
        return float(m.group("m"))
    return None


def read_pdf_fallback(pdf_path, layers_map, params):
    """PDF legend-colour route — Probable only (DO-2). Stripe clusters + ROW scale (R8)."""
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError("pdfplumber is not installed") from exc

    path = str(Path(pdf_path).resolve())
    sections = []
    page_count = 0
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        for page_i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            rgb_to_u = _pdf_legend_colours(page)
            # Fall back to empty — no fixed table (R1)
            stripes = []
            for fig in list(page.rects or []) + list(page.curves or []):
                if not fig.get("fill"):
                    continue
                rgb = _as_rgb(fig.get("non_stroking_color"))
                if not rgb:
                    continue
                util = rgb_to_u.get(rgb)
                if not util:
                    best, best_d = None, 1e18
                    for key, u in rgb_to_u.items():
                        d = sum((a - b) ** 2 for a, b in zip(rgb, key))
                        if d < best_d:
                            best, best_d = u, d
                    if best is not None and best_d <= 40 ** 2:
                        util = best
                if not util:
                    continue
                x0, x1 = float(fig["x0"]), float(fig["x1"])
                y0, y1 = float(fig.get("top", fig.get("y0", 0))), float(
                    fig.get("bottom", fig.get("y1", 0))
                )
                stripes.append({
                    "utility": util,
                    "x_min": min(x0, x1), "x_max": max(x0, x1),
                    "y": (y0 + y1) / 2.0,
                    "page": page_i,
                })

            if not stripes:
                # Still emit empty section if a label is present
                lab = parse_section_label(text, strict=False)
                if lab:
                    sections.append({
                        "label": lab, "cl_x": 0.0,
                        "curb_left": None, "curb_right": None,
                        "bands": [], "utilities": {}, "band_count": 0,
                        "page": page_i, "row_width": None,
                    })
                continue

            # Cluster stripes by y, then x-gaps (same as DWG R3)
            y_tol = 8.0  # PDF points
            stripes.sort(key=lambda s: (s["y"], s["x_min"]))
            y_groups = [[stripes[0]]]
            for s in stripes[1:]:
                if abs(s["y"] - y_groups[-1][0]["y"]) <= y_tol:
                    y_groups[-1].append(s)
                else:
                    y_groups.append([s])

            row_m_page = _parse_row_m(text)
            for yg in y_groups:
                yg = sorted(yg, key=lambda s: s["x_min"])
                # split large x gaps in PDF points
                parts = [[yg[0]]]
                for s in yg[1:]:
                    if s["x_min"] - parts[-1][-1]["x_max"] > 40:
                        parts.append([s])
                    else:
                        parts[-1].append(s)
                for part in parts:
                    span = max(s["x_max"] for s in part) - min(s["x_min"] for s in part)
                    if span <= 0:
                        continue
                    row_m = row_m_page
                    # Prefer a nearby dimension word cluster — use page-level ROW
                    scale = (row_m / span) if row_m else 1.0
                    label = parse_section_label(text, strict=False) or f"P{page_i}"
                    bands = []
                    utilities = {}
                    for s in part:
                        x_min = s["x_min"] * scale
                        x_max = s["x_max"] * scale
                        # Re-centre around mid of stripe group so CL≈0
                        centre = (x_min + x_max) / 2.0
                        width = x_max - x_min
                        meta = resolve_utility(s["utility"], layers_map) or {
                            "utility": s["utility"],
                            "class": "wet" if s["utility"] in (params.get("wet") or []) else "dry",
                            "power": s["utility"] in (params.get("power") or []),
                        }
                        rec = {
                            "utility": meta.get("utility") or s["utility"],
                            "side": "right" if centre >= 0 else "left",
                            "index": 0,
                            "x_min": x_min, "x_max": x_max, "centre": centre, "width": width,
                            "offset_to_curb": None,
                            "layer": f"pdf:{s['utility']}", "handle": f"page{page_i}:{s['utility']}",
                            "class": meta.get("class"),
                            "power": bool(meta.get("power")),
                            "c1_as": list(meta.get("c1_as") or []),
                            "exclude_c3_c4": bool(meta.get("exclude_c3_c4")),
                            "page": page_i,
                        }
                        bands.append(rec)
                        utilities.setdefault(rec["utility"], rec)
                    # Shift so mid-ROW is at 0
                    mid = (min(b["x_min"] for b in bands) + max(b["x_max"] for b in bands)) / 2.0
                    for b in bands:
                        b["x_min"] -= mid
                        b["x_max"] -= mid
                        b["centre"] -= mid
                        b["side"] = "right" if b["centre"] >= 0 else "left"
                    row_left = min(b["x_min"] for b in bands)
                    row_right = max(b["x_max"] for b in bands)
                    sections.append({
                        "label": label,
                        "cl_x": 0.0,
                        "curb_left": None, "curb_right": None,
                        "row_left": row_left, "row_right": row_right,
                        "row_width": round(row_right - row_left, 6),
                        "bands": bands, "utilities": utilities,
                        "band_count": len(bands),
                        "page": page_i,
                        "pdf_scale": scale, "row_m_from_text": row_m,
                        "curb_found": False,
                    })

    return {
        "sections_version": "1.0",
        "route": "pdf",
        "source": {"path": path, "page_count": page_count},
        "status_cap": "Probable",
        "sections": sections,
        "warnings": [{"code": "PDF_FALLBACK",
                      "detail": "PDF legend-colour route; statuses capped at Probable (DO-2)"}],
        "meta": {"generated_at": _now(), "reader": f"read_corridor_sections/{__version__}"},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read corridor cross-section bands (skill 14)")
    ap.add_argument("--dwg", help="Corridor section DWG (raw INSERT route)")
    ap.add_argument("--pdf", help="PDF fallback (legend colours; Probable)")
    ap.add_argument("--sections-json", help="Pass-through prebuilt sections JSON")
    ap.add_argument("--out", required=True, help="Output sections.json")
    ap.add_argument("--site", default=None)
    ap.add_argument("--utility-layers", default=str(LAYERS_PATH))
    ap.add_argument("--params", default=str(PARAMS_PATH))
    a = ap.parse_args(argv)

    def run():
        if not path_ok(a.out):
            raise ValueError(f"path longer than 250 characters: {a.out}")
        params = load_params(a.params)
        layers_map = load_utility_layers(a.utility_layers, site=a.site)

        if a.sections_json:
            doc = read_json(a.sections_json)
            if "sections" not in doc:
                raise ValueError("sections-json must contain a sections array")
            doc.setdefault("sections_version", "1.0")
            doc.setdefault("meta", {})["generated_at"] = _now()
            atomic_write_json(a.out, doc)
            emit({"status": "ok", "route": doc.get("route"),
                  "sections": len(doc.get("sections") or []), "out": str(a.out)})
            return EXIT_OK

        if a.dwg:
            if not Path(a.dwg).is_file():
                raise ValueError(f"DWG not found: {a.dwg}")
            doc = read_dwg_raw(a.dwg, layers_map, params)
        elif a.pdf:
            if not Path(a.pdf).is_file():
                raise ValueError(f"PDF not found: {a.pdf}")
            doc = read_pdf_fallback(a.pdf, layers_map, params)
        else:
            raise ValueError("provide --dwg, --pdf, or --sections-json")

        atomic_write_json(a.out, doc)
        emit({
            "status": "ok",
            "route": doc["route"],
            "sections": len(doc.get("sections") or []),
            "band_inserts": (doc.get("source") or {}).get("band_insert_count"),
            "status_cap": doc.get("status_cap"),
            "out": str(a.out),
        })
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
