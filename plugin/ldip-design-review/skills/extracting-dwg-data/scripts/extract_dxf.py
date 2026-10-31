#!/usr/bin/env python3
"""DXF → common ingestion JSON `<stem>.dwg.json` (SPC-0006 §2.4, §4.1.4).

Reads the DXF written by convert.py with ezdxf and records what the drawing contains. It does not
judge the design. Geometry stays in drawing units; units other than metres and a missing or
different CRS are written to `warnings` (SPC-0002 §6.1). Title-block fields are read by tag names
until the Parsons title-block layout is confirmed (K5) and carry a TITLE_BLOCK_HEURISTIC warning.

Usage: python extract_dxf.py --dxf <file.dxf> --dwg <source.dwg> --convert-log <file.convert.json>
                             --out <stem>.dwg.json [--source-path submittals/<SITE>/<sub>/<file>.dwg]
                             [--resolve-base <folder of the DWG>] [--linework] [--no-expand]

0.2.0 (SPC-0006 Rev F §4.1A.3–4.1A.4; K21-2, K17-1, DF-2); DOa4-1 / DOa4-2 (G-Oa4):
- Block expansion (default on): model-space block references (INSERT, bound xrefs included) are expanded
  to their entities with the insert transform (ezdxf virtual_entities, nested to depth 4). Texts, closed
  polylines and DIMENSIONs found inside enter `texts` / `polylines` / `dimensions` with `via_block`
  (block-name chain), `insert_handle` (the model-space insert) and `layer_short`; `handle` is the
  entity's handle in the block definition. Expanded dimensions also carry transformed `points`
  (defpoint / text_midpoint). An entity on layer 0 takes the layer of its insert. Title-block skip
  (narrowed, DOa4-2): paper-space inserts, `TB_ATTRIB_BLOCKS`, or block name containing "Title Block"
  (case-insensitive). Other attributed inserts are expanded; each ATTRIB becomes a text with
  `source: "attrib"` and `tag`. `--no-expand` keeps the 0.1 behaviour.
- `--linework` writes the side file `<stem>.linework.json` next to the ingestion file: every LINE, ARC,
  CIRCLE, LWPOLYLINE, 2-D POLYLINE and SPLINE of model space and of the expanded blocks, flattened at
  0.05 m, with layer, layer_short, handle, via_block, closed, length and points [[E, N], ...] in drawing
  units (schema `schemas/linework.schema.json` 1.0). It is listed in `content.side_files`. Over
  200,000 records -> warning LINEWORK_LARGE and only the per-layer list is written.
--source-path is written to source.path (default: the --dwg path as given). --resolve-base is the
folder used to decide whether xrefs, images and fonts resolve; without it `resolved` is null.
Exit: 0 written · 2 input error (missing or unreadable DXF, log not ok) · 3 ezdxf missing.
No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dwg_common import (  # noqa: E402
    EXIT_DEPENDENCY, EXIT_INPUT, EXIT_OK, DwgError, atomic_write_json, check_path, emit, r2, r3, sha256_file,
)

RIYADH = timezone(timedelta(hours=3))
UNITS = {0: "unknown", 1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m", 7: "km"}
DOCNO_RE = re.compile(r"^[A-Z0-9]{2,6}(-[A-Z0-9]+){8,9}$")
TB_TAGS = {
    "document_no": re.compile(r"^(DWG|DRG|DRAWING|DOC|DOCUMENT)[ _.-]?(NO|NUM|NUMBER|REF)$|^DOC_?NO$|^DWGNO$", re.I),
    "revision": re.compile(r"^REV(ISION)?([ _.-]?(NO|CODE))?$", re.I),
    "title": re.compile(r"^(DRAWING[ _.-]?)?TITLE[ _.-]?\d*$", re.I),
    "date": re.compile(r"^(ISSUE[ _.-]?)?DATE$", re.I),
    "status": re.compile(r"^(STATUS|SUITABILITY|ISSUE[ _.-]?STATUS|PURPOSE)$", re.I),
}
EPSG_RE = re.compile(r"EPSG[\"':=\s]*(\d{4,5})|<Alias id=\"(\d{4,5})\"", re.I)


def hkey(e):
    try:
        return int(e.dxf.handle, 16)
    except (AttributeError, TypeError, ValueError):
        return 0


def proxy_class_id(e):
    """Application class ID (group 91) of an ACAD_PROXY_ENTITY / ACAD_PROXY_OBJECT; -1 if absent.
    It is not a DXF attribute in ezdxf, so it is read from the raw tags."""
    tag_sets = []
    t = getattr(e, "acdb_proxy_entity", None)
    if t is not None:
        tag_sets.append(t)
    xt = getattr(e, "xtags", None)
    if xt is not None:
        tag_sets.extend(getattr(xt, "subclasses", []))
    for tags in tag_sets:
        for tag in tags:
            if getattr(tag, "code", None) == 91:
                try:
                    return int(tag.value)
                except (TypeError, ValueError):
                    return -1
    return -1


def pt(v):
    return [r3(v[0]), r3(v[1]), r3(v[2] if len(v) > 2 else 0.0)]


def load(path):
    try:
        import ezdxf
        from ezdxf import recover
    except ImportError as exc:
        raise DwgError(EXIT_DEPENDENCY, "ezdxf is not installed") from exc
    try:
        doc, auditor = recover.readfile(str(path))
    except (OSError, ezdxf.DXFStructureError) as exc:
        raise DwgError(EXIT_INPUT, f"DXF not readable: {exc}") from exc
    return doc, auditor


def attribs(insert):
    out = {}
    for a in getattr(insert, "attribs", []):
        tag, val = str(a.dxf.tag).strip(), str(a.dxf.text)
        k, n = tag, 2
        while k in out:
            k, n = f"{tag}#{n}", n + 1
        out[k] = val
    return out


def insert_rec(e, space):
    return {"name": e.dxf.name, "layer": e.dxf.layer, "insert": pt(e.dxf.insert),
            "scale": [r3(e.dxf.get("xscale", 1)), r3(e.dxf.get("yscale", 1)), r3(e.dxf.get("zscale", 1))],
            "rotation": r3(e.dxf.get("rotation", 0)), "attributes": attribs(e), "handle": e.dxf.handle,
            "space": space}


def text_rec(e, space):
    if e.dxftype() == "MTEXT":
        s, h = e.plain_text(), e.dxf.get("char_height", None)
    else:
        s, h = e.plain_text() if hasattr(e, "plain_text") else e.dxf.text, e.dxf.get("height", None)
    return {"type": e.dxftype(), "text": s, "layer": e.dxf.layer, "insert": pt(e.dxf.insert),
            "height": r3(h), "rotation": r3(e.dxf.get("rotation", 0)), "handle": e.dxf.handle, "space": space}


NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def dim_points(e):
    """Definition / text points after any insert transform (virtual DIMENSION is already WCS)."""
    out = {}
    for key in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "text_midpoint"):
        if e.dxf.hasattr(key):
            try:
                out[key] = pt(e.dxf.get(key))
            except Exception:
                pass
    return out


def dim_rec(e, space, *, with_points=False):
    txt = e.dxf.get("text", "")
    try:
        m = e.get_measurement()
        meas = r3(m) if isinstance(m, (int, float)) else None
    except Exception:          # some dimension types (ordinate, arc) have no single value
        meas = None
    if txt in ("", "<>"):
        override, kind = False, "measured"
    elif txt.strip() == "" :
        override, kind = True, "suppressed"
    elif "<>" in txt:
        override, kind = False, "measured_with_prefix_suffix"
    else:
        override, kind = True, "text_override"
    rec = {"dimtype": e.dimtype, "measurement": meas, "text": txt, "override": override, "override_kind": kind,
           "layer": e.dxf.layer, "handle": e.dxf.handle, "space": space}
    if override and kind == "text_override":
        nums = NUM_RE.findall(txt.replace(",", ""))
        if nums:
            tv = float(nums[0])
            rec["text_value"] = tv
            rec["text_differs"] = meas is not None and abs(tv - meas) > 0.0005 * max(1.0, abs(meas))
    if with_points:
        pts = dim_points(e)
        if pts:
            rec["points"] = pts
    return rec


def attrib_handle(a, fallback):
    """ATTRIB on a virtual nested INSERT may have no handle; fall back to the insert handle."""
    try:
        h = a.dxf.handle
        if h:
            return h
    except Exception:
        pass
    return fallback


def attrib_text_rec(a, space, layer, handle):
    """ATTRIB on an expanded insert → text with source/tag (DOa4-2); position is already WCS."""
    return {"type": "TEXT", "text": str(a.dxf.get("text", "")), "layer": layer,
            "insert": pt(a.dxf.insert), "height": r3(a.dxf.get("height", None)),
            "rotation": r3(a.dxf.get("rotation", 0)), "handle": handle, "space": space,
            "source": "attrib", "tag": str(a.dxf.get("tag", ""))}


def shoelace(pts):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def poly_rec(e):
    from ezdxf import path as ezpath
    if e.dxftype() == "LWPOLYLINE":
        raw = list(e.get_points("xyb"))
        verts = [(p[0], p[1]) for p in raw]
        bulge = any(abs(p[2]) > 1e-12 for p in raw)
        elev = e.dxf.get("elevation", 0.0)
    else:
        verts = [(v.dxf.location[0], v.dxf.location[1]) for v in e.vertices]
        bulge = any(abs(v.dxf.get("bulge", 0.0)) > 1e-12 for v in e.vertices)
        elev = e.dxf.elevation[2] if hasattr(e.dxf, "elevation") else 0.0
    if bulge:
        flat = [(p[0], p[1]) for p in ezpath.make_path(e).flattening(0.001)]
        area = shoelace(flat[:-1] if flat and flat[0] == flat[-1] else flat)
    else:
        area = shoelace(verts)
    return {"type": e.dxftype(), "layer": e.dxf.layer, "handle": e.dxf.handle, "vertices": [[r3(x), r3(y)] for x, y in verts],
            "n_vertices": len(verts), "has_bulge": bulge, "elevation": r3(elev), "area": r2(area)}


MAX_DEPTH = 4
FLATTEN = 0.05
LINEWORK_MAX = 200_000
TB_ATTRIB_BLOCKS = ("Ladun - Qairwan ATT",)          # K5 profile, as convert.py
LINEWORK_TYPES = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE"}


def layer_short(name):
    return str(name or "").split("$")[-1]


def skip_insert(e, doc, *, paper_space=False):
    """Title-block rule narrowed (DOa4-2 / K5): paper-space, TB_ATTRIB_BLOCKS, or name contains 'Title Block'."""
    if paper_space:
        return True
    name = str(e.dxf.get("name") or "")
    if name in TB_ATTRIB_BLOCKS:
        return True
    if "title block" in name.lower():
        return True
    return False


def expanded(msp, doc, stats):
    """(entity, layer, src_handle, via_block chain, insert handle) for every entity inside model-space inserts.

    Also yields ATTRIB entities attached to expanded inserts (DOa4-2); their insert point is already WCS.
    """
    def emit_attribs(ins, chain, top, eff_layer):
        # Nested virtual INSERTs often lack ATTRIB handles; fall back to this insert's handle, else top.
        try:
            ins_h = ins.dxf.handle
        except Exception:
            ins_h = None
        fb = ins_h or top
        for a in getattr(ins, "attribs", []) or []:
            lay = a.dxf.get("layer", "0")
            lay = eff_layer if lay == "0" else lay
            stats["entities"] += 1
            stats["attribs"] += 1
            yield a, lay, attrib_handle(a, fb), chain, top

    def walk(ins, chain, top, depth, eff_layer):
        try:
            ents = list(ins.virtual_entities())
        except Exception as exc:              # an insert ezdxf cannot transform: counted, never fatal
            stats["inserts_not_expanded"] += 1
            stats.setdefault("not_expanded_detail", f"{type(exc).__name__}: {str(exc)[:120]}")
            return
        for v in ents:
            lay = v.dxf.get("layer", "0")
            lay = eff_layer if lay == "0" else lay
            src = getattr(v, "source_of_copy", None)
            h = src.dxf.handle if src is not None else None
            if v.dxftype() == "INSERT":
                if skip_insert(v, doc):
                    stats["inserts_skipped_title_block"] += 1
                    continue
                nested_chain = chain + [v.dxf.name]
                yield from emit_attribs(v, nested_chain, top, lay)
                if depth >= MAX_DEPTH:
                    stats["inserts_beyond_depth"] += 1
                    continue
                yield from walk(v, nested_chain, top, depth + 1, lay)
            else:
                stats["entities"] += 1
                yield v, lay, h, chain, top
    for e in sorted((e for e in msp if e.dxftype() == "INSERT"), key=hkey):
        if skip_insert(e, doc):
            stats["inserts_skipped_title_block"] += 1
            continue
        stats["inserts_expanded"] += 1
        top_layer = e.dxf.get("layer", "0")
        chain = [e.dxf.name]
        yield from emit_attribs(e, chain, e.dxf.handle, top_layer)
        yield from walk(e, chain, e.dxf.handle, 1, top_layer)


def linework_rec(e, layer, handle, chain):
    from ezdxf import path as ezpath
    t = e.dxftype()
    if t == "POLYLINE" and not e.is_2d_polyline:
        return None
    if t == "CIRCLE":
        pts = [(v.x, v.y) for v in e.flattening(FLATTEN)]
        closed = True
    else:
        pts = [(v.x, v.y) for v in ezpath.make_path(e).flattening(FLATTEN)]
        closed = bool(getattr(e, "closed", False)) if t == "LWPOLYLINE" else (bool(e.is_closed) if t == "POLYLINE" else False)
    if len(pts) < 2:
        return None
    length = sum(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 for (x0, y0), (x1, y1) in zip(pts, pts[1:]))
    return {"type": t, "layer": layer, "layer_short": layer_short(layer), "handle": handle, "via_block": chain,
            "closed": closed, "length": r3(length), "points": [[r3(x), r3(y)] for x, y in pts]}


def resolved(p, base):
    if base is None or not p:
        return None
    cand = Path(str(p).replace("\\", "/"))
    return cand.is_file() or (Path(base) / cand.name).is_file() or (Path(base) / cand).is_file()


# SHX fonts installed with every AutoCAD (Fonts folder): never "missing" from a package (fix 12 Oct 2026:
# 'txt' on style Standard was reported as unresolved on all Namar sheets).
AUTOCAD_SHX = {"txt", "simplex", "complex", "italic", "monotxt", "romans", "romand", "romanc", "romant", "scripts",
               "scriptc", "italicc", "italict", "greeks", "greekc", "gothice", "gothicg", "gothici", "syastro",
               "symap", "symath", "symeteo", "symusic", "isocp", "isocp2", "isocp3", "isoct", "isoct2", "isoct3",
               "isocpeur", "isoct2", "gdt", "amgdt", "ltypeshp", "bigfont", "chineset", "extfont", "extfont2",
               "gbcbig", "gbenor", "gbeitc", "@extfont2", "whgdtxt", "whgtxt", "whtgtxt", "whtmtxt"}


def font_resolved(f, base):
    stem = Path(str(f).replace("\\", "/")).name.lower()
    stem = stem[:-4] if stem.endswith(".shx") else stem
    if stem in AUTOCAD_SHX:
        return True
    return resolved(f, base)


def title_block(inserts):
    """Pick the insert with most attributes; map tags by name. [GAP K5] until Parsons zones confirmed."""
    cands = [i for i in inserts if i["attributes"]]
    if not cands:
        return None
    best = sorted(cands, key=lambda i: (-len(i["attributes"]), int(i["handle"], 16)))[0]
    tb = {k: None for k in TB_TAGS}
    titles = []
    for tag, val in best["attributes"].items():
        base = tag.split("#")[0]
        for field, rx in TB_TAGS.items():
            if rx.match(base) and str(val).strip():
                if field == "title":
                    titles.append((tag, str(val).strip()))
                elif tb[field] is None:
                    tb[field] = str(val).strip()
    if titles:
        tb["title"] = " ".join(v for _, v in sorted(titles))
    tb["source_ref"] = f"handle {best['handle']} (block {best['name']}, {best['space']})"
    return tb


def extract(a):
    dxf = check_path(a.dxf, must_exist=True)
    dwg = check_path(a.dwg, must_exist=True)
    out = check_path(a.out)
    try:
        conv = json.loads(check_path(a.convert_log, must_exist=True).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DwgError(EXIT_INPUT, f"converter log is not JSON: {exc}") from exc
    if conv.get("status") != "ok":
        raise DwgError(EXIT_INPUT, f"converter log status is {conv.get('status')!r}: {conv.get('detail')}")
    dwg_sha = sha256_file(dwg)
    if conv.get("input", {}).get("sha256") and conv["input"]["sha256"] != dwg_sha:
        raise DwgError(EXIT_INPUT, "the DWG hash differs from the converter log: the DXF is not from this DWG")
    doc, auditor = load(dxf)
    warnings = list(conv.get("warnings", []))
    if auditor.has_errors:
        warnings.append({"code": "DXF_RECOVERED", "detail": f"{len(auditor.errors)} structure errors fixed while reading"})
    hdr = doc.header
    insunits = int(hdr.get("$INSUNITS", 0))
    msp = doc.modelspace()
    layouts = [lo for lo in doc.layouts if not lo.is_modelspace]

    # entities per layer (all layouts)
    per_layer = Counter()
    for lo in [msp] + layouts:
        for e in lo:
            per_layer[e.dxf.get("layer", "0")] += 1

    blocks, texts, dims, polys = [], [], [], []
    open_by_layer = Counter()
    proxy_ent, proxy_obj = Counter(), Counter()
    aecc_entities, aecc_objects = Counter(), Counter()
    for space, lo in [("model", msp)] + [(f"paper:{lo.name}", lo) for lo in layouts]:
        for e in sorted(lo, key=hkey):
            t = e.dxftype()
            if t == "INSERT":
                blocks.append(insert_rec(e, space))
            elif t in ("TEXT", "MTEXT"):
                texts.append(text_rec(e, space))
            elif t == "DIMENSION":
                dims.append(dim_rec(e, space))
            elif t in ("LWPOLYLINE", "POLYLINE") and space == "model":
                if t == "POLYLINE" and not (e.is_2d_polyline):
                    continue
                if e.is_closed:
                    polys.append(poly_rec(e))
                else:
                    open_by_layer[e.dxf.layer] += 1
            elif t == "ACAD_PROXY_ENTITY":
                proxy_ent[proxy_class_id(e)] += 1
            elif t.upper().startswith("AECC"):
                aecc_entities[t] += 1

    lines = []
    if getattr(a, "linework", False):
        for e in sorted((e for e in msp if e.dxftype() in LINEWORK_TYPES), key=hkey):
            rec = linework_rec(e, e.dxf.layer, e.dxf.handle, [])
            if rec:
                lines.append(rec)
    xstats = Counter()
    if not getattr(a, "no_expand", False):
        for v, lay, h, chain, top in expanded(msp, doc, xstats):
            t = v.dxftype()
            extra = {"handle": h, "layer": lay, "layer_short": layer_short(lay), "via_block": chain, "insert_handle": top}
            try:
                if t == "ATTRIB":
                    texts.append({**attrib_text_rec(v, "model", lay, h), **extra})
                    xstats["texts"] += 1
                elif t in ("TEXT", "MTEXT"):
                    texts.append({**text_rec(v, "model"), **extra})
                    xstats["texts"] += 1
                elif t == "DIMENSION":
                    # DOa4-1: measurement / override from the virtual entity (already insert-transformed);
                    # points are the transformed definition / text midpoints.
                    rec = dim_rec(v, "model", with_points=True)
                    rec.update({k: extra[k] for k in ("handle", "layer", "layer_short", "via_block", "insert_handle")})
                    dims.append(rec)
                    xstats["dimensions"] += 1
                elif t in ("LWPOLYLINE", "POLYLINE") and (t == "LWPOLYLINE" or v.is_2d_polyline) and v.is_closed:
                    rec = poly_rec(v)
                    rec.update({k: extra[k] for k in ("handle", "layer", "layer_short", "via_block", "insert_handle")})
                    polys.append(rec)
                    xstats["closed_polylines"] += 1
                if getattr(a, "linework", False) and t in LINEWORK_TYPES:
                    rec = linework_rec(v, lay, h, chain)
                    if rec:
                        rec["insert_handle"] = top
                        lines.append(rec)
            except Exception:                 # one entity that cannot be read: counted, never fatal
                xstats["entities_unreadable"] += 1
        if xstats.get("inserts_not_expanded"):
            warnings.append({"code": "BLOCK_NOT_EXPANDED",
                             "detail": f"{xstats['inserts_not_expanded']} insert(s) not expanded: "
                                       + str(xstats.get("not_expanded_detail", ""))})
        if xstats.get("inserts_beyond_depth"):
            warnings.append({"code": "BLOCK_DEPTH_LIMIT",
                             "detail": f"{xstats['inserts_beyond_depth']} nested insert(s) deeper than {MAX_DEPTH} not expanded"})

    classes = []
    for i, c in enumerate(doc.classes.classes.values()):
        classes.append({"index": i, "id": 500 + i, "name": c.dxf.name, "cpp_class_name": c.dxf.cpp_class_name,
                        "app_name": c.dxf.app_name, "instance_count": c.dxf.get("instance_count", 0)})
    by_id = {c["id"]: c["name"] for c in classes}
    for o in doc.objects:
        ot = o.dxftype()
        if ot == "ACAD_PROXY_OBJECT":
            proxy_obj[proxy_class_id(o)] += 1
        elif ot.upper().startswith("AECC"):
            aecc_objects[ot] += 1
    is_aecc = lambda c: c["name"].upper().startswith("AECC") or c["cpp_class_name"].upper().startswith("AECC") \
        or "AECC" in c["app_name"].upper()  # noqa: E731
    aecc_classes = sorted((c for c in classes if is_aecc(c)), key=lambda c: c["name"])
    def by_class(counter):
        out = Counter()
        for k, v in counter.items():
            out[by_id.get(k, f"class_id {k}")] += v
        return dict(sorted(out.items()))
    ent_by_class, obj_by_class = by_class(proxy_ent), by_class(proxy_obj)
    aecc_proxy_entities = sum(v for k, v in ent_by_class.items() if k.upper().startswith("AECC"))
    # P-S3-22 (owner, 30 Sep 2026): Civil 3D native = at least one AECC object drawn in the layouts
    # (AECC proxy entity or AECC entity type). AECC class definitions alone only show the file was
    # saved in a Civil 3D session (brief §1.4 (c)).
    native = bool(aecc_proxy_entities or aecc_entities)
    aecc = {"aecc_class_count": len(aecc_classes),
            "aecc_classes": [{"name": c["name"], "app_name": c["app_name"], "instance_count": c["instance_count"]}
                             for c in aecc_classes],
            "aecc_entities_by_type": dict(sorted(aecc_entities.items())),
            "aecc_objects_by_type": dict(sorted(aecc_objects.items())),
            "proxy_entities_by_class": ent_by_class, "proxy_objects_by_class": obj_by_class,
            "aecc_proxy_entity_count": aecc_proxy_entities,
            "proxy_count": sum(proxy_ent.values()) + sum(proxy_obj.values()),
            "civil3d_native": native,
            "saved_in_civil3d": bool(aecc_classes) and not native}
    if conv.get("engine") == "ezdwg":
        warnings.append({"code": "AECC_DETECTION_LIMITED",
                         "detail": "ezdwg does not document class or proxy transfer; an AECC count of 0 is not proof "
                                   "that the DWG is not Civil 3D native (use oda or accoreconsole for brief §1.4)"})

    layers = []
    for ly in sorted(doc.layers, key=lambda x: x.dxf.name):
        layers.append({"name": ly.dxf.name, "color": abs(ly.dxf.get("color", 7)), "linetype": ly.dxf.get("linetype", "Continuous"),
                       "frozen": ly.is_frozen(), "off": ly.is_off(), "locked": ly.is_locked(),
                       "entity_count": per_layer.get(ly.dxf.name, 0)})

    base = Path(a.resolve_base) if a.resolve_base else None
    xrefs, images, fonts = [], [], []
    for b in doc.blocks:
        br = b.block_record
        flags = b.block.dxf.get("flags", 0)
        if br.is_xref or flags & 4:
            xp = b.block.dxf.get("xref_path", "")
            xrefs.append({"name": b.name, "saved_path": xp, "overlay": bool(flags & 8), "resolved": resolved(xp, base)})
    for o in doc.objects:
        if o.dxftype() == "IMAGEDEF":
            fn = o.dxf.get("filename", "")
            images.append({"filename": fn, "resolved": resolved(fn, base), "handle": o.dxf.handle})
    for st in doc.styles:
        f = st.dxf.get("font", "")
        if f:
            fonts.append({"style": st.dxf.name, "font": f, "bigfont": st.dxf.get("bigfont", "") or None,
                          "resolved": font_resolved(f, base)})

    lay_out = []
    for lo in sorted(layouts, key=lambda x: (x.dxf.get("taborder", 0), x.name)):
        ins = [b for b in blocks if b["space"] == f"paper:{lo.name}"]
        tb = title_block(ins)
        lay_out.append({"name": lo.name, "taborder": lo.dxf.get("taborder", 0),
                        "title_block_candidate": None if tb is None else next(
                            {"block": i["name"], "handle": i["handle"], "attributes": i["attributes"]}
                            for i in ins if i["handle"] == tb["source_ref"].split()[1]),
                        "inserts_with_attributes": sum(1 for i in ins if i["attributes"])})
    tb = next((title_block([b for b in blocks if b["space"] == f"paper:{lo['name']}"]) for lo in lay_out
               if lo["title_block_candidate"]), None) or title_block([b for b in blocks if b["space"] == "model"])
    if tb is not None:
        warnings.append({"code": "TITLE_BLOCK_HEURISTIC",
                         "detail": "title-block fields read by attribute tag names; Parsons layout not confirmed (K5)"})
    elif any(w.get("code") == "TITLE_BLOCK_ATTRIBS_UNDECODED" for w in conv.get("warnings", [])):
        pass  # K5-2: attributes exist but the engine could not decode them; the convert-log warning stands
    else:
        warnings.append({"code": "TITLE_BLOCK_NOT_FOUND", "detail": "no block insert with attributes found"})

    # CRS
    declared, geo = None, None
    try:
        geo = msp.get_geodata()
    except Exception:
        geo = None
    if geo is not None:
        xml = geo.coordinate_system_definition or ""
        m = EPSG_RE.search(xml)
        if m:
            declared = f"EPSG:{m.group(1) or m.group(2)}"
    if declared is None:
        warnings.append({"code": "CRS_NOT_DECLARED", "detail": "no GEODATA coordinate system in the drawing"})
    elif declared != "EPSG:32638":
        warnings.append({"code": "CRS_NOT_32638", "detail": f"declared {declared}; project CRS is EPSG:32638"})
    ext = [hdr.get("$EXTMIN", (0, 0, 0)), hdr.get("$EXTMAX", (0, 0, 0))]
    if any(abs(v) >= 1e19 for p_ in ext for v in p_):
        warnings.append({"code": "EXTENTS_NOT_SET", "detail": "$EXTMIN / $EXTMAX hold the empty-drawing sentinel"})
    if insunits != 6:
        warnings.append({"code": "UNITS_NOT_METRES", "detail": f"$INSUNITS = {insunits} ({UNITS.get(insunits, 'other')})"})

    names = [ly["name"] for ly in layers]
    bnames = [b.name for b in doc.blocks]
    origin = {"confidence": "Probable",
              "layers_ncs_civil_prefix": sum(1 for n in names if re.match(r"^C-", n, re.I)),
              "layers_with_aecc": sum(1 for n in names if "AECC" in n.upper()),
              "blocks_with_aecc": sum(1 for n in bnames if "AECC" in n.upper()),
              "anonymous_blocks": sum(1 for n in bnames if n.startswith("*U")),
              "proxy_entities": sum(proxy_ent.values()), "proxy_objects": sum(proxy_obj.values()),
              "note": "signals only, for the brief §1.4 (b)/(c) heuristic; decided by intake-package"}

    stem = dwg.stem
    side_files = []
    if getattr(a, "linework", False):
        lw_path = out.with_name(out.name[:-len(".dwg.json")] + ".linework.json" if out.name.endswith(".dwg.json")
                                else out.stem + ".linework.json")
        by_layer = {}
        for r in lines:
            k = by_layer.setdefault(r["layer"], {"layer_short": r["layer_short"], "records": 0, "length": 0.0})
            k["records"] += 1
            k["length"] += r["length"] or 0.0
        for k in by_layer.values():
            k["length"] = r3(k["length"])
        lw_warn = []
        recs = sorted(lines, key=lambda r: (r["layer"], r["insert_handle"] if r.get("insert_handle") else "",
                                            str(r["handle"]), r["points"][0]))
        if len(recs) > LINEWORK_MAX:
            lw_warn.append({"code": "LINEWORK_LARGE", "detail": f"{len(recs)} records > {LINEWORK_MAX}: layer list only"})
            warnings.append(dict(lw_warn[0]))
            recs = []
        lw = {"schema_version": "1.0", "source": {"path": (a.source_path or dwg.as_posix()).replace("\\", "/"),
                                                  "sha256": dwg_sha},
              "extractor": {"script": "extract_dxf.py", "version": __version__, "flatten_m": FLATTEN,
                            "block_expansion": not getattr(a, "no_expand", False)},
              "units": UNITS.get(insunits, "unknown"), "layers": dict(sorted(by_layer.items())), "records": recs,
              "warnings": lw_warn}
        atomic_write_json(lw_path, lw)
        side_files.append({"path": lw_path.name, "kind": "linework", "sha256": sha256_file(lw_path),
                           "records": len(recs)})
    content = {
        "header": {"insunits": insunits, "measurement": hdr.get("$MEASUREMENT", None),
                   "extmin": pt(hdr.get("$EXTMIN", (0, 0, 0))), "extmax": pt(hdr.get("$EXTMAX", (0, 0, 0))),
                   "acadver_source": conv.get("input", {}).get("acadver"), "acadver_dxf": hdr.get("$ACADVER", None)},
        "layers": layers, "blocks": blocks, "texts": texts, "dimensions": dims, "polylines": polys,
        "open_polylines_by_layer": dict(sorted(open_by_layer.items())),
        "xrefs": sorted(xrefs, key=lambda x: x["name"]), "images": sorted(images, key=lambda x: x["filename"]),
        "fonts": sorted(fonts, key=lambda x: x["style"]), "aecc": aecc, "origin_signals": origin, "layouts": lay_out,
        "block_expansion": {"on": not getattr(a, "no_expand", False),
                            **{k: v for k, v in sorted(xstats.items()) if k != "not_expanded_detail"}},
        "side_files": side_files,
    }
    ing = {
        "template_version": "1.2",
        "source": {"path": (a.source_path or dwg.as_posix()).replace("\\", "/"), "sha256": dwg_sha,
                   "bytes": dwg.stat().st_size, "document_no": stem if DOCNO_RE.match(stem) else None,
                   "revision": None, "file_type": "dwg"},
        "extractor": {"skill": "extracting-dwg-data", "script": "extract_dxf.py", "version": __version__,
                      "converter": {"name": conv.get("engine"), "version": conv.get("engine_version")}},
        "crs": {"declared": declared, "units": UNITS.get(insunits, "unknown") if insunits in (4, 6) else "unknown",
                "insunits": insunits},
        "title_block": tb,
        "content": content,
        "warnings": sorted(warnings, key=lambda w: (w["code"], w["detail"])),
        "meta": {"extracted_at": datetime.now(RIYADH).isoformat(timespec="seconds"),
                 "dxf_sha256": sha256_file(dxf)},
    }
    atomic_write_json(out, ing)
    return ing, out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dxf", required=True)
    ap.add_argument("--dwg", required=True)
    ap.add_argument("--convert-log", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--source-path")
    ap.add_argument("--resolve-base")
    ap.add_argument("--linework", action="store_true")
    ap.add_argument("--no-expand", action="store_true")
    a = ap.parse_args(argv)
    try:
        ing, out = extract(a)
        c = ing["content"]
        emit({"status": "ok", "out": str(out), "layers": len(c["layers"]), "blocks": len(c["blocks"]),
              "texts": len(c["texts"]), "dimensions": len(c["dimensions"]),
              "dimension_overrides": sum(1 for d in c["dimensions"] if d["override"]),
              "closed_polylines": len(c["polylines"]), "civil3d_native": c["aecc"]["civil3d_native"],
              "saved_in_civil3d": c["aecc"]["saved_in_civil3d"],
              "expanded": {k: v for k, v in c["block_expansion"].items()
                           if k in ("inserts_expanded", "texts", "closed_polylines", "dimensions", "attribs")},
              "linework": c["side_files"][0]["records"] if c["side_files"] else None,
              "warnings": [w["code"] for w in ing["warnings"]]})
        return EXIT_OK
    except DwgError as e:
        emit({"status": "error", "code": e.code, "detail": e.detail})
        return e.code
    except Exception as exc:   # content this version cannot read: report, never crash silently (B5)
        import traceback
        tb = traceback.extract_tb(exc.__traceback__)[-1]
        emit({"status": "error", "code": EXIT_INPUT,
              "detail": f"unreadable DXF content: {type(exc).__name__}: {exc} (extract_dxf.py line {tb.lineno})"})
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
