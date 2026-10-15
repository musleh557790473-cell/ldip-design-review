#!/usr/bin/env python3
"""Road register (SPC-0006 Rev D §4.13.5; D10-2, D10-3): classes, design speeds, ROW groups, road labels,
typical sections and LandXML alignments of one submittal, with the carrier of every value.

Classes: criteria-table columns (read_criteria.py) and typical-section titles, mapped by the D-13 synonym
list (road_common.class_of). ROW groups: layer names "<n> m ROW" of the road hierarchy plan. Roads: texts
RD-nn / RD-M-nn / RA-nn with their insert points. Alignments: linked to a road by name (road label in
Alignment name or desc), else by position (a road label within half the widest ROW group of the
alignment centreline) — both cap later findings at Probable. Class of an alignment: a class term in its
own name or desc; a road's class from the hierarchy linework is not available in phase A (skill 1 keeps
no open-polyline geometry: [GAP] K17).
ROD-001 is checked here: every class the submittal uses maps to an ER class; the class given to one ROW
width by the criteria table and by a section title agree.

Usage: python build_register.py --criteria <criteria.json> --rules <rules_RDE.json> --out <road_register.json>
           [--hierarchy <plan.dwg.json> ...] [--sections <sections.dwg.json> ...] [--landxml <x.xml.json> ...]
Exit: 0 written · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_OK, ROAD_LABEL, atomic_write_json, class_of, emit, main_guard, param,  # noqa: E402
                         read_json, rules_index, source_of)
import check_sections  # noqa: E402

ROW_LAYER = re.compile(r"(?:^|\$)(\d+(?:\.\d+)?)\s*m\s*ROW$", re.I)


def seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def polyline(alignment):
    pts = []
    for e in alignment.get("elements", []):
        if e.get("start"):
            pts.append(e["start"][:2])
        if e.get("type") == "arc" and e.get("center") and e.get("start") and e.get("end"):
            c, s, f = e["center"], e["start"], e["end"]
            r = math.hypot(s[0] - c[0], s[1] - c[1])
            a0, a1 = math.atan2(s[1] - c[1], s[0] - c[0]), math.atan2(f[1] - c[1], f[0] - c[0])
            d = a1 - a0
            if e.get("rot") == "cw" and d > 0:
                d -= 2 * math.pi
            if e.get("rot") == "ccw" and d < 0:
                d += 2 * math.pi
            for k in range(1, 16):
                pts.append([c[0] + r * math.cos(a0 + d * k / 16), c[1] + r * math.sin(a0 + d * k / 16)])
        if e.get("end"):
            pts.append(e["end"][:2])
    return pts


def build(crit, rules, hierarchy, sections, landxml):
    reg = {"classes": {}, "row_groups": [], "roads": {}, "sections": [], "alignments": [], "checks": [], "warnings": []}
    src_c = crit["source"]
    for t in crit.get("tables", []):
        for heading, cls in zip(t["headings"], t["classes"]):
            reg["classes"].setdefault(cls, {"terms": [], "design_speed": None, "row": None})["terms"].append(heading)
    for cls, vals in crit.get("criteria", {}).items():
        c = reg["classes"].setdefault(cls, {"terms": [], "design_speed": None, "row": None})
        if (vals.get("design_speed") or {}).get("numbers"):
            c["design_speed"] = {"value": vals["design_speed"]["numbers"][0], "page": vals["design_speed"]["page"],
                                 "bbox": vals["design_speed"]["bbox"], "carrier": "design_report"}
        if (vals.get("row") or {}).get("numbers"):
            c["row"] = {"values": vals["row"]["numbers"], "page": vals["row"]["page"], "carrier": "design_report"}
        if (vals.get("functional_class") or {}).get("text"):
            c["terms"].append(vals["functional_class"]["text"])
    widest = 0.0
    for ing in hierarchy:
        src = source_of(ing)
        content = ing.get("content") or {}
        counts = content.get("open_polylines_by_layer") or {}
        for lay in sorted({l["name"] for l in content.get("layers", [])} | set(counts)):
            m = ROW_LAYER.search(lay)
            if m:
                w = float(m.group(1))
                widest = max(widest, w)
                reg["row_groups"].append({"width_m": w, "layer": lay, "open_polylines": counts.get(lay, 0),
                                          "source": src["path"]})
        for t in content.get("texts", []):
            txt = (t.get("text") or "").strip()
            if ROAD_LABEL.match(txt) and t.get("insert"):
                r = reg["roads"].setdefault(txt, {"labels": [], "source": src["path"], "class": None,
                                                 "class_note": "road class from hierarchy linework not available in phase A (K17)"})
                r["labels"].append({"e": t["insert"][0], "n": t["insert"][1], "handle": t["handle"]})
    for ing in sections:
        for s in check_sections.sections_of(ing):
            reg["sections"].append({"title": s["title"], "class": s["class"], "title_width_m": s["title_width_m"],
                                    "handle": s["title_handle"], "source": source_of(ing)["path"]})
    # ROD-001 (a): class terms map to ER classes; (b) one class per ROW width across carriers
    r001 = rules.get("ROD-001")
    if r001:
        classes = (param(r001, "classes") or {}).get("value") or []
        for cls, c in sorted(reg["classes"].items()):
            ok = cls in classes
            reg["checks"].append({"rule_id": "ROD-001", "parameter": "classes", "class": cls, "criterion": "class",
                                  "subject": f"road class '{' / '.join(dict.fromkeys(c['terms']))}'", "observed": cls,
                                  "required": "one of " + ", ".join(classes), "comparison": "in", "unit": None,
                                  "result": "pass" if ok else "fail",
                                  "evidence": {**src_c, "source_type": "pdf_vector_text", "page": crit["tables"][0]["pages"][0] if crit.get("tables") else None,
                                               "bbox": None, "extract": f"criteria table headings: {', '.join(c['terms'])}"}})
        by_width = {}
        for cls, c in reg["classes"].items():
            for w in ((c.get("row") or {}).get("values") or []):
                by_width.setdefault(w, set()).add(("design_report", cls))
        for s in reg["sections"]:
            if s["title_width_m"] is not None and s["class"]:
                by_width.setdefault(s["title_width_m"], set()).add(("typical_section", s["class"]))
        for w, pairs in sorted(by_width.items()):
            cl = {c for _, c in pairs}
            if len(cl) > 1:
                reg["checks"].append({"rule_id": "ROD-001", "parameter": "classes", "class": "/".join(sorted(cl)),
                                      "criterion": "class_by_row", "subject": f"ROW {w:g} m", "observed": sorted(pairs),
                                      "required": "one class per ROW width", "comparison": "consistent", "unit": "m",
                                      "result": "fail", "issue_type": "inconsistency",
                                      "evidence": {**src_c, "source_type": "pdf_vector_text", "page": None, "bbox": None,
                                                   "extract": f"ROW {w:g} m: " + "; ".join(f"{a} → {b}" for a, b in sorted(pairs))}})
        if not crit.get("criteria") and not reg["sections"]:
            reg["warnings"].append({"code": "NO_CLASS_CARRIER", "detail": "neither criteria table nor typical sections give a class"})
    half = widest / 2 if widest else 0.0
    for ing in landxml:
        src = source_of(ing)
        for a in (ing.get("content") or {}).get("alignments", []):
            name, desc = a.get("name") or "", a.get("desc") or ""
            road = next((r for r in reg["roads"] if re.search(r"(?<![A-Z0-9-])" + re.escape(r) + r"(?![0-9])", f"{name} {desc}".upper())), None)
            method = "name" if road else None
            if not road and half and reg["roads"]:
                pts = polyline(a)
                best = None
                for rid, r in reg["roads"].items():
                    for lab in r["labels"]:
                        d = min((seg_dist((lab["e"], lab["n"]), pts[i], pts[i + 1]) for i in range(len(pts) - 1)), default=1e9)
                        if d <= half and (best is None or d < best[1]):
                            best = (rid, d)
                if best:
                    road, method = best[0], "position"
            cls = class_of(f"{name} {desc}")
            speed = ((reg["classes"].get(cls) or {}).get("design_speed") or {}).get("value") if cls else None
            reg["alignments"].append({"name": name, "desc": desc, "source": src["path"], "road": road,
                                      "link_method": method, "class": cls,
                                      "class_source": "landxml name/desc" if cls else None,
                                      "design_speed_kmh": speed, "speed_source": "design_report" if speed else None})
    return reg


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--criteria", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--hierarchy", nargs="*", default=[])
    ap.add_argument("--sections", nargs="*", default=[])
    ap.add_argument("--landxml", nargs="*", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        rules, _, _ = rules_index(read_json(a.rules))
        reg = build(read_json(a.criteria), rules, [read_json(p) for p in a.hierarchy],
                    [read_json(p) for p in a.sections], [read_json(p) for p in a.landxml])
        reg["tool"] = {"script": "build_register.py", "version": __version__}
        reg["mode"] = "register"
        atomic_write_json(a.out, reg)
        emit({"status": "ok", "out": a.out, "classes": sorted(reg["classes"]), "row_groups": len(reg["row_groups"]),
              "roads": len(reg["roads"]), "sections": len(reg["sections"]), "alignments": len(reg["alignments"]),
              "fail": sum(1 for c in reg["checks"] if c["result"] == "fail")})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
