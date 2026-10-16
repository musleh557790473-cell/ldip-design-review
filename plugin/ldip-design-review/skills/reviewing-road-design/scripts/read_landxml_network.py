#!/usr/bin/env python3
"""LandXML alignments -> named alignments in the setout.json format (SPC-0006 Rev G §4.13D.3; K19-1, DG-1).

The DD position source (K19-1): the design alignments of skill 3 LandXML ingestion files (`content.alignments`),
one alignment per name. Each CoordGeom element becomes one segment of the setout.json format that
build_network.py, cross_check.py, read_class.py and check_locations.py already read:
- Line   -> `tangent` (Start, End);
- Curve  -> `curve` with its radius; an arc is rebuilt from Start, Center and rot (or Start, PI, End when the
  centre is missing) and split into pieces of at most 120° (each piece: start, a point on the arc, end);
- Spiral -> `spiral`, flattened at `flatten_m` (1 m) by integrating the clothoid from Start (start direction
  Start -> PI, curvature linear from 1/radiusStart to 1/radiusEnd, rot); the small closing error to the
  recorded End is spread along the points and reported when above 0.5 m (SPIRAL_CLOSURE). A spiral without
  PI or rot is drawn as its chord (SPIRAL_AS_CHORD).
An alignment name that appears in two files with different geometry keeps the first (LANDXML_NAME_DIFFERS).
The output says `position_source: landxml`; positions from LandXML may reach the rule cap (Rev F §4.13C.7).
When setting-out tables also exist, this network is used and the tables are the cross-check source (K19-2,
DG-1: cross_check.py --setout).

Usage: python read_landxml_network.py --landxml <x.xml.json> [...] [--include "<regex on names>"] --out <setout.json>
Exit: 0 written (also with no alignment) · 2 input error. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import EXIT_INPUT, EXIT_OK, RoadError, atomic_write_json, emit, main_guard, read_json, source_of  # noqa: E402

FLATTEN_M = 1.0
MAX_PIECE_DEG = 120.0
CLOSURE_WARN_M = 0.5


def _r(v):
    return round(float(v), 3)


def arc_pieces(e):
    """Pieces [(start, on-arc point, end)] of an arc element, or None when it cannot be rebuilt."""
    st, ed, ctr, rad = e.get("start"), e.get("end"), e.get("center"), e.get("radius")
    if ctr and rad and e.get("rot") in ("cw", "ccw"):
        a0 = math.atan2(st[1] - ctr[1], st[0] - ctr[0])
        a1 = math.atan2(ed[1] - ctr[1], ed[0] - ctr[0])
        sweep = (a1 - a0) % (2 * math.pi) if e["rot"] == "ccw" else -((a0 - a1) % (2 * math.pi))
        if abs(sweep) < 1e-9:
            sweep = 2 * math.pi if e["rot"] == "ccw" else -2 * math.pi
        n = max(1, math.ceil(abs(math.degrees(sweep)) / MAX_PIECE_DEG))
        pt = lambda a: (ctr[0] + rad * math.cos(a), ctr[1] + rad * math.sin(a))  # noqa: E731
        out = []
        for i in range(n):
            b0, b1 = a0 + sweep * i / n, a0 + sweep * (i + 1) / n
            p0 = tuple(st) if i == 0 else pt(b0)
            p1 = tuple(ed) if i == n - 1 else pt(b1)
            out.append((p0, pt((b0 + b1) / 2), p1))
        return out
    if e.get("pi") and rad:
        return [(tuple(st), tuple(e["pi"]), tuple(ed))]
    return None


def spiral_points(e, warnings, name):
    st, ed, pi = e.get("start"), e.get("end"), e.get("pi")
    L = e.get("length") or e.get("length_computed")
    if not (pi and L and e.get("rot") in ("cw", "ccw")):
        warnings.append({"code": "SPIRAL_AS_CHORD", "detail": f"{name}: spiral {e.get('index')} without PI, length or rot"})
        return [tuple(st), tuple(ed)]
    k = lambda r: 0.0 if r in (None, "INF") or (isinstance(r, float) and math.isinf(r)) or r == 0 else 1.0 / float(r)  # noqa: E731
    k0, k1 = k(e.get("radius_start")), k(e.get("radius_end"))
    sgn = 1.0 if e["rot"] == "ccw" else -1.0
    th0 = math.atan2(pi[1] - st[1], pi[0] - st[0])
    n_int = max(10, int(L / 0.05))
    ds = L / n_int
    x, y = float(st[0]), float(st[1])
    pts, every = [(x, y)], max(1, int(round(FLATTEN_M / ds)))
    for i in range(n_int):
        s = (i + 0.5) * ds
        th = th0 + sgn * (k0 * s + (k1 - k0) * s * s / (2 * L))
        x, y = x + ds * math.cos(th), y + ds * math.sin(th)
        if (i + 1) % every == 0 or i == n_int - 1:
            pts.append((x, y))
    dx, dy = ed[0] - pts[-1][0], ed[1] - pts[-1][1]
    err = math.hypot(dx, dy)
    if err > CLOSURE_WARN_M:
        warnings.append({"code": "SPIRAL_CLOSURE", "detail": f"{name}: spiral {e.get('index')} computed end {err:.2f} m from "
                                                             "the recorded End; difference spread along the spiral"})
    m = len(pts) - 1
    return [(px + dx * i / m, py + dy * i / m) for i, (px, py) in enumerate(pts)]


def segments_of(al, warnings):
    """setout.json segments of one skill 3 alignment."""
    name, segs = al.get("name") or "", []
    for e in al.get("elements") or []:
        base = {"stations": [], "control": [], "length": e.get("length") if e.get("length") is not None else e.get("length_computed"),
                "sta_start": e.get("sta_start"), "handles": [], "element": e.get("index")}
        if e["type"] == "line":
            segs.append({**base, "tag": f"L{e['index']}", "type": "tangent", "radius": None,
                         "points": [[_r(c) for c in e["start"]], [_r(c) for c in e["end"]]]})
        elif e["type"] == "arc":
            pieces = arc_pieces(e)
            if pieces is None:
                warnings.append({"code": "ARC_AS_CHORD", "detail": f"{name}: arc {e['index']} without centre / rot or PI"})
                segs.append({**base, "tag": f"C{e['index']}", "type": "tangent", "radius": e.get("radius"),
                             "points": [[_r(c) for c in e["start"]], [_r(c) for c in e["end"]]]})
                continue
            for j, (p0, pm, p1) in enumerate(pieces):
                segs.append({**base, "tag": f"C{e['index']}" + (f".{j + 1}" if len(pieces) > 1 else ""), "type": "curve",
                             "radius": e.get("radius"), "points": [[_r(c) for c in p] for p in (p0, pm, p1)]})
        elif e["type"] == "spiral":
            segs.append({**base, "tag": f"S{e['index']}", "type": "spiral", "radius": None,
                         "points": [[_r(c) for c in p] for p in spiral_points(e, warnings, name)]})
    return segs


def alignment_line(segs):
    """Plan polyline of an alignment in element order (for stationing low points, find_low_points.py)."""
    from location_common import segment_points
    pts = []
    for s in segs:
        p = segment_points(s)
        if not p:
            continue
        if pts and math.dist(pts[-1], p[0]) < 0.01:
            p = p[1:]
        pts += [tuple(q) for q in p]
    return pts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--landxml", nargs="+", required=True)
    ap.add_argument("--include")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        inc = re.compile(a.include) if a.include else None
        alignments, warnings, sources = {}, [], []
        for p in a.landxml:
            ing = read_json(p)
            if (ing.get("source") or {}).get("file_type") not in ("xml", "landxml"):
                raise RoadError(EXIT_INPUT, f"{p}: not a LandXML ingestion file (skill 3)")
            src = source_of(ing)
            als = (ing.get("content") or {}).get("alignments") or []
            used = 0
            for al in als:
                name = al.get("name") or ""
                if not al.get("elements") or (inc and not inc.search(name)):
                    continue
                segs = segments_of(al, warnings)
                if not segs:
                    continue
                used += 1
                rec = {"segments": segs, "sheets": [src["path"]], "sta_start": al.get("sta_start"),
                       "length": al.get("length"), "station_equations": al.get("station_equations") or [],
                       "evidence": {"source_type": "landxml", "path": src["path"], "sha256": src["sha256"], "alignment": name}}
                if name not in alignments:
                    alignments[name] = rec
                elif [s["points"] for s in alignments[name]["segments"]] == [s["points"] for s in segs]:
                    alignments[name]["sheets"].append(src["path"])
                else:
                    warnings.append({"code": "LANDXML_NAME_DIFFERS", "detail": f"{name}: geometry in {Path(src['path']).name} "
                                     f"differs from {Path(alignments[name]['sheets'][0]).name}; first kept"})
            sources.append({**src, "alignments": len(als), "used": used})
        segs = [s for v in alignments.values() for s in v["segments"]]
        out = {"tool": {"script": "read_landxml_network.py", "version": __version__}, "position_source": "landxml",
               "sources": sources, "alignments": dict(sorted(alignments.items())), "warnings": warnings,
               "counts": {"alignments": len(alignments), "segments": len(segs),
                          "tangents": sum(1 for s in segs if s["type"] == "tangent"),
                          "curves": sum(1 for s in segs if s["type"] == "curve"),
                          "spirals": sum(1 for s in segs if s["type"] == "spiral")}}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, **out["counts"], "files": len(sources), "warnings": len(warnings)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
