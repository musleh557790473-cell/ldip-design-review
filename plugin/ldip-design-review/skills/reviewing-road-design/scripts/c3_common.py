"""Shared helpers for skill 10 phase C3 (SPC-0006 Rev J §4.13G). No network, no shell calls."""
__version__ = "0.1.0"

import math
import re
from pathlib import Path

from location_common import closed_polygons, params as all_params, shapely_mods, site_params, xy
from road_common import (EXIT_INPUT, PRESENCE_ELEMENTS, RoadError, applies, param, read_json, rules_index,
                         source_of, term_hits, tol)

DETAIL_TERMS = {
    "ROD-044": [r"speed\s+humps?"],
    "ROD-045": [r"speed\s+tables?", r"raised\s+crossings?", r"raised\s+crosswalks?"],
    "ROD-046": [r"raised\s+intersections?"],
}
VALUE_LABELS = {
    "length": [r"\blength\b", r"\boverall\b"],
    "height": [r"\bheight\b", r"\bh\b"],
    "flat_top": [r"flat\s+top", r"plateau", r"flat"],
    "ramp": [r"\bramp\b", r"slope"],
}
ACCESSIBLE_DEFAULT = [r"wheelchair", r"\bisa\b", r"accessible", r"disabled", r"handicap"]
# SRC-25 accessible-bay table (total bays → required accessible). TJ9: 40→1, 120→4.
SRC25_TABLE = [(50, 1), (75, 2), (100, 3), (150, 4), (200, 5), (300, 7), (400, 8), (500, 9)]


def c3_params(site=None):
    p = all_params()
    c3 = dict(p.get("c3") or {})
    site_p = {}
    if site:
        site_p, _ = site_params(site)
    return c3, site_p, p


def src25_required(total):
    req = 1
    for lim, n in SRC25_TABLE:
        if total <= lim:
            return n
        req = n
    return req


def phase_of(stage_map, stage):
    for name, info in (stage_map or {}).items():
        if name == stage or name.lower() in stage.lower() or stage.lower() in name.lower():
            return info.get("phase")
    if "60" in stage or "dd" in stage.lower() or "detailed" in stage.lower():
        return "DD"
    if "30" in stage or "pd" in stage.lower() or "concept" in stage.lower():
        return "PD"
    return None


def gate(rules, skipped, rid, stage_map, stage, carriers_needed=None):
    """Return (rule, phase) or (None, reason) when the rule must not run."""
    if rid in {s.get("rule_id") for s in skipped}:
        return None, f"{rid} inactive / skipped"
    rule = rules.get(rid)
    if rule is None:
        return None, f"{rid} not in rules file"
    if rule.get("state") not in ("active", "advisory"):
        return None, f"{rid} state {rule.get('state')!r} — not run"
    phase = phase_of(stage_map, stage)
    req = rule.get("required_from_phase") or "DD"
    if phase == "PD" and req == "DD":
        return None, "not required at stage"
    return rule, phase


def fail_rec(rid, pname, subject, observed, required, op, unit, src, *, cls=None, result="fail",
             assigned=None, link_cap=None, status_override=None, issue_type=None, mode_tag=None,
             note=None, xy_pt=None, handles=None, extract=None, criterion=None):
    ev = {**(src or {}), "source_type": (src or {}).get("source_type") or "dwg_vector",
          "handles": handles or [], "extract": extract or f"{subject}: {observed}"}
    if xy_pt is not None:
        ev["xy"] = list(xy_pt) if not isinstance(xy_pt, list) else xy_pt
    r = {"rule_id": rid, "parameter": pname, "class": cls, "criterion": criterion or pname,
         "subject": subject, "observed": observed, "required": required, "comparison": op,
         "tolerance": 0.0, "unit": unit, "result": result,
         "evidence": ev}
    if assigned:
        r["assigned"] = assigned
    if link_cap:
        r["link_cap"] = link_cap
    if status_override:
        r["status_override"] = status_override
    if issue_type:
        r["issue_type"] = issue_type
    if mode_tag:
        r["mode_tag"] = mode_tag
    if note:
        r["note"] = note
    return r


def parse_file_args(items):
    out = []
    for it in items or []:
        if "=" not in it:
            raise RoadError(EXIT_INPUT, f"--file needs carrier=path, got {it!r}")
        carrier, path = it.split("=", 1)
        out.append((carrier.strip(), path.strip()))
    return out


def load_carrier_files(file_args):
    files = []
    for carrier, path in file_args:
        ing = read_json(path)
        files.append((carrier, path, ing, source_of(ing)))
    return files


def texts_of(ing):
    return [t for t in (ing.get("content") or {}).get("texts", [])
            if t.get("space", "model") == "model" and isinstance(t.get("text"), str) and t.get("insert")]


def hit_terms(text, terms):
    return [h for h in term_hits(text, terms) if not h[3]]


def device_type_of(labels):
    blob = " ".join(labels).lower()
    if re.search(r"chicane", blob):
        return "chicane"
    if re.search(r"mini[- ]?roundabout|mini\s+rb", blob):
        return "mini_roundabout"
    if re.search(r"raised\s+intersection", blob):
        return "raised_intersection"
    if re.search(r"speed\s+table|raised\s+crossing|raised\s+crosswalk", blob):
        return "speed_table"
    if re.search(r"speed\s+hump|hump", blob):
        return "speed_hump"
    return "calming"


def merge_calming(files, g, merge_m=10.0, layers=None):
    """B2 DG-4 style: traffic-calming hits merged within merge_m."""
    terms = PRESENCE_ELEMENTS["traffic_calming"]["terms"]
    pts = []
    for carrier, path, ing, src in files:
        for t in texts_of(ing):
            if layers and (t.get("layer") or "") not in layers and (t.get("layer_short") or "") not in layers:
                # still allow term hits on any layer when layers given only as preference
                pass
            hs = hit_terms(t["text"], terms)
            if not hs:
                continue
            pt = g.Point(t["insert"][0], t["insert"][1])
            pts.append((pt, t["text"].strip(), src, t.get("handle"), t.get("layer")))
        for b in (ing.get("content") or {}).get("blocks", []):
            name = str(b.get("name") or "")
            if not hit_terms(name, terms):
                continue
            ins = b.get("insert") or [0, 0]
            pts.append((g.Point(ins[0], ins[1]), name, src, b.get("handle"), b.get("layer")))
        for layer, handle, verts in [(p.get("layer"), p.get("handle"), p.get("vertices") or [])
                                     for p in (ing.get("content") or {}).get("polylines", [])]:
            if not hit_terms(layer or "", terms) or len(verts) < 2:
                continue
            poly = g.LineString([tuple(v[:2]) for v in verts])
            pts.append((poly.centroid, layer, src, handle, layer))
    pts = sorted(pts, key=lambda t: (round(t[0].x, 3), round(t[0].y, 3), t[1]))
    clusters = []
    for pt, label, src, handle, layer in pts:
        c = next((k for k in clusters if k["seed"].distance(pt) <= merge_m), None)
        if c is None:
            c = {"seed": pt, "pts": [], "labels": [], "src": src, "handles": []}
            clusters.append(c)
        c["pts"].append(pt)
        if label not in c["labels"]:
            c["labels"].append(label)
        if handle:
            c["handles"].append(handle)
    out = []
    for i, c in enumerate(clusters, 1):
        cx = sum(p.x for p in c["pts"]) / len(c["pts"])
        cy = sum(p.y for p in c["pts"]) / len(c["pts"])
        out.append({"id": f"CD{i:03d}", "xy": xy((cx, cy)), "point": g.Point(cx, cy),
                    "hits": len(c["pts"]), "labels": c["labels"][:5], "type": device_type_of(c["labels"]),
                    "src": c["src"], "handles": c["handles"]})
    return out


def nearest_road(net, pt, g):
    best, best_d, best_ch = None, 1e9, None
    for name, road in (net.get("roads") or {}).items():
        geom = road.get("geometry") or []
        if len(geom) < 2:
            continue
        line = g.LineString([tuple(p[:2]) for p in geom])
        d = line.distance(pt)
        if d < best_d:
            best_d = d
            best = name
            # chainage along line
            best_ch = line.project(pt)
    return best, best_ch, best_d


def min_rotated_rect(poly):
    """Return (width, length, angle_deg) of the minimum-area rotated rectangle; width ≤ length."""
    mrr = poly.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords)
    edges = [math.dist(coords[i], coords[i + 1]) for i in range(3)]
    # two unique side lengths
    a, b = sorted({round(e, 6) for e in edges if e > 1e-9})[:2] if len(set(round(e, 6) for e in edges if e > 1e-9)) >= 2 else (min(edges), max(edges))
    # angle of the longer side
    longest = max(range(3), key=lambda i: math.dist(coords[i], coords[i + 1]))
    dx = coords[longest + 1][0] - coords[longest][0]
    dy = coords[longest + 1][1] - coords[longest][1]
    ang = math.degrees(math.atan2(dy, dx)) % 180
    w, L = (a, b) if a <= b else (b, a)
    return w, L, ang


def parking_layer_match(layer, patterns):
    """True when layer (short name) matches a site parking_layers entry (R-I3 / fullmatch, like curb_layers)."""
    if not patterns or not layer:
        return False
    return any(re.fullmatch(p, layer, re.I) for p in patterns)


def stall_size_ok(width, length, c3=None):
    """G-1 size gate: width and length must both fall in the configured stall ranges.

    Defaults cover parallel / perpendicular / angle / accessible bay envelopes in check_parking.SIZES
    and ACC_SIZES (≈ 2.0–5.5 m × 4.5–7.0 m). Parcel-scale polygons fail length or width.
    """
    c3 = c3 or {}
    w_lo, w_hi = (c3.get("bay_width_m") or [2.0, 5.5])[:2]
    l_lo, l_hi = (c3.get("bay_length_m") or [4.5, 7.0])[:2]
    w, L = (width, length) if width <= length else (length, width)
    return (w_lo - 1e-9 <= w <= w_hi + 1e-9) and (l_lo - 1e-9 <= L <= l_hi + 1e-9)


def angle_class(ang_to_curb, classes):
    """classes: {parallel_max, angle_lo, angle_hi, perp_min}."""
    a = abs(ang_to_curb) % 180
    a = min(a, 180 - a)
    if a <= classes.get("parallel_max", 20):
        return "parallel"
    if classes.get("angle_lo", 45) <= a <= classes.get("angle_hi", 75):
        return "angle_60"
    if a >= classes.get("perp_min", 75):
        return "perpendicular"
    return "other"


def grade_at_station(prof, sta):
    """Absolute design-profile grade (%) at an alignment station from tangents or PVI slopes."""
    for t in prof.get("tangents") or []:
        a, b = float(t.get("sta_from", 0)), float(t.get("sta_to", 0))
        lo, hi = (a, b) if a <= b else (b, a)
        if lo - 1e-6 <= float(sta) <= hi + 1e-6 and t.get("grade_pct") is not None:
            return abs(float(t["grade_pct"]))
    pvis = sorted([p for p in (prof.get("pvis") or []) if p.get("sta") is not None], key=lambda p: p["sta"])
    for i in range(len(pvis) - 1):
        a, b = pvis[i], pvis[i + 1]
        if a["sta"] - 1e-6 <= float(sta) <= b["sta"] + 1e-6:
            ds = b["sta"] - a["sta"]
            if abs(ds) < 1e-9:
                continue
            return abs((float(b["elev"]) - float(a["elev"])) / ds * 100.0)
    return None


def parse_len_height(text):
    """Return list of {kind_guess, value, unit, raw} from a detail value string."""
    t = text.strip()
    out = []
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", t)
    if m:
        out.append({"kind": "ramp", "value": f"1:{m.group(2)}" if float(m.group(1)) == 1 else f"{m.group(1)}:{m.group(2)}",
                    "ratio": float(m.group(1)) / float(m.group(2)), "unit": "ratio", "raw": t})
        return out
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", t)
    if m:
        out.append({"kind": "ramp", "value": float(m.group(1)), "unit": "%", "raw": t,
                    "ratio": float(m.group(1)) / 100.0})
        return out
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*mm", t, re.I)
    if m:
        out.append({"kind": "height", "value": float(m.group(1)), "unit": "mm", "raw": t})
        return out
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*m", t, re.I)
    if m:
        out.append({"kind": "length", "value": float(m.group(1)), "unit": "m", "raw": t})
        return out
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", t)
    if m:
        v = float(m.group(1))
        if v >= 100:  # "3700" → 3.7 m
            out.append({"kind": "length", "value": v / 1000.0, "unit": "m", "raw": t, "from_mm_digits": True})
        elif v < 1:  # 0.075 → 75 mm
            out.append({"kind": "height", "value": v * 1000.0, "unit": "mm", "raw": t})
        else:
            out.append({"kind": "length", "value": v, "unit": "m", "raw": t})
        return out
    return out
