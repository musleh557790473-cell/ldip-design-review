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
        # Still usable; caller records a warning (D13 pin).
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
    # block header handle → block name
    header_name = {h: n for h, n in block_headers}
    # block name → list of insert rows that reference it (by name)
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

    # For each insert, walk owner: if owner is a block header that is itself
    # inserted somewhere, chain those parent inserts. Model-space owners stop.
    header_to_inserts = defaultdict(list)
    # Invert: which inserts place a given block (by block-header handle of that name)
    name_to_header = {n: h for h, n in block_headers}
    for h, rec in insert_by_h.items():
        nm = rec["name"]
        if nm and nm in name_to_header:
            # this INSERT places block nm; its *definition* header is name_to_header[nm]
            pass
    # Entities owned by block-header H live inside that block. Parent INSERTs are
    # those whose name equals header_name[H].
    def parent_chain(handle):
        chain = []
        seen = set()
        owner = owner_of.get(handle)
        while owner is not None and owner not in seen:
            seen.add(owner)
            bname = header_name.get(owner)
            if not bname:
                break
            # Find INSERT(s) that reference this block. Prefer the one whose
            # owner is model space (or continue outward).
            parents = [insert_by_h[ph] for ph in by_name.get(bname, []) if ph in insert_by_h]
            if not parents:
                break
            # Pick a parent; if several, take the first (typical: one xref insert).
            parent = parents[0]
            chain.append({
                "x": parent["x"], "y": parent["y"],
                "sx": parent["sx"], "sy": parent["sy"], "rot": parent["rot"],
                "handle": parent["handle"], "name": parent["name"],
            })
            owner = owner_of.get(parent["handle"])
        return chain

    return insert_by_h, parent_chain


def read_dwg_raw(dwg_path, layers_map, params):
    ver, unpinned = _require_ezdwg()
    from ezdwg.raw import (
        decode_insert_entities, decode_insert_owner_handles, decode_layer_names,
        decode_object_entity_layer_handles, decode_line_entities,
        decode_text_entities, decode_mtext_entities, decode_block_header_names,
        decode_entity_placements,
    )

    path = str(Path(dwg_path).resolve())
    inserts = list(decode_insert_entities(path))
    owners = list(decode_insert_owner_handles(path))
    layer_names = dict(decode_layer_names(path))  # handle → name
    block_headers = list(decode_block_header_names(path))
    insert_by_h, parent_chain = _build_owner_index(inserts, owners, block_headers)

    # Layer handle per insert
    handles = [r[0] for r in inserts]
    ent_layers = dict(decode_object_entity_layer_handles(path, handles)) if handles else {}

    # Lines for curb / CL
    lines = list(decode_line_entities(path))
    line_handles = [r[0] for r in lines]
    line_layers = dict(decode_object_entity_layer_handles(path, line_handles)) if line_handles else {}
    curb_subs = (params.get("curb") or {}).get("limit_of_work_layer_substrings") or ["LIMIT OF WORK"]
    cl_subs = (params.get("curb") or {}).get("center_line_layer_substrings") or ["Center line"]
    title_subs = (params.get("section_label") or {}).get("layer_substrings") or ["DWG_TITLE"]

    curb_xs, cl_xs = [], []
    for h, x1, y1, z1, x2, y2, z2 in lines:
        lname = layer_names.get(line_layers.get(h), "")
        # Near-vertical lines (section cuts are horizontal bands; curb/CL are vertical ticks)
        if abs(x2 - x1) > abs(y2 - y1):
            continue
        xmid = (x1 + x2) / 2.0
        if _layer_match(lname, curb_subs):
            curb_xs.append(xmid)
        if _layer_match(lname, cl_subs):
            cl_xs.append(xmid)

    # Texts for section labels
    texts = []
    for row in decode_text_entities(path):
        # (handle, text, insert, align, extrusion, metrics, flags, style, ...)
        h, text, insert = row[0], row[1], row[2]
        texts.append((h, text, insert[0], insert[1]))
    for row in decode_mtext_entities(path):
        h, text, insert = row[0], row[1], row[2]
        texts.append((h, text, insert[0], insert[1]))

    text_handles = [t[0] for t in texts]
    text_layers = dict(decode_object_entity_layer_handles(path, text_handles)) if text_handles else {}
    labels = []
    for h, text, tx, ty in texts:
        lname = layer_names.get(text_layers.get(h), "")
        if not _layer_match(lname, title_subs):
            # Also accept any text that looks like a section label
            lab = parse_section_label(text)
            if not lab:
                continue
        else:
            lab = parse_section_label(text) or str(text).strip()
        if lab:
            labels.append({"label": lab, "x": tx, "y": ty, "handle": h, "text": text})

    # Band inserts by utility layer
    bands = []
    for h, x, y, z, sx, sy, sz, rot, name in inserts:
        lh = ent_layers.get(h)
        lname = layer_names.get(lh, "")
        meta = resolve_utility(lname, layers_map)
        if not meta:
            continue
        # Skip container inserts that are not themselves utility bands
        # (utility bands typically have |sx| as band width < ~5 m; xref containers ~1)
        # Keep any insert whose layer maps to a utility.
        chain = parent_chain(h)
        wx, wy = _combine_parent_transform(x, y, chain)
        # Scale sx through parent x-scales
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
            "x_min": x_min, "x_max": x_max, "centre": centre, "width": width,
            "y": wy,
            "nested": bool(chain),
            "parent_chain": [p["handle"] for p in chain],
            "raw_x": x, "raw_sx": sx,
        })

    # Group bands into sections by nearest label in Y (or single section if one label)
    sections = _group_sections(bands, labels, curb_xs, cl_xs, params)

    warnings = []
    if unpinned:
        warnings.append({
            "code": "ENGINE_VERSION_UNPINNED",
            "detail": f"ezdwg {ver} installed; pinned {EZDWG_PINNED}",
        })

    return {
        "sections_version": "1.0",
        "route": "dwg_raw",
        "source": {"path": path, "ezdwg": ver, "insert_count": len(inserts),
                   "band_insert_count": len(bands)},
        "status_cap": None,
        "sections": sections,
        "warnings": warnings,
        "meta": {"generated_at": _now(), "reader": f"read_corridor_sections/{__version__}"},
    }


def _group_sections(bands, labels, curb_xs, cl_xs, params):
    """Assign bands to section labels; derive curb/CL per section."""
    if not labels:
        labels = [{"label": "A1", "x": 0.0, "y": 0.0, "handle": None, "text": "A1"}]

    # Unique labels preferring DWG_TITLE order
    by_label = {}
    for lab in labels:
        by_label.setdefault(lab["label"], lab)
    label_list = list(by_label.values())

    # Global CL / curbs as defaults
    cl_x = float(sorted(cl_xs, key=abs)[0]) if cl_xs else 0.0
    left_curbs = sorted(x for x in curb_xs if x < cl_x)
    right_curbs = sorted(x for x in curb_xs if x > cl_x)
    curb_left = left_curbs[-1] if left_curbs else (min(curb_xs) if curb_xs else None)
    curb_right = right_curbs[0] if right_curbs else (max(curb_xs) if curb_xs else None)

    # Assign each band to nearest label by Y (then X)
    assigned = defaultdict(list)
    for b in bands:
        best = min(label_list, key=lambda L: (abs(b["y"] - L["y"]), abs(b["centre"] - L["x"])))
        assigned[best["label"]].append(b)

    # If no bands matched any label clustering, put all under first label
    if bands and not any(assigned.values()):
        assigned[label_list[0]["label"]] = list(bands)

    sections = []
    for lab in label_list:
        label = lab["label"]
        sec_bands = assigned.get(label) or []
        # Per-section curb: prefer global; allow override if bands imply a local CL
        utilities = {}
        for b in sec_bands:
            u = b["utility"]
            # Combined bands: keep the widest / first; record extras
            rec = {
                "x_min": b["x_min"], "x_max": b["x_max"], "centre": b["centre"],
                "width": b["width"], "layer": b["layer"], "handle": b["handle"],
                "class": b["class"], "power": b["power"], "nested": b["nested"],
            }
            off, side = offset_to_curb(b["centre"], curb_left, curb_right, cl_x)
            rec["offset_to_curb"] = off
            rec["side"] = side or side_of_cl(b["centre"], cl_x)
            if u not in utilities:
                utilities[u] = rec
            else:
                # Merge: expand envelope (combined band)
                prev = utilities[u]
                x_min = min(prev["x_min"], rec["x_min"])
                x_max = max(prev["x_max"], rec["x_max"])
                centre = (x_min + x_max) / 2.0
                off2, side2 = offset_to_curb(centre, curb_left, curb_right, cl_x)
                utilities[u] = {
                    **prev,
                    "x_min": x_min, "x_max": x_max, "centre": centre,
                    "width": x_max - x_min,
                    "offset_to_curb": off2, "side": side2,
                    "combined": True,
                    "handles": list({*(prev.get("handles") or [prev["handle"]]), rec["handle"]}),
                }

        sections.append({
            "label": label,
            "cl_x": cl_x,
            "curb_left": curb_left,
            "curb_right": curb_right,
            "row_left": min(curb_xs) if curb_xs else None,
            "row_right": max(curb_xs) if curb_xs else None,
            "utilities": utilities,
            "band_count": len(sec_bands),
            "label_handle": lab.get("handle"),
            "label_text": lab.get("text"),
        })
    return sections


def read_pdf_fallback(pdf_path, layers_map, params):
    """PDF legend-colour route — Probable only (DO-2)."""
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError("pdfplumber is not installed") from exc

    path = str(Path(pdf_path).resolve())
    legend = layers_map.get("legend_colours") or {}
    # Invert RGB → utility (exact match after rounding)
    rgb_to_u = {}
    for util, rgb in legend.items():
        if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
            key = tuple(int(c) for c in rgb[:3])
            rgb_to_u[key] = util

    sections = []
    with pdfplumber.open(path) as pdf:
        for page_i, page in enumerate(pdf.pages, start=1):
            # Legend text
            text = page.extract_text() or ""
            label = parse_section_label(text) or f"P{page_i}"
            # Coloured rects / curves as band proxies (pdfplumber)
            utilities = {}
            figs = list(page.rects or []) + list(page.curves or [])
            for fig in figs:
                if not fig.get("fill"):
                    continue
                fill = fig.get("non_stroking_color")
                if fill is None:
                    continue
                if isinstance(fill, (int, float)):
                    rgb = (int(fill * 255),) * 3 if fill <= 1 else (int(fill),) * 3
                elif isinstance(fill, (list, tuple)):
                    if len(fill) == 1:
                        v = fill[0]
                        rgb = (int(v * 255),) * 3 if v <= 1 else (int(v),) * 3
                    else:
                        vals = list(fill[:3])
                        if all(isinstance(v, (int, float)) and v <= 1 for v in vals):
                            rgb = tuple(int(v * 255) for v in vals)
                        else:
                            rgb = tuple(int(v) for v in vals)
                else:
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
                # PDF units as metres for synthetic fixtures; real sheets scale from ROW.
                x_min, x_max = min(x0, x1), max(x0, x1)
                centre = (x_min + x_max) / 2.0
                width = x_max - x_min
                meta = resolve_utility(util, layers_map) or {
                    "utility": util,
                    "class": "wet" if util in (params.get("wet") or []) else "dry",
                    "power": util in (params.get("power") or []),
                }
                utilities[util] = {
                    "x_min": x_min, "x_max": x_max, "centre": centre, "width": width,
                    "layer": f"pdf:{util}", "handle": f"page{page_i}:{util}",
                    "class": meta.get("class"), "power": bool(meta.get("power")),
                    "page": page_i,
                }

            cl_x = 0.0
            curb_left = curb_right = None
            if utilities:
                right_mins = [u["x_min"] for u in utilities.values() if u["centre"] > 0]
                left_maxs = [u["x_max"] for u in utilities.values() if u["centre"] < 0]
                if right_mins:
                    curb_right = max(0.0, min(right_mins) - 0.1)
                else:
                    curb_right = 1.0
                if left_maxs:
                    curb_left = min(0.0, max(left_maxs) + 0.1)
                else:
                    curb_left = -curb_right
                for _u, rec in utilities.items():
                    off, side = offset_to_curb(rec["centre"], curb_left, curb_right, cl_x)
                    rec["offset_to_curb"] = off
                    rec["side"] = side

            sections.append({
                "label": label,
                "cl_x": cl_x,
                "curb_left": curb_left,
                "curb_right": curb_right,
                "utilities": utilities,
                "band_count": len(utilities),
                "page": page_i,
            })

    return {
        "sections_version": "1.0",
        "route": "pdf",
        "source": {"path": path, "page_count": len(sections)},
        "status_cap": "Probable",
        "sections": sections,
        "warnings": [{"code": "PDF_FALLBACK", "detail": "PDF legend-colour route; statuses capped at Probable (DO-2)"}],
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
            emit({"status": "ok", "route": doc.get("route"), "sections": len(doc.get("sections") or []),
                  "out": str(a.out)})
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
