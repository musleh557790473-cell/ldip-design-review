#!/usr/bin/env python3
"""Read one LandXML file into the common ingestion JSON, template 1.1 (SPC-0006 Rev C §2.4.2, §4.12).

Extracts alignments (lines, arcs, spirals, stations, station equations), profiles (ProfAlign design
PVIs and vertical curves with grade, A and K; ProfSurf existing-ground lines) and TIN surfaces
(points, faces, breaklines, boundaries; extents, Z range, 2D / 3D area from visible faces) and
writes, after schema validation:
  <out>/<file>.json                 e.g. X.xml -> X.xml.json  (template 1.1)
  <out>/<stem>.surface-<n>.json     one side file per surface with the full TIN (E, N, Z)
Every coordinate is written Easting, Northing, Elevation (LandXML text is N E Z; §4.12.6).

Usage: python parse_landxml.py --in <file.xml> --out <batch dir> | <batch dir>/<file.xml>.json
                               --provenance parsons|employer|aicc_export
                               [--source-path <path as recorded, relative to LDIP_Review>]
--out as the main output file (0.1.1, P7) is the form used in a review run: like skills 1 and 2 it names a
file inside the batch folder, which the guard can check before the folder exists; it must be <file.xml>.json.
Exit: 0 written, or skipped (not LandXML; nothing written) · 2 input error (missing, > 500 MB,
malformed XML, DOCTYPE with entity declarations, unknown LandXML namespace) · 3 lxml / jsonschema
missing · 4 output failed its schema (nothing written).
XML security (§4.12.9): resolve_entities=False, no_network=True, load_dtd=False; entity
declarations refused before parsing. No network, no shell calls, no eval.
"""
__version__ = "0.1.1"

import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from landxml_common import (  # noqa: E402
    AICC_EXPORT_SUFFIX, EXIT_DEPENDENCY, EXIT_INPUT, EXIT_OK, EXIT_SCHEMA, MAX_BYTES, LandXmlError,
    atomic_write_bytes, check_path, dumps_main, dumps_side, emit, number_from_name, r2, r3, revision_of,
    sha256_bytes, sha256_file,
)
import landxml_geom as g  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
INGESTION_SCHEMA = PLUGIN_ROOT / "schemas" / "ingestion.schema.json"
SURFACE_SCHEMA = PLUGIN_ROOT / "schemas" / "landxml_surface.schema.json"
NAMESPACES = {"http://www.landxml.org/schema/LandXML-1.0": "1.0",
              "http://www.landxml.org/schema/LandXML-1.1": "1.1",
              "http://www.landxml.org/schema/LandXML-1.2": "1.2"}
EXTRACTED_TOP = {"Units", "CoordinateSystem", "Project", "Application", "Alignments", "Surfaces"}
ALIGN_CHILDREN_READ = {"CoordGeom", "Profile", "StaEquation", "Feature"}
SURFACE_CHILDREN_READ = {"SourceData", "Definition", "Feature"}
SIDE_SUFFIXES = (".aecc.pnt", ".aecc.tri")
PROLOG_MAX = 8 * 1024 * 1024
TOL = 0.001                 # m — continuity and station tolerance (§4.12.5)
KSA = timezone(timedelta(hours=3))


class NotLandXml(Exception):
    pass


# ============================================================================ security pre-check
def prolog_check(path):
    """Scan the bytes before the root element. Returns {'doctype': bool}. Raises LandXmlError(2) for a
    DOCTYPE that declares entities (internal or external), or when no root element is found."""
    with open(path, "rb") as fh:
        data = fh.read(PROLOG_MAX)
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        data = data.decode("utf-16", errors="replace").encode("utf-8")
    i, doctype = 0, False
    while True:
        j = data.find(b"<", i)
        if j < 0:
            raise LandXmlError(EXIT_INPUT, "no root element in the first 8 MB")
        if data.startswith(b"<!--", j):
            k = data.find(b"-->", j + 4)
            if k < 0:
                raise LandXmlError(EXIT_INPUT, "unterminated comment before the root element")
            i = k + 3
        elif data.startswith(b"<?", j):
            k = data.find(b"?>", j + 2)
            if k < 0:
                raise LandXmlError(EXIT_INPUT, "unterminated processing instruction")
            i = k + 2
        elif data.startswith(b"<!", j):
            doctype = True
            close, bracket = data.find(b">", j), data.find(b"[", j)
            if 0 <= bracket < close:
                close = data.find(b"]", bracket)
                close = data.find(b">", close) if close >= 0 else -1
            if close < 0:
                raise LandXmlError(EXIT_INPUT, "unterminated DOCTYPE")
            decl = data[j:close + 1].upper()
            if b"<!ENTITY" in decl or b"SYSTEM" in decl or b"PUBLIC" in decl:
                raise LandXmlError(EXIT_INPUT, "DOCTYPE with an entity declaration or external DTD refused (§4.12.9)")
            i = close + 1
        else:
            return {"doctype": doctype}


def classify(path):
    """What intake should record for a .xml file that has no ingestion file (P7; write_manifest.py of
    skill 1). Reads only the prolog and the root element, with the same rules as main():
    returns ("skipped", code, detail) for a Civil 3D side file or a file whose root is not LandXML, and
    ("failed", code, detail) for anything that main() refuses or should have written."""
    p = Path(path)
    low = p.name.lower()
    if low.endswith(SIDE_SUFFIXES) or not low.endswith(".xml"):
        return "skipped", EXIT_INPUT, "not a LandXML file (Civil 3D side file or not .xml)"
    if not p.is_file():
        return "failed", EXIT_INPUT, "source file not found"
    if p.stat().st_size > MAX_BYTES:
        return "failed", EXIT_INPUT, "file above the 500 MB cap (§4.12.9)"
    try:
        prolog_check(p)
        from lxml import etree
        it = etree.iterparse(str(p), events=("start",), resolve_entities=False, no_network=True,
                             load_dtd=False, huge_tree=True)
        _, el = next(it)
    except LandXmlError as exc:
        return "failed", exc.code, exc.detail
    except ImportError:
        return "failed", EXIT_DEPENDENCY, "lxml is not installed"
    except Exception as exc:  # XMLSyntaxError before the root element, StopIteration
        return "failed", EXIT_INPUT, f"malformed XML: {str(exc)[:200]}"
    if local(el.tag) != "LandXML":
        return "skipped", EXIT_INPUT, f"not LandXML (root element {local(el.tag)})"
    ns = el.tag[1:].split("}")[0] if el.tag.startswith("{") else ""
    if ns not in NAMESPACES:
        return "failed", EXIT_INPUT, f"unknown LandXML namespace: {ns or '(none)'}"
    return "failed", EXIT_INPUT, "no ingestion file: parse_landxml.py not run or failed"


# ============================================================================ helpers
def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def fnum(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def children(el, name):
    return [c for c in el if local(c.tag) == name]


def free(el):
    """Release a processed element and its earlier siblings (streaming)."""
    el.clear()
    parent = el.getparent()
    if parent is not None:
        while el.getprevious() is not None:
            del parent[0]


class Warn:
    def __init__(self):
        self.items = []

    def add(self, code, detail):
        self.items.append({"code": code, "detail": str(detail)[:500]})


class AxisTally:
    """Counts of the EPSG:32638 sanity check per object (§4.12.6)."""
    def __init__(self):
        self.c = Counter()

    def check(self, e, n):
        self.c[g.axis_check(e, n)] += 1

    def report(self, w, what, active):
        if not active:
            return
        if self.c["swapped"]:
            w.add("AXIS_ORDER_SUSPECT", f"{what}: {self.c['swapped']} of {sum(self.c.values())} points fit "
                                        "EPSG:32638 only with Easting and Northing exchanged")
        if self.c["outside"]:
            w.add("COORD_OUTSIDE_UTM_RANGE", f"{what}: {self.c['outside']} points outside the EPSG:32638 "
                                             "Easting / Northing ranges either way")


def en(pt):
    return [r3(pt[0]), r3(pt[1])]


# ============================================================================ alignments
def read_alignment(el, units, w, axis_active):
    name = el.get("name") or ""
    sta0, length = fnum(el.get("staStart")), fnum(el.get("length"))
    dir_unit = units.get("directionUnit")
    tally = AxisTally()
    elements, counts, gaps, unread = [], Counter(), [], Counter()
    cg = child(el, "CoordGeom")
    run_sta = sta0 if sta0 is not None else 0.0
    prev_end = None
    for c in (list(cg) if cg is not None else []):
        kind = local(c.tag)
        if kind not in ("Line", "Curve", "Spiral"):
            unread[kind] += 1
            continue
        try:
            st, ed = g.ne_text_to_en(child(c, "Start").text)[:2], g.ne_text_to_en(child(c, "End").text)[:2]
        except (AttributeError, g.GeomError) as exc:
            w.add("ELEMENT_NOT_READ", f"alignment {name}: {kind} {len(elements) + 1} has no readable Start / End ({exc})")
            continue
        for p in (st, ed):
            tally.check(*p)
        e = {"index": len(elements) + 1, "type": {"Line": "line", "Curve": "arc", "Spiral": "spiral"}[kind],
             "start": en(st), "end": en(ed), "length": r3(fnum(c.get("length"))), "length_computed": None,
             "radius": None, "rot": c.get("rot"), "spi_type": None, "radius_start": None, "radius_end": None,
             "center": None, "pi": None, "dir_start_deg": None, "dir_end_deg": None,
             "sta_start": None}
        if kind == "Line":
            e["length_computed"] = r3(g.dist2d(st, ed))
            d = fnum(c.get("dir"))   # recorded as read (converted to degrees); convention not interpreted
            e["dir_start_deg"] = e["dir_end_deg"] = r3(g.to_degrees(d, dir_unit)) if d is not None else None
        elif kind == "Curve":
            if (c.get("crvType") or "arc") != "arc":
                w.add("CURVE_TYPE_NOT_ARC", f"alignment {name}: curve {e['index']} crvType {c.get('crvType')}; arc formulas used")
            ctr_el, pi_el = child(c, "Center"), child(c, "PI")
            ctr = g.ne_text_to_en(ctr_el.text)[:2] if ctr_el is not None and ctr_el.text else None
            if pi_el is not None and pi_el.text:
                e["pi"] = en(g.ne_text_to_en(pi_el.text))
            rad = fnum(c.get("radius")) or (g.dist2d(ctr, st) if ctr else None)
            e["radius"] = r3(rad)
            if ctr:
                e["center"] = en(ctr)
                if e["rot"] in ("cw", "ccw"):
                    e["length_computed"] = r3(g.arc_length(st, ctr, ed, e["rot"], rad))
            for k, a in (("dir_start_deg", "dirStart"), ("dir_end_deg", "dirEnd")):
                v = fnum(c.get(a))
                e[k] = r3(g.to_degrees(v, dir_unit)) if v is not None else None
            if rad:
                e["_r"] = rad
        else:  # Spiral
            e["spi_type"] = c.get("spiType")
            rs, re_ = g.parse_radius(c.get("radiusStart")), g.parse_radius(c.get("radiusEnd"))
            e["radius_start"] = "INF" if rs is not None and math.isinf(rs) else r3(rs)
            e["radius_end"] = "INF" if re_ is not None and math.isinf(re_) else r3(re_)
            pi_el = child(c, "PI")
            pi = g.ne_text_to_en(pi_el.text)[:2] if pi_el is not None and pi_el.text else None
            if pi:
                e["pi"] = en(pi)
            d0 = fnum(c.get("dirStart"))
            e["dir_start_deg"] = r3(g.to_degrees(d0, dir_unit)) if d0 is not None else None
            az0 = g.azimuth(st, pi) if pi else None    # start tangent from geometry (Start -> PI) only
            d1 = fnum(c.get("dirEnd"))
            e["dir_end_deg"] = r3(g.to_degrees(d1, dir_unit)) if d1 is not None else None
            L = fnum(c.get("length"))
            if (e["spi_type"] or "clothoid") == "clothoid" and L and az0 is not None and e["rot"] in ("cw", "ccw"):
                calc = g.clothoid_point(st, az0, L, rs, re_, e["rot"], L)
                e["end_check_m"] = r3(g.dist2d(calc, ed))
                if e["end_check_m"] > 0.01:
                    w.add("SPIRAL_END_MISMATCH", f"alignment {name}: spiral {e['index']} End differs from the "
                                                 f"clothoid computed from its attributes by {e['end_check_m']} m")
            for rr in (rs, re_):
                if rr and not math.isinf(rr):
                    e.setdefault("_rs", []).append(rr)
        if e["length"] is not None and e["length_computed"] is not None and abs(e["length"] - e["length_computed"]) > TOL:
            w.add("ELEMENT_LENGTH_MISMATCH", f"alignment {name}: {e['type']} {e['index']} length {e['length']} "
                                             f"vs geometry {e['length_computed']}")
        s_attr = fnum(c.get("staStart"))
        e["sta_start"] = r3(s_attr if s_attr is not None else run_sta)
        run_sta = (s_attr if s_attr is not None else run_sta) + (e["length"] if e["length"] is not None else (e["length_computed"] or 0.0))
        if prev_end is not None:
            gap = g.dist2d(prev_end, st)
            if gap > TOL:
                gaps.append({"after_element": e["index"] - 1, "gap_m": r3(gap)})
        prev_end = ed
        counts[e["type"]] += 1
        elements.append(e)
    radii = [x.pop("_r") for x in elements if "_r" in x]
    spiral_r = [r for x in elements for r in x.pop("_rs", [])]
    for kind, n in unread.items():
        w.add("ELEMENT_NOT_EXTRACTED", f"alignment {name}: {n} CoordGeom element(s) {kind} not extracted")
    if gaps:
        w.add("CONTINUITY_GAP", f"alignment {name}: {len(gaps)} gap(s) > {TOL} m between elements")
    tally.report(w, f"alignment {name}", axis_active)
    eqs = [{"sta_back": r3(fnum(q.get("staBack"))), "sta_ahead": r3(fnum(q.get("staAhead"))),
            "sta_internal": r3(fnum(q.get("staInternal"))), "desc": q.get("desc")}
           for q in children(el, "StaEquation")]
    other = Counter(local(c.tag) for c in el if local(c.tag) not in ALIGN_CHILDREN_READ)
    alignment = {"name": name, "desc": el.get("desc"), "sta_start": r3(sta0), "length": r3(length),
                 "elements": elements, "station_equations": eqs,
                 "element_counts": {k: counts.get(k, 0) for k in ("line", "arc", "spiral")},
                 "min_radius": r3(min(radii)) if radii else None,
                 "min_spiral_radius": r3(min(spiral_r)) if spiral_r else None,
                 "continuity_gaps": gaps}
    profiles = [p for pr in children(el, "Profile") for p in read_profile(pr, alignment, w)]
    return alignment, profiles, other


def read_profile(pr, alignment, w):
    out = []
    aname = alignment["name"]
    lo = alignment["sta_start"]
    hi = None if lo is None or alignment["length"] is None else lo + alignment["length"]
    has_geom = bool(alignment["elements"]) and hi is not None
    if not has_geom:
        w.add("PROFILE_WITHOUT_ALIGNMENT", f"profile(s) of alignment {aname}: the alignment has no "
                                           "geometry or no length; stations not checked")
    for pa in children(pr, "ProfAlign"):
        pts = []
        for c in pa:
            kind = local(c.tag)
            if kind not in ("PVI", "ParaCurve", "UnsymParaCurve", "CircCurve"):
                continue
            v = g.numbers(c.text)
            if len(v) < 2:
                w.add("PVI_NOT_READ", f"profile {pa.get('name')} of {aname}: {kind} without station / elevation")
                continue
            if kind == "PVI":
                L = None
            elif kind == "UnsymParaCurve":
                L = (fnum(c.get("lengthIn")) or 0.0) + (fnum(c.get("lengthOut")) or 0.0)
            else:
                L = fnum(c.get("length"))
            pts.append({"kind": kind, "sta": v[0], "elev": v[1], "length": L,
                        "radius": fnum(c.get("radius")) if kind == "CircCurve" else None})
        pts.sort(key=lambda p: p["sta"])
        tangents, curves = [], []
        for a, b in zip(pts, pts[1:]):
            tangents.append({"sta_from": r3(a["sta"]), "sta_to": r3(b["sta"]), "grade_pct": round(g.grade_pct(
                (a["sta"], a["elev"]), (b["sta"], b["elev"])), 4)})
        for i, p in enumerate(pts):
            if p["kind"] == "PVI":
                continue
            if i == 0 or i == len(pts) - 1:
                w.add("CURVE_AT_PROFILE_END", f"profile {pa.get('name')} of {aname}: vertical curve at station "
                                              f"{r3(p['sta'])} has no tangent on one side; K not computed")
                curves.append({"type": p["kind"], "pvi_sta": r3(p["sta"]), "pvi_elev": r3(p["elev"]),
                               "length": r3(p["length"]), "radius": r3(p["radius"]), "g_in_pct": None,
                               "g_out_pct": None, "a_pct": None, "k": None, "curve": None})
                continue
            gi, go = tangents[i - 1]["grade_pct"], tangents[i]["grade_pct"]
            a_, k, kind = g.curve_k(p["length"] or 0.0, gi, go)
            curves.append({"type": p["kind"], "pvi_sta": r3(p["sta"]), "pvi_elev": r3(p["elev"]),
                           "length": r3(p["length"]), "radius": r3(p["radius"]), "g_in_pct": gi, "g_out_pct": go,
                           "a_pct": round(a_, 4), "k": round(k, 2) if k is not None else None,
                           "curve": kind if kind != "none" else None})
        outside = [r3(p["sta"]) for p in pts if has_geom and (p["sta"] < lo - TOL or p["sta"] > hi + TOL)]
        if outside:
            w.add("PROFILE_STATION_OUTSIDE_ALIGNMENT", f"profile {pa.get('name')} of {aname}: {len(outside)} PVI "
                                                       f"station(s) outside {lo}–{r3(hi)}: {outside[:5]}")
        out.append({"alignment": aname, "name": pa.get("name"), "desc": pa.get("desc"), "kind": "design",
                    "pvis": [{"sta": r3(p["sta"]), "elev": r3(p["elev"]), "type": p["kind"]} for p in pts],
                    "tangents": tangents, "curves": curves, "points": None, "sta_range": None, "elev_range": None})
    for ps in children(pr, "ProfSurf"):
        v = []
        for pl in children(ps, "PntList2D"):
            nums = g.numbers(pl.text)
            v += [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        out.append({"alignment": aname, "name": ps.get("name"), "desc": ps.get("desc"), "kind": "existing",
                    "pvis": [], "tangents": [], "curves": [], "points": len(v),
                    "sta_range": [r3(min(p[0] for p in v)), r3(max(p[0] for p in v))] if v else None,
                    "elev_range": [r3(min(p[1] for p in v)), r3(max(p[1] for p in v))] if v else None})
    return out


# ============================================================================ surfaces
class SurfaceAcc:
    def __init__(self, el):
        self.name, self.desc = el.get("name") or "", el.get("desc")
        self.points, self.order = {}, []
        self.faces, self.invisible, self.non_tri = [], 0, 0
        self.breaklines, self.brk_points, self.boundaries = [], 0, 0
        self.definition, self.faces_element = None, False
        self.dup_ids, self.tally = 0, AxisTally()
        self.other = Counter()

    def point(self, el):
        pid = el.get("id")
        p = g.ne_text_to_en(el.text)
        if len(p) != 3:
            raise g.GeomError(f"surface point {pid} has no elevation")
        if pid in self.points:
            self.dup_ids += 1
            return
        self.points[pid] = p
        self.order.append(pid)
        self.tally.check(p[0], p[1])

    def face(self, el):
        ids = (el.text or "").split()
        if el.get("i") == "1":
            self.invisible += 1
            return
        if len(ids) == 3:
            self.faces.append(tuple(ids))
        elif len(ids) == 4:
            self.non_tri += 1
            self.faces += [(ids[0], ids[1], ids[2]), (ids[0], ids[2], ids[3])]
        else:
            self.non_tri += 1

    def breakline(self, el):
        pts = []
        for pl in el:
            t = local(pl.tag)
            if t == "PntList3D":
                pts += g.ne_list_to_en(pl.text, 3)
            elif t == "PntList2D":
                pts += [(p[0], p[1], None) for p in g.ne_list_to_en(pl.text, 2)]
        for p in pts:
            self.tally.check(p[0], p[1])
        self.brk_points += len(pts)
        self.breaklines.append({"brk_type": el.get("brkType"), "desc": el.get("desc"),
                                "points": [[r3(p[0]), r3(p[1]), r3(p[2])] for p in pts]})

    def finish(self, idx, stem, w, axis_active):
        missing, a2, a3, kept = 0, [], [], []
        for f in self.faces:
            try:
                p = [self.points[i] for i in f]
            except KeyError:
                missing += 1
                continue
            kept.append(list(f))
            a2.append(g.tri_area_2d(*p))
            a3.append(g.tri_area_3d(*p))
        if missing:
            w.add("FACE_MISSING_POINT", f"surface {self.name}: {missing} face(s) refer to a missing point id; excluded")
        if self.dup_ids:
            w.add("DUPLICATE_POINT_ID", f"surface {self.name}: {self.dup_ids} duplicate point id(s); first kept")
        if self.non_tri:
            w.add("FACE_NOT_TRIANGLE", f"surface {self.name}: {self.non_tri} face(s) with other than 3 points "
                                       "(quads split in two; others excluded)")
        tin = self.faces_element and bool(self.faces or self.invisible)
        if not tin:
            w.add("TIN_NOT_BUILT", f"surface {self.name}: no Faces; areas not computed")
        self.tally.report(w, f"surface {self.name}", axis_active)
        d = self.definition or {}
        declared = {"area_2d": r2(fnum(d.get("area2DSurf"))), "area_3d": r2(fnum(d.get("area3DSurf"))),
                    "elev_min": r3(fnum(d.get("elevMin"))), "elev_max": r3(fnum(d.get("elevMax")))}
        pts = list(self.points.values())
        comp = {"area_2d": r2(math.fsum(a2)) if tin else None, "area_3d": r2(math.fsum(a3)) if tin else None,
                "elev_min": r3(min(p[2] for p in pts)) if pts else None,
                "elev_max": r3(max(p[2] for p in pts)) if pts else None}
        dvc = {}
        for k in declared:
            if declared[k] is None or comp[k] is None:
                dvc[k] = None
            else:
                dvc[k] = (r2 if k.startswith("area") else r3)(comp[k] - declared[k])
        side = {"template": "landxml-surface-1.0", "surface": self.name, "index": idx,
                "points": [[pid, r3(self.points[pid][0]), r3(self.points[pid][1]), r3(self.points[pid][2])]
                           for pid in self.order],
                "faces": kept, "breaklines": self.breaklines}
        side_bytes = dumps_side(side)
        entry = {"name": self.name, "desc": self.desc, "surf_type": d.get("surfType"),
                 "declared": declared, "computed": comp, "declared_vs_computed": dvc, "tin_built": tin,
                 "counts": {"points": len(self.points), "faces": len(self.faces) + self.invisible,
                            "faces_visible": len(self.faces), "faces_invisible": self.invisible,
                            "faces_excluded_missing_point": missing, "breaklines": len(self.breaklines),
                            "breakline_points": self.brk_points, "boundaries": self.boundaries,
                            "duplicate_point_ids": self.dup_ids},
                 "extents": {"e_min": r3(min(p[0] for p in pts)) if pts else None,
                             "e_max": r3(max(p[0] for p in pts)) if pts else None,
                             "n_min": r3(min(p[1] for p in pts)) if pts else None,
                             "n_max": r3(max(p[1] for p in pts)) if pts else None},
                 "tin_file": f"{stem}.surface-{idx}.json", "tin_sha256": sha256_bytes(side_bytes)}
        if self.non_tri:   # quads were split: report faces as read, not as triangles
            entry["counts"]["faces"] = None
        for k, n in self.other.items():
            w.add("NOT_EXTRACTED", f"surface {self.name}: {n} {k} element(s) not extracted")
        return entry, side, side_bytes


# ============================================================================ main parse
def parse(path, w):
    from lxml import etree
    stack, inventory, header = [], Counter(), {}
    units, crs_el = {}, None
    alignments, profiles, surfaces, sides = [], [], [], []
    align_other = Counter()
    cur, version = None, None
    stem = Path(path).name[:-4] if Path(path).name.lower().endswith(".xml") else Path(path).name
    it = etree.iterparse(str(path), events=("start", "end"), resolve_entities=False, no_network=True,
                         load_dtd=False, huge_tree=True, remove_comments=True, remove_pis=True)
    axis_active = True        # refined once CoordinateSystem is read (it precedes the data in Civil 3D files)
    for event, el in it:
        if event == "start":
            name = local(el.tag)
            if not stack:
                if name != "LandXML":
                    raise NotLandXml(f"root element is {name}")
                ns = el.tag[1:].split("}")[0] if el.tag.startswith("{") else ""
                if ns not in NAMESPACES:
                    raise LandXmlError(EXIT_INPUT, f"unknown LandXML namespace: {ns or '(none)'}")
                version = NAMESPACES[ns]
                header.update(version=el.get("version"), date=el.get("date"), time=el.get("time"),
                              namespace_version=version)
            elif len(stack) == 1:
                inventory[name] += 1
            elif name == "Surface" and stack[-1] == "Surfaces":
                cur = SurfaceAcc(el)
            stack.append(name)
            continue
        name = stack.pop()
        parent = stack[-1] if stack else None
        if cur is not None:
            if name == "P" and parent == "Pnts":
                cur.point(el); free(el); continue
            if name == "F" and parent == "Faces":
                cur.face(el); free(el); continue
            if name == "Breakline":
                cur.breakline(el); free(el); continue
            if name == "Boundary":
                cur.boundaries += 1; free(el); continue
            if name == "Faces":
                cur.faces_element = True
            elif name == "Definition" and parent == "Surface":
                cur.definition = dict(el.attrib)
            elif parent == "Surface" and name not in SURFACE_CHILDREN_READ:
                cur.other[name] += 1
            elif name == "Surface":
                entry, side, sb = cur.finish(len(surfaces) + 1, stem, w, axis_active)
                surfaces.append(entry); sides.append((entry["tin_file"], side, sb))
                cur = None
                free(el)
            continue
        if name == "Alignment" and parent == "Alignments":
            a, prof, other = read_alignment(el, units, w, axis_active)
            alignments.append(a); profiles += prof; align_other.update(other)
            free(el)
            continue
        if len(stack) == 1:   # a top-level child ended
            if name == "Units":
                m, imp = child(el, "Metric"), child(el, "Imperial")
                u = m if m is not None else imp
                units = dict(u.attrib) if u is not None else {}
                header["units"] = {"system": local(u.tag) if u is not None else None,
                                   "linear": units.get("linearUnit"), "area": units.get("areaUnit"),
                                   "angular": units.get("angularUnit"), "direction": units.get("directionUnit")}
            elif name == "CoordinateSystem":
                crs_el = dict(el.attrib)
                code = crs_el.get("epsgCode")
                axis_active = code in (None, "", "32638")
            elif name == "Project":
                header["project_name"] = el.get("name")
            elif name == "Application":
                header["application"] = {"name": el.get("name"), "version": el.get("version"),
                                         "manufacturer": el.get("manufacturer"), "time_stamp": el.get("timeStamp")}
            el.clear()
    return {"header": header, "units": units, "crs_el": crs_el, "inventory": inventory, "alignments": alignments,
            "profiles": profiles, "surfaces": surfaces, "sides": sides, "align_other": align_other}


def build(args, src, res, w, pro):
    import hashlib
    header = res["header"]
    crs_el, units = res["crs_el"], res["units"]
    declared = None
    if not crs_el:
        w.add("CRS_MISSING", "no CoordinateSystem element; crs.declared = null (no reprojection)")
        cs = None
    else:
        code = (crs_el.get("epsgCode") or "").strip()
        if code.isdigit() and 4 <= len(code) <= 5:
            declared = f"EPSG:{code}"
            if code != "32638":
                w.add("CRS_NOT_32638", f"declared {declared}; values not reprojected")
        else:
            w.add("CRS_MISSING", f"CoordinateSystem without a usable epsgCode ({code or 'none'})")
        wkt = crs_el.get("ogcWktCode")
        cs = {"epsg_code": code or None, "desc": crs_el.get("desc"),
              "horizontal_datum": crs_el.get("horizontalDatum"),
              "ogc_wkt_sha256": hashlib.sha256(wkt.encode("utf-8")).hexdigest() if wkt else None}
    header["coordinate_system"] = cs
    header.setdefault("units", None)
    header.setdefault("project_name", None)
    header.setdefault("application", None)
    lin = (units.get("linearUnit") or "").strip()
    unit_map = {"meter": "m", "metre": "m", "millimeter": "mm"}
    crs_units = unit_map.get(lin.lower(), lin or "unknown")
    if crs_units != "m":
        w.add("UNITS_NOT_METRES", f"linear unit {lin or 'not declared'}; values recorded as read, not converted")
    inv = {k: v for k, v in sorted(res["inventory"].items()) if k not in EXTRACTED_TOP}
    for k, v in inv.items():
        w.add("NOT_EXTRACTED", f"{v} top-level {k} element(s) not extracted in this version")
    for k, v in sorted(res["align_other"].items()):
        inv[f"Alignment/{k}"] = v
        w.add("NOT_EXTRACTED", f"{v} {k} element(s) inside alignments not extracted")
    carriers = []
    if any(a["elements"] for a in res["alignments"]):
        carriers.append("landxml_alignment")
    if any(p["kind"] == "design" and p["pvis"] for p in res["profiles"]):
        carriers.append("landxml_profile")
    if res["surfaces"]:
        carriers.append("landxml_surface")
    if not carriers:
        w.add("NO_CARRIERS", "no alignment geometry, design profile or surface in this file")
    name = Path(args.source_path).name
    if pro != "aicc_export" and name[:-4].upper().endswith(AICC_EXPORT_SUFFIX):
        w.add("PROVENANCE_NAME_MISMATCH", f"file name ends {AICC_EXPORT_SUFFIX} but --provenance is {pro}")
    if pro == "aicc_export" and not name[:-4].upper().endswith(AICC_EXPORT_SUFFIX):
        w.add("PROVENANCE_NAME_MISMATCH", f"--provenance aicc_export but the file name does not end {AICC_EXPORT_SUFFIX}")
    docno = number_from_name(name)
    return {
        "template_version": "1.1",
        "source": {"path": args.source_path, "sha256": src["sha256"], "bytes": src["bytes"], "document_no": docno,
                   "revision": revision_of(docno), "file_type": "landxml", "provenance": pro},
        "extractor": {"skill": "parsing-landxml", "script": "parse_landxml.py", "version": __version__,
                      "converter": {"name": None, "version": None}},
        "crs": {"declared": declared, "units": crs_units, "insunits": None},
        "title_block": None,
        "content": {"header": header, "carriers": carriers, "alignments": res["alignments"],
                    "profiles": res["profiles"], "surfaces": res["surfaces"], "inventory": inv},
        "warnings": w.items,
        "meta": {"extracted_at": datetime.now(KSA).isoformat(timespec="seconds"),
                 "geom_version": g.__version__},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--provenance", required=True, choices=["parsons", "employer", "aicc_export"])
    ap.add_argument("--source-path")
    a = ap.parse_args(argv)
    a.source_path = (a.source_path or a.inp).replace("\\", "/")
    try:
        from lxml import etree  # noqa: F401
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:
        emit({"status": "error", "code": EXIT_DEPENDENCY, "detail": f"package missing: {exc.name}"})
        return EXIT_DEPENDENCY
    try:
        p = check_path(a.inp, must_exist=True)
        low = p.name.lower()
        if low.endswith(SIDE_SUFFIXES) or not low.endswith(".xml"):
            emit({"status": "skipped", "code": EXIT_OK, "file": a.source_path,
                  "detail": "not a LandXML file (Civil 3D side file or not .xml); nothing written"})
            return EXIT_OK
        out_arg = Path(a.out)
        if out_arg.name.lower().endswith(".xml.json"):
            if out_arg.name != f"{p.name}.json":
                raise LandXmlError(EXIT_INPUT, f"--out must be named {p.name}.json")
            out, main_path = out_arg.parent, out_arg
        else:
            out, main_path = out_arg, out_arg / f"{p.name}.json"
        size = p.stat().st_size
        if size > MAX_BYTES:
            raise LandXmlError(EXIT_INPUT, f"file is {size} bytes, above the 500 MB cap (§4.12.9)")
        pro_info = prolog_check(p)
        src = {"sha256": sha256_file(p), "bytes": size}
        w = Warn()
        if pro_info["doctype"]:
            w.add("DOCTYPE_PRESENT", "DOCTYPE without entity declarations; not loaded")
        res = parse(p, w)
        doc = build(a, src, res, w, a.provenance)
        vi = Draft202012Validator(json.loads(INGESTION_SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker())
        vs = Draft202012Validator(json.loads(SURFACE_SCHEMA.read_text(encoding="utf-8")))
        errs = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message[:160]}" for e in vi.iter_errors(doc)]
        for fname, side, _ in res["sides"]:
            errs += [f"{fname}: {'/'.join(map(str, e.absolute_path))}: {e.message[:160]}" for e in vs.iter_errors(side)]
        if errs:
            emit({"status": "fail", "code": EXIT_SCHEMA, "errors": errs[:20]})
            return EXIT_SCHEMA
        for fname, _, sb in res["sides"]:       # side files first: the main file carries their hashes
            atomic_write_bytes(out / fname, sb)
        atomic_write_bytes(main_path, dumps_main(doc))
    except NotLandXml as exc:
        emit({"status": "skipped", "code": EXIT_OK, "file": a.source_path, "detail": f"not LandXML ({exc}); nothing written"})
        return EXIT_OK
    except LandXmlError as exc:
        emit({"status": "error", "code": exc.code, "detail": exc.detail})
        return exc.code
    except g.GeomError as exc:
        emit({"status": "error", "code": EXIT_INPUT, "detail": f"unreadable geometry: {exc}"})
        return EXIT_INPUT
    except Exception as exc:  # lxml XMLSyntaxError and other malformed input
        if type(exc).__name__ in ("XMLSyntaxError", "ParseError"):
            emit({"status": "error", "code": EXIT_INPUT, "detail": f"malformed XML: {str(exc)[:300]}"})
            return EXIT_INPUT
        raise
    c = doc["content"]
    emit({"status": "ok", "out": main_path.as_posix(), "side_files": [s[0] for s in res["sides"]],
          "carriers": c["carriers"], "alignments": len(c["alignments"]), "profiles": len(c["profiles"]),
          "surfaces": len(c["surfaces"]), "warnings": len(doc["warnings"])})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
