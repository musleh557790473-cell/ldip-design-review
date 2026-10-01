#!/usr/bin/env python3
"""DXF → common ingestion JSON `<stem>.dwg.json` (SPC-0006 §2.4, §4.1.4).

Reads the DXF written by convert.py with ezdxf and records what the drawing contains. It does not
judge the design. Geometry stays in drawing units; units other than metres and a missing or
different CRS are written to `warnings` (SPC-0002 §6.1). Title-block fields are read by tag names
until the Parsons title-block layout is confirmed (K5) and carry a TITLE_BLOCK_HEURISTIC warning.

Usage: python extract_dxf.py --dxf <file.dxf> --dwg <source.dwg> --convert-log <file.convert.json>
                             --out <stem>.dwg.json [--source-path submittals/<SITE>/<sub>/<file>.dwg]
                             [--resolve-base <folder of the DWG>]
--source-path is written to source.path (default: the --dwg path as given). --resolve-base is the
folder used to decide whether xrefs, images and fonts resolve; without it `resolved` is null.
Exit: 0 written · 2 input error (missing or unreadable DXF, log not ok) · 3 ezdxf missing.
No network, no shell calls.
"""
__version__ = "0.1.2"

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


def dim_rec(e, space):
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
    return rec


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


def resolved(p, base):
    if base is None or not p:
        return None
    cand = Path(str(p).replace("\\", "/"))
    return cand.is_file() or (Path(base) / cand.name).is_file() or (Path(base) / cand).is_file()


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
                          "resolved": resolved(f, base)})

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
    content = {
        "header": {"insunits": insunits, "measurement": hdr.get("$MEASUREMENT", None),
                   "extmin": pt(hdr.get("$EXTMIN", (0, 0, 0))), "extmax": pt(hdr.get("$EXTMAX", (0, 0, 0))),
                   "acadver_source": conv.get("input", {}).get("acadver"), "acadver_dxf": hdr.get("$ACADVER", None)},
        "layers": layers, "blocks": blocks, "texts": texts, "dimensions": dims, "polylines": polys,
        "open_polylines_by_layer": dict(sorted(open_by_layer.items())),
        "xrefs": sorted(xrefs, key=lambda x: x["name"]), "images": sorted(images, key=lambda x: x["filename"]),
        "fonts": sorted(fonts, key=lambda x: x["style"]), "aecc": aecc, "origin_signals": origin, "layouts": lay_out,
    }
    ing = {
        "template_version": "1.0",
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
    a = ap.parse_args(argv)
    try:
        ing, out = extract(a)
        c = ing["content"]
        emit({"status": "ok", "out": str(out), "layers": len(c["layers"]), "blocks": len(c["blocks"]),
              "texts": len(c["texts"]), "dimensions": len(c["dimensions"]),
              "dimension_overrides": sum(1 for d in c["dimensions"] if d["override"]),
              "closed_polylines": len(c["polylines"]), "civil3d_native": c["aecc"]["civil3d_native"],
              "saved_in_civil3d": c["aecc"]["saved_in_civil3d"],
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
