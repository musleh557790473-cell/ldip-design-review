"""Shared helpers for the skill 10 location register (SPC-0006 Rev F §4.13C; K17-1, K19-1 ... 5, RC-1 ... 4;
Rev G §4.13D, DG-1 ... DG-5; Rev I §4.13F: circle fit and curved-run splitter for phase C2).

Geometry of setting-out segments (tangents and circular curves rebuilt from PC / PI / PT and radius),
linework side files of skill 1, and the skill parameters in references/road_class_params.json (per site:
layer names, naming code, PDF legend colours; network radii and tolerances). shapely is required.
No network, no shell calls.
"""
__version__ = "0.3.0"

import json
import math
import re
from pathlib import Path

from road_common import EXIT_DEPENDENCY, EXIT_INPUT, RoadError, read_json

PARAMS = Path(__file__).resolve().parents[1] / "references" / "road_class_params.json"


def shapely_mods():
    try:
        import shapely.geometry as g
        import shapely.ops as o
    except ImportError as exc:
        raise RoadError(EXIT_DEPENDENCY, "shapely is not installed (location register, SPC-0006 Rev F §4.13C)") from exc
    return g, o


def params():
    return json.loads(PARAMS.read_text(encoding="utf-8"))


def site_params(site):
    p = params()
    if site not in p["sites"]:
        raise RoadError(EXIT_INPUT, f"no road-class parameters for site {site} (references/road_class_params.json)")
    return p["sites"][site], p


def arc_points(p0, p1, radius, pi, step=1.0):
    """Circular arc from PC p0 to PT p1 with the given radius, bulging toward the PI; points every ~step m."""
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    c = math.dist(p0, p1) / 2
    if not radius or c == 0 or radius < c:
        return [tuple(p0), tuple(p1)]
    h = math.sqrt(radius * radius - c * c)
    ux, uy = (p1[0] - p0[0]) / (2 * c), (p1[1] - p0[1]) / (2 * c)
    nx, ny = -uy, ux
    side = 1 if ((pi[0] - mx) * nx + (pi[1] - my) * ny) < 0 else -1      # centre opposite the PI
    cx, cy = mx + side * h * nx, my + side * h * ny
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)
    da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
    n = max(4, int(abs(da) * radius / step))
    return [(cx + radius * math.cos(a0 + da * i / n), cy + radius * math.sin(a0 + da * i / n)) for i in range(n + 1)]


def full_circle(points_list, radius, step=1.0):
    """K19-4: a roundabout drawn as curve rows whose PCs / PTs close the circle -> centre and circle points."""
    pts = [p for seg in points_list for p in seg]
    if len(pts) < 2 or not radius:
        return None
    # centre: least-squares circle through the control points (they all lie on the circle)
    best = None
    for p in pts:
        for q in pts:
            if math.dist(p, q) < 1.0:
                continue
            c = math.dist(p, q) / 2
            if radius < c:
                continue
            h = math.sqrt(radius * radius - c * c)
            ux, uy = (q[0] - p[0]) / (2 * c), (q[1] - p[1]) / (2 * c)
            for s in (1, -1):
                cx = (p[0] + q[0]) / 2 + s * h * -uy
                cy = (p[1] + q[1]) / 2 + s * h * ux
                err = sum(abs(math.dist((cx, cy), r) - radius) for r in pts)
                if best is None or err < best[0]:
                    best = (err, cx, cy)
    if best is None or best[0] / len(pts) > 0.05 * radius:
        return None
    _, cx, cy = best
    n = max(16, int(2 * math.pi * radius / step))
    return (cx, cy), [(cx + radius * math.cos(2 * math.pi * i / n), cy + radius * math.sin(2 * math.pi * i / n))
                      for i in range(n + 1)]


def segment_points(seg):
    """Plan points of one setting-out segment (tangent: ends; curve: arc through PC / PT bulging to PI)."""
    pts = [tuple(p) for p in seg.get("points") or []]
    if seg["type"] == "spiral":                      # Rev G (DG-1): LandXML spiral, flattened at 1 m
        return pts if len(pts) >= 2 else None
    if seg["type"] == "tangent":
        return [pts[0], pts[-1]] if len(pts) >= 2 else None
    if len(pts) >= 3 and seg.get("radius"):
        return arc_points(pts[0], pts[-1], seg["radius"], pts[1])
    if len(pts) == 2 and seg.get("radius") and seg.get("pi"):
        return arc_points(pts[0], pts[-1], seg["radius"], seg["pi"])
    return None


def load_linework(paths):
    """[(record, source path, source sha)] from skill 1 linework side files (given as the ingestion files or the
    side files themselves)."""
    out = []
    for p in paths:
        p = Path(p)
        if p.name.endswith(".dwg.json"):
            ing = read_json(p)
            sides = [s for s in (ing.get("content") or {}).get("side_files", []) if s.get("kind") == "linework"]
            if not sides:
                raise RoadError(EXIT_INPUT, f"{p.name}: no linework side file (run skill 1 extract_dxf.py --linework)")
            p = p.parent / sides[0]["path"]
        lw = read_json(p)
        if lw.get("schema_version") != "1.0" or "records" not in lw:
            raise RoadError(EXIT_INPUT, f"{p.name}: not a linework side file")
        for r in lw["records"]:
            out.append((r, lw["source"]["path"], lw["source"]["sha256"]))
    return out


def layer_match(rec, patterns):
    short = rec.get("layer_short") or str(rec.get("layer", "")).split("$")[-1]
    return any(re.fullmatch(p, short, re.I) for p in patterns)


def lines_of(records, patterns):
    g, _ = shapely_mods()
    out = []
    for r, path, sha in records:
        if layer_match(r, patterns) and len(r["points"]) >= 2:
            out.append((g.LineString(r["points"]), r, path, sha))
    return out


def snap_width(w, widths, tol):
    best = min(widths, key=lambda s: abs(s - w)) if widths else None
    return best if best is not None and abs(best - w) <= tol + 1e-9 else None


def xy(p):
    return [round(float(p[0]), 3), round(float(p[1]), 3)]


def closed_polygons(ing, lw):
    """[(polygon, layer_short, handle)] — closed polylines of a DWG ingestion file (content.polylines, n >= 3) and
    closed records of its linework side file, once per handle (Rev G §4.13D.5)."""
    g, _ = shapely_mods()
    out, seen = [], set()
    recs = [(p.get("layer_short") or str(p.get("layer", "")).split("$")[-1], p.get("handle"), p.get("vertices") or [])
            for p in (ing.get("content") or {}).get("polylines", []) if (p.get("n_vertices") or len(p.get("vertices") or [])) >= 3]
    recs += [(r.get("layer_short") or str(r.get("layer", "")).split("$")[-1], r.get("handle"), r.get("points") or [])
             for r in lw if r.get("closed") and len(r.get("points") or []) >= 3]
    for layer, handle, pts in recs:
        key = (layer, handle, round(pts[0][0], 2), round(pts[0][1], 2)) if pts else None
        if key in seen or len(pts) < 3:
            continue
        seen.add(key)
        poly = g.Polygon([tuple(v[:2]) for v in pts])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area <= 0:
            continue
        out.append((poly, layer, handle))
    return out


def plot_polygons(ings, polygon_layers, text_layers):
    """Rev G §4.13D.7 (DG-5): cadastral plots — closed polylines on the GIS-005 `plot_label_source` polygon layers
    with the one text of a text layer inside them (the same reading as skill 9 match_plots.py). Returns
    ([{id, polygon, handle, layer, path, sha256}], unlabelled count, ambiguous count)."""
    g, _ = shapely_mods()
    plots, unl, amb = [], 0, 0
    for ing in ings:
        src = ing.get("source") or {}
        c = ing.get("content") or {}
        texts = [t for t in c.get("texts", []) if t.get("space") == "model" and t.get("layer") in text_layers
                 and str(t.get("text", "")).strip() and t.get("insert")]
        for p in c.get("polylines", []):
            if p.get("layer") not in polygon_layers or (p.get("n_vertices") or len(p.get("vertices") or [])) < 3:
                continue
            poly = g.Polygon([tuple(v[:2]) for v in p["vertices"]])
            if not poly.is_valid:
                poly = poly.buffer(0)
            labels = sorted({" ".join(str(t["text"]).split()).upper() for t in texts
                             if poly.intersects(g.Point(t["insert"][:2]))})
            if not labels:
                unl += 1
            elif len(labels) > 1:
                amb += 1
            else:
                plots.append({"id": labels[0], "polygon": poly, "handle": p.get("handle"), "layer": p.get("layer"),
                              "path": src.get("path"), "sha256": src.get("sha256")})
    return plots, unl, amb


# ---------------------------------------------------------------- Rev I §4.13F (phase C2): arcs of curb line work
def fit_circle(pts):
    """Least-squares (algebraic) circle through pts -> ((cx, cy), r, largest radial residual); None if degenerate."""
    n = len(pts)
    if n < 3:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    q = [(p[0] - mx, p[1] - my) for p in pts]          # centred for numerical stability
    suu = sum(u * u for u, _ in q)
    svv = sum(v * v for _, v in q)
    suv = sum(u * v for u, v in q)
    suuu = sum(u ** 3 for u, _ in q)
    svvv = sum(v ** 3 for _, v in q)
    suvv = sum(u * v * v for u, v in q)
    svuu = sum(v * u * u for u, v in q)
    det = suu * svv - suv * suv
    if abs(det) < 1e-12:
        return None
    b1 = (suuu + suvv) / 2
    b2 = (svvv + svuu) / 2
    uc = (b1 * svv - b2 * suv) / det
    vc = (b2 * suu - b1 * suv) / det
    r = math.sqrt(uc * uc + vc * vc + (suu + svv) / n)
    cx, cy = uc + mx, vc + my
    res = max(abs(math.dist((cx, cy), p) - r) for p in pts)
    return (cx, cy), r, res


def curved_runs(pts, seg_max=3.0, min_turn_deg=0.2):
    """Runs of consecutive short segments (< seg_max) turning the same way inside a polyline (bulges flattened by
    skill 1, or curves drawn as short chords). Each run has at least 4 distinct points."""
    runs, cur, sgn = [], [], 0
    for i in range(1, len(pts) - 1):
        l1, l2 = math.dist(pts[i - 1], pts[i]), math.dist(pts[i], pts[i + 1])
        a = math.atan2(pts[i][1] - pts[i - 1][1], pts[i][0] - pts[i - 1][0])
        b = math.atan2(pts[i + 1][1] - pts[i][1], pts[i + 1][0] - pts[i][0])
        d = math.degrees((b - a + math.pi) % (2 * math.pi) - math.pi)
        s = 1 if d > 0 else -1
        ok = 0 < l1 < seg_max and 0 < l2 < seg_max and abs(d) > min_turn_deg
        if ok and cur and s == sgn:
            cur.append(pts[i + 1])
        else:
            if len(cur) >= 4:
                runs.append(cur)
            cur, sgn = ([pts[i - 1], pts[i], pts[i + 1]], s) if ok else ([], 0)
    if len(cur) >= 4:
        runs.append(cur)
    out = []
    for r in runs:
        q = [r[0]]
        for p in r[1:]:
            if math.dist(p, q[-1]) > 1e-6:
                q.append(p)
        if len(q) >= 4:
            out.append(q)
    return out


def arc_geometry(run, c, r):
    """Sweep (degrees, unsigned), mid point, ends, tangent intersection point (PI) and length of an arc run."""
    ang = [math.atan2(p[1] - c[1], p[0] - c[0]) for p in run]
    tot = 0.0
    for a0, a1 in zip(ang, ang[1:]):
        tot += (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
    am = ang[0] + tot / 2
    mid = (c[0] + r * math.cos(am), c[1] + r * math.sin(am))
    half = abs(tot) / 2
    pi = None
    if 1e-6 < half < math.radians(89.5):
        k = r / math.cos(half)
        pi = (c[0] + k * math.cos(am), c[1] + k * math.sin(am))
    return {"sweep": math.degrees(abs(tot)), "mid": mid, "p0": tuple(run[0]), "p1": tuple(run[-1]), "pi": pi,
            "length": r * abs(tot)}


def fitted_arcs(records, patterns, fit_tol=0.05, seg_max=3.0, r_max=200.0):
    """Arcs of the line work on the given layers: ARC records, CIRCLE records (full) and curved runs of polylines,
    each fitted with a least-squares circle and accepted when the largest residual is <= fit_tol x R.
    records: [(record, source path, source sha)] (load_linework)."""
    out = []
    for r, path, sha in records:
        if not layer_match(r, patterns) or len(r.get("points") or []) < 3:
            continue
        pts = [tuple(p[:2]) for p in r["points"]]
        if r["type"] in ("ARC", "CIRCLE"):
            runs = [pts]
        elif r["type"] in ("LWPOLYLINE", "POLYLINE"):
            runs = curved_runs(pts, seg_max)
        else:
            continue
        for k, run in enumerate(runs):
            f = fit_circle(run)
            if f is None:
                continue
            c, R, res = f
            if R >= r_max or res > fit_tol * max(R, 1.0):
                continue
            full = r["type"] == "CIRCLE" or (math.dist(run[0], run[-1]) < 0.5 and len(run) > 8)
            geo = arc_geometry(run, c, R)
            if full:
                geo.update(sweep=360.0, pi=None, length=2 * math.pi * R)
            out.append({"handle": r.get("handle"), "layer": r.get("layer_short") or r.get("layer"), "type": r["type"],
                        "closed": bool(r.get("closed")), "run": k, "c": c, "R": R, "residual": res, "full": full, "path": path, "sha256": sha, **geo})
    return sorted(out, key=lambda a: (str(a["path"]), str(a["handle"]), a["run"]))
