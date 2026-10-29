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


def _build_owner_index(inserts, owners, block_headers):
    """Map insert handle → parent insert chain (for nested block refs)."""
    owner_of = {h: o for h, o in owners}
    header_name = {}
    for h, n in block_headers:
        header_name.setdefault(h, n)
    by_name = defaultdict(list)
    insert_by_h = {}
    for row in inserts:
        h, x, y, z, sx, sy, sz, rot, name = row
        insert_by_h[h] = {
            "handle": h, "x": x, "y": y, "z": z,
            "sx": sx, "sy": sy, "sz": sz, "rot": rot, "name": name,
            "owner": owner_of.get(h),
        }
        if name:
            by_name[name].append(h)

    def parent_chain(handle):
        chain = []
        seen = set()
        owner = owner_of.get(handle)
        warnings = []
        while owner is not None and owner not in seen:
            seen.add(owner)
            bname = header_name.get(owner)
            if not bname:
                break
            parents = [insert_by_h[ph] for ph in by_name.get(bname, []) if ph in insert_by_h]
            if not parents:
                break
            # R6: warn only when this *parent container* block is inserted more than once
            if len(parents) > 1:
                warnings.append({
                    "code": "MULTI_INSERT_PARENT",
                    "detail": f"block '{bname}' inserted {len(parents)} times; "
                              f"using handle {parents[0]['handle']}",
                    "block": bname,
                    "count": len(parents),
                })
            parent = parents[0]
            chain.append({
                "x": parent["x"], "y": parent["y"],
                "sx": parent["sx"], "sy": parent["sy"], "rot": parent["rot"],
                "handle": parent["handle"], "name": parent["name"],
            })
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


def _band_block_names(lw_rows, lw_owners, block_headers, params):
    """Block names whose definition has one LWPOLYLINE width 1 at x=−0.5 (R5)."""
    band_cfg = params.get("band") or {}
    marker_x = float(band_cfg.get("marker_local_x", -0.5))
    marker_w = float(band_cfg.get("marker_width", 1.0))
    header_name = {}
    for h, n in block_headers:
        header_name.setdefault(h, n)
    owner_of = {h: o for h, o in lw_owners}
    markers_by_block = defaultdict(list)
    for row in lw_rows:
        if not _is_band_marker_lw(row, marker_x, marker_w):
            continue
        owner = owner_of.get(row[0])
        bname = header_name.get(owner)
        if bname and not str(bname).startswith("*"):
            markers_by_block[bname].append(row[0])
    # Require exactly one marker LWPOLYLINE in the block
    return {n for n, hs in markers_by_block.items() if len(hs) == 1}


def _near_vertical_xs(lw_rows, layer_names, ent_layers, substrings):
    """Collect x midpoints of near-vertical LWPOLYLINES on matching layers."""
    xs = []
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
        xs.append(sum(xs_p) / len(xs_p))
    return xs


def _near_vertical_line_xs(lines, layer_names, ent_layers, substrings):
    xs = []
    for h, x1, y1, z1, x2, y2, z2 in lines:
        lname = layer_names.get(ent_layers.get(h), "")
        if not _layer_match(lname, substrings):
            continue
        if abs(x2 - x1) > abs(y2 - y1):
            continue
        xs.append((x1 + x2) / 2.0)
    return xs


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
    insert_by_h, parent_chain = _build_owner_index(
        inserts, owners, block_headers
    )

    lw_rows = list(decode_lwpolyline_entities(path))
    lw_owners = list(decode_lwpolyline_owner_handles(path))
    lw_handles = [r[0] for r in lw_rows]
    lw_layers = dict(decode_object_entity_layer_handles(path, lw_handles)) if lw_handles else {}

    band_blocks = _band_block_names(lw_rows, lw_owners, block_headers, params)

    handles = [r[0] for r in inserts]
    ent_layers = dict(decode_object_entity_layer_handles(path, handles)) if handles else {}

    lines = list(decode_line_entities(path))
    line_handles = [r[0] for r in lines]
    line_layers = dict(decode_object_entity_layer_handles(path, line_handles)) if line_handles else {}

    curb_subs = (params.get("curb") or {}).get("limit_of_work_layer_substrings") or ["LIMIT OF WORK"]
    cl_subs = (params.get("curb") or {}).get("center_line_layer_substrings") or ["Center line"]
    title_subs = (params.get("section_label") or {}).get("layer_substrings") or ["DWG_TITLE"]

    # R2: LWPOLYLINE primary; LINE as supplemental
    curb_xs = _near_vertical_xs(lw_rows, layer_names, lw_layers, curb_subs)
    curb_xs += _near_vertical_line_xs(lines, layer_names, line_layers, curb_subs)
    cl_xs = _near_vertical_xs(lw_rows, layer_names, lw_layers, cl_subs)
    cl_xs += _near_vertical_line_xs(lines, layer_names, line_layers, cl_subs)

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

    text_handles = [t[0] for t in texts]
    text_layers = dict(decode_object_entity_layer_handles(path, text_handles)) if text_handles else {}
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
    # Band inserts: utility/tree layer AND band-block content (R5)
    bands = []
    seen_chain_warn = set()
    for h, x, y, z, sx, sy, sz, rot, name in inserts:
        if name not in band_blocks:
            continue
        lh = ent_layers.get(h)
        lname = layer_names.get(lh, "")
        meta = resolve_utility(lname, layers_map)
        if not meta:
            continue
        chain, chain_warns = parent_chain(h)
        for w in chain_warns:
            key = (w.get("code"), w.get("block"))
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

    sections = _group_sections(
        bands, labels, curb_xs, cl_xs, lane_edges, params
    )

    return {
        "sections_version": "1.0",
        "route": "dwg_raw",
        "source": {
            "path": path, "ezdwg": ver, "insert_count": len(inserts),
            "band_insert_count": len(bands),
            "band_block_count": len(band_blocks),
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


def _pick_curbs(cl_x, row_left, row_right, curb_xs, lane_edges, sec_y, y_tol=25.0):
    """LIMIT OF WORK at outer edge of driving lanes (R2)."""
    if cl_x is None:
        return None, None
    # Lane-dimension outer edges near this section
    lane_left = lane_right = None
    for x1, y1, x2, y2, meas, text in lane_edges or []:
        ym = (y1 + y2) / 2.0
        if abs(ym - sec_y) > y_tol and abs(y1 - sec_y) > y_tol and abs(y2 - sec_y) > y_tol:
            continue
        for xv in (x1, x2):
            if xv < cl_x:
                lane_left = xv if lane_left is None else min(lane_left, xv)
            elif xv > cl_x:
                lane_right = xv if lane_right is None else max(lane_right, xv)
    # Candidate LIMIT OF WORK xs inside / near ROW
    cands = []
    for x in curb_xs or []:
        if row_left is not None and row_right is not None:
            if x < row_left - 1.0 or x > row_right + 1.0:
                continue
        cands.append(x)
    if not cands:
        cands = list(curb_xs or [])

    def nearest(target, side):
        pool = [x for x in cands if (x < cl_x if side == "L" else x > cl_x)]
        if not pool:
            return None
        if target is not None:
            return min(pool, key=lambda x: abs(x - target))
        # Outer edge of driving lanes ≈ innermost LOW on that side of CL
        # (closest to CL from the verge side of the carriageway)
        if side == "L":
            below = [x for x in pool if x < cl_x]
            return max(below) if below else None
        above = [x for x in pool if x > cl_x]
        return min(above) if above else None

    curb_left = nearest(lane_left, "L")
    curb_right = nearest(lane_right, "R")
    return curb_left, curb_right


def _group_sections(bands, labels, curb_xs, cl_xs, lane_edges, params):
    """Group by insert y + x-gaps > 3 m; attach ^[AB]\\d+(-\\d+)?$ labels (R3/R4)."""
    band_cfg = params.get("band") or {}
    y_tol = float(band_cfg.get("y_cluster_tol_m", 1.0))
    gap_m = float(band_cfg.get("x_gap_split_m", 3.0))
    coord_tol = float(params.get("coord_abs_m") or 0.05)

    # Deduplicate labels by value, keep first
    by_label = {}
    for lab in labels:
        by_label.setdefault(lab["label"], lab)
    label_list = list(by_label.values())

    clusters = []
    for y_group in _cluster_by_y(bands, y_tol):
        clusters.extend(_split_x_gaps(y_group, gap_m))

    used_labels = set()
    sections = []
    for group in clusters:
        if not group:
            continue
        gy = sum(b["y"] for b in group) / len(group)
        gcx = sum(b["centre"] for b in group) / len(group)
        row_left = min(b["x_min"] for b in group)
        row_right = max(b["x_max"] for b in group)
        mid_row = (row_left + row_right) / 2.0

        # CL = mid-ROW, cross-checked with Center line inside span (R2)
        cl_x = mid_row
        in_span = [x for x in cl_xs if row_left - 0.5 <= x <= row_right + 0.5]
        if in_span:
            nearest_cl = min(in_span, key=lambda x: abs(x - mid_row))
            if abs(nearest_cl - mid_row) <= coord_tol:
                cl_x = nearest_cl
            else:
                cl_x = mid_row  # prefer mid-ROW; still record nearest for debug

        curb_left, curb_right = _pick_curbs(
            cl_x, row_left, row_right, curb_xs, lane_edges, gy
        )

        # Nearest unused label (or allow reuse if two sections share geometry labels)
        lab = None
        if label_list:
            candidates = sorted(
                label_list,
                key=lambda L: (abs(L["y"] - gy), abs(L["x"] - gcx)),
            )
            for c in candidates:
                # Prefer unused; allow same label only if identical text already taken
                if c["label"] not in used_labels:
                    lab = c
                    break
            if lab is None:
                lab = candidates[0]
            used_labels.add(lab["label"])
            label = lab["label"]
        else:
            label = f"UNLABELED-{len(sections)+1}"
            lab = {"label": label, "x": gcx, "y": gy, "handle": None, "text": label}

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

        sections.append({
            "label": label,
            "cl_x": cl_x,
            "curb_left": curb_left,
            "curb_right": curb_right,
            "row_left": row_left,
            "row_right": row_right,
            "row_width": round(row_right - row_left, 6),
            "bands": band_rows,
            "utilities": utilities,
            "band_count": len(band_rows),
            "label_handle": lab.get("handle"),
            "label_text": lab.get("text"),
            "curb_found": curb_left is not None and curb_right is not None,
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
