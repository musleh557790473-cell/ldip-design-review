#!/usr/bin/env python3
"""Road network from the setting-out alignments (SPC-0006 Rev F §4.13C.2; K19-1, K19-3, K19-4, DF-12).

Rebuilds every named alignment of setout.json (tangents; circular curves from PC / PI / PT and radius) and
finds, with the parameters of references/road_class_params.json (K19-3; [PROPOSED] values until tested):
- junctions: an alignment end within `end_on_alignment_m` (1.0 m) of another alignment; legs clustered
  within `junction_cluster_m` (15 m) form one junction location, with the roads meeting and the angles
  between their legs (kept for phase C, DF-12);
- roundabouts (K19-4): an alignment drawn as curve rows that close a circle (two half circles through two
  opposite points, or one 360° row whose centre is taken from a closed centreline of the same radius in
  the linework); alignment ends on the circle are its approaches;
- road ends: every other alignment end, typed as `gap` (within `drafting_gap_m`, 3 m, of another
  alignment: a drafting gap), `site_exit` (within `site_exit_buffer_m` of the site boundary when
  --site-boundary is given, or pointing, within `site_exit_reach_m` straight ahead, to a plan centreline
  that no table covers: a road outside the setting-out tables) or `dead_end`; `basis` says how the type
  was decided and `exit_checked` whether an exit could be recognised at all;
- per road: the junctions and roundabouts on it with their chainage (spacing for phase C, DF-12).
Positions from drawings cap at Probable (K19-1).
0.2.0 (SPC-0006 Rev G §4.13D.3, DG-1): the input may also be the LandXML network of read_landxml_network.py
(same format, `spiral` segments flattened); `position_source` (tables / landxml) is carried to network.json.

Usage: python build_network.py --setout <setout.json | landxml_network.json> --site <SITE> [--linework <plan.dwg.json | x.linework.json> ...]
           [--site-boundary <boundary.geojson>] --out <network.json>
Exit: 0 written · 2 input error · 3 shapely missing. No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import EXIT_OK, atomic_write_json, emit, main_guard, read_json  # noqa: E402
from location_common import (full_circle, lines_of, load_linework, segment_points, shapely_mods,  # noqa: E402
                             site_params, xy)


def ray_hit(parts, end, target, reach, g):
    """Distance from a road end, straight ahead along the road's last segment, to the target geometry."""
    P = g.Point(end)
    seg = min(parts, key=lambda l: l.distance(P))
    cs = list(seg.coords)
    if math.dist(cs[0], end) < 0.5:
        a, b = cs[0], cs[1]
    else:
        a, b = cs[-1], cs[-2]
    dx, dy = a[0] - b[0], a[1] - b[1]
    L = math.hypot(dx, dy)
    if L == 0:
        return None
    ray = g.LineString([a, (a[0] + dx / L * reach, a[1] + dy / L * reach)])
    hit = ray.intersection(target)
    return None if hit.is_empty else P.distance(hit)


def circle_from_linework(pt, radius, cl_lines, g):
    """Centre of a closed centreline ring through pt with the given radius (K19-4 fallback)."""
    P = g.Point(pt)
    for geom, rec, _, _ in cl_lines:
        if geom.distance(P) > 1.0 or len(rec["points"]) < 8:
            continue
        ring = rec["points"]
        if math.dist(ring[0], ring[-1]) > 1.0:
            continue
        cx = sum(p[0] for p in ring[:-1]) / (len(ring) - 1)
        cy = sum(p[1] for p in ring[:-1]) / (len(ring) - 1)
        r = sum(math.dist((cx, cy), p) for p in ring[:-1]) / (len(ring) - 1)
        if abs(r - radius) <= 0.5:
            return (cx, cy)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--setout", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NMAR", "NRJS"])
    ap.add_argument("--linework", nargs="*", default=[])
    ap.add_argument("--site-boundary")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, ops = shapely_mods()
        sp, P = site_params(a.site)
        N = P["network"]
        tol_end, clus, gap_m = N["end_on_alignment_m"], N["junction_cluster_m"], N["drafting_gap_m"]
        so = read_json(a.setout)
        warnings = []
        lw = load_linework(a.linework) if a.linework else []
        cl_lines = lines_of(lw, sp["centreline_layers"])
        boundary = None
        if a.site_boundary:
            gj = read_json(a.site_boundary)
            feats = gj.get("features") or [gj]
            boundary = ops.unary_union([g.shape(f.get("geometry") or f) for f in feats]).boundary
        roads, rbouts = {}, []
        for name, al in so.get("alignments", {}).items():
            segs = al["segments"]
            if segs and all(s["type"] == "curve" for s in segs) and re.match(sp.get("roundabout_name", r"^RA-?\d+$"), name, re.I):
                radius = segs[0].get("radius")
                pts = [tuple(p) for s in segs for p in s["points"]]
                distinct = []
                for p in pts:
                    if all(math.dist(p, q) > 0.5 for q in distinct):
                        distinct.append(p)
                centre = None
                if len(distinct) == 2 and radius and abs(math.dist(*distinct) - 2 * radius) <= 0.5:
                    centre = ((distinct[0][0] + distinct[1][0]) / 2, (distinct[0][1] + distinct[1][1]) / 2)
                elif len(distinct) >= 3:
                    fc = full_circle([distinct], radius)
                    centre = fc[0] if fc else None
                if centre is None and distinct and radius:
                    centre = circle_from_linework(distinct[0], radius, cl_lines, g)
                if centre is None:
                    warnings.append({"code": "ROUNDABOUT_NOT_REBUILT",
                                     "detail": f"{name}: centre not decidable from the table"
                                               + ("" if cl_lines else " (no plan centreline given)")})
                    rbouts.append({"name": name, "radius": radius, "centre": None, "ring": None, "basis": None,
                                   "evidence": al.get("evidence")})
                    continue
                ring = g.Point(centre).buffer(radius, quad_segs=64).exterior
                rbouts.append({"name": name, "radius": radius, "centre": xy(centre), "ring": ring,
                               "basis": "table" if len(distinct) != 1 else "table + plan centreline ring",
                               "evidence": al.get("evidence")})
                continue
            parts, tags = [], []
            for s in segs:
                p = segment_points(s)
                if p is None:
                    warnings.append({"code": "SEGMENT_NOT_REBUILT", "detail": f"{name} {s['tag']}"})
                    continue
                parts.append(g.LineString(p))
                tags.append(s["tag"])
            if not parts:
                continue
            u = ops.unary_union(parts)
            merged = ops.linemerge(u) if u.geom_type == "MultiLineString" else u
            roads[name] = {"parts": parts, "tags": tags, "geom": merged, "evidence": al.get("evidence"),
                           "length": round(sum(x.length for x in parts), 3)}
        names = sorted(roads)
        geom_all = {n: roads[n]["geom"] for n in names}
        # free ends of each alignment
        ends = []
        for n in names:
            pts = [c for p in roads[n]["parts"] for c in (p.coords[0], p.coords[-1])]
            for c in pts:
                if sum(1 for q in pts if math.dist(c, q) < 0.5) == 1:
                    ends.append((n, c))
        legs, free = [], []
        ring_of = [(rb, rb["ring"]) for rb in rbouts if rb["ring"] is not None]
        for n, c in ends:
            P_ = g.Point(c)
            # an end on the ring or inside it (approach centrelines drawn to the centre) is an approach
            rb_hit = [rb["name"] for rb, ring in ring_of
                      if ring.distance(P_) <= tol_end or g.Point(rb["centre"]).distance(P_) <= rb["radius"] + tol_end]
            if rb_hit:
                for rb in rbouts:
                    if rb["name"] in rb_hit:
                        rb.setdefault("approaches", []).append({"road": n, "xy": xy(c)})
                continue
            hits = [m for m in names if m != n and geom_all[m].distance(P_) <= tol_end]
            (legs if hits else free).append((n, c, hits))
        # cluster junction legs (deterministic: sorted by road then position)
        nodes = []
        for n, c, hits in sorted(legs, key=lambda t: (t[0], round(t[1][0], 3), round(t[1][1], 3))):
            node = next((k for k in nodes if math.dist(k["xy0"], c) <= clus), None)
            if node is None:
                node = {"xy0": c, "pts": [], "roads": set(), "legs": []}
                nodes.append(node)
            node["pts"].append(c)
            node["roads"] |= {n, *hits}
            node["legs"].append(n)
        junctions = []
        for i, k in enumerate(sorted(nodes, key=lambda k: (-k["xy0"][1], k["xy0"][0])), 1):
            cx = sum(p[0] for p in k["pts"]) / len(k["pts"])
            cy = sum(p[1] for p in k["pts"]) / len(k["pts"])
            J = g.Point(cx, cy)
            brs = []
            for r in sorted(k["roads"]):
                geom = geom_all[r]
                lines = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
                for ln in lines:
                    if ln.distance(J) > clus:
                        continue
                    d = ln.project(J)
                    for sign in (1, -1):
                        t = d + sign * 10.0
                        if 0 <= t <= ln.length:
                            q = ln.interpolate(t)
                            brs.append((round(math.degrees(math.atan2(q.x - cx, q.y - cy)) % 360, 1), r))
            brs.sort()
            angles = [round((brs[(j + 1) % len(brs)][0] - brs[j][0]) % 360, 1) for j in range(len(brs))] if len(brs) > 1 else []
            junctions.append({"id": f"J{i:03d}", "xy": xy((cx, cy)), "roads": sorted(k["roads"]),
                              "legs": len(brs), "leg_bearings": [{"road": r, "bearing": b} for b, r in brs],
                              "angles": angles})
        # road ends: gap / site exit / dead end
        tables_union = ops.unary_union(list(geom_all.values()) + [rb["ring"] for rb in rbouts if rb["ring"] is not None])
        outside_cl = []
        for geom, rec, path, sha in cl_lines:
            rest = geom.difference(tables_union.buffer(1.0))
            if not rest.is_empty and rest.length > 20.0:
                outside_cl.append(rest)
        outside = ops.unary_union(outside_cl) if outside_cl else None
        all_cl = ops.unary_union([x[0] for x in cl_lines]) if cl_lines else None
        out_ends = []
        for i, (n, c, _) in enumerate(sorted(free, key=lambda t: (t[0], round(t[1][0], 3))), 1):
            P_ = g.Point(c)
            others = min((geom_all[m].distance(P_) for m in names if m != n), default=1e9)
            typ, basis = "dead_end", "no other road, plan centreline or boundary at this end"
            if others <= gap_m:
                typ, basis = "gap", f"another alignment {others:.2f} m away (drafting gap <= {gap_m} m)"
            elif boundary is not None and boundary.distance(P_) <= N["site_exit_buffer_m"]:
                typ, basis = "site_exit", f"{boundary.distance(P_):.1f} m from the site boundary"
            elif outside is not None and (ahead := ray_hit(roads[n]["parts"], c, outside, N["site_exit_reach_m"], g)) is not None:
                typ, basis = "site_exit", (f"points to a plan centreline that no setting-out table covers (road outside the "
                                           f"tables) {ahead:.1f} m ahead")
            elif all_cl is not None and boundary is None:
                basis += "; site boundary not given (GIS-011 site_boundary) — exit not excluded"
            elif all_cl is None and boundary is None:
                basis = "no plan centreline and no site boundary given: type not checked (exit not excluded)"
            out_ends.append({"id": f"E{i:03d}", "road": n, "xy": xy(c), "type": typ, "basis": basis,
                             "exit_checked": boundary is not None or outside is not None})
        # per road: junctions / roundabouts with chainage (DF-12)
        per_road = {}
        for n in names:
            geom = roads[n]["geom"]
            line = geom if geom.geom_type == "LineString" else None
            items = []
            for j in junctions:
                if n in j["roads"]:
                    items.append({"location": j["id"], "chainage": round(line.project(g.Point(j["xy"])), 1) if line else None})
            for rb in rbouts:
                for ap_ in rb.get("approaches", []):
                    if ap_["road"] == n:
                        items.append({"location": rb["name"], "chainage": round(line.project(g.Point(ap_["xy"])), 1) if line else None})
            items.sort(key=lambda x: (x["chainage"] is None, x["chainage"] or 0))
            per_road[n] = {"length": roads[n]["length"], "segments": len(roads[n]["parts"]),
                           "continuous": geom.geom_type == "LineString", "locations": items,
                           "evidence": roads[n]["evidence"],
                           "tags": roads[n]["tags"],
                           "geometry": [[xy(c) for c in p.coords] for p in roads[n]["parts"]]}
        for rb in rbouts:
            rb.pop("ring", None)
            rb.setdefault("approaches", [])
        out = {"tool": {"script": "build_network.py", "version": __version__}, "site": a.site,
               "position_source": so.get("position_source", "tables"),
               "params": {k: N[k] for k in ("end_on_alignment_m", "junction_cluster_m", "drafting_gap_m", "site_exit_buffer_m",
                                          "site_exit_reach_m")},
               "params_status": N.get("status", "PROPOSED"),
               "inputs": {"setout": a.setout, "linework": a.linework, "site_boundary": a.site_boundary},
               "roads": per_road, "junctions": junctions, "roundabouts": rbouts, "ends": out_ends,
               "warnings": warnings,
               "counts": {"roads": len(per_road), "length_m": round(sum(r["length"] for r in per_road.values())),
                          "junctions": len(junctions), "roundabouts": len(rbouts),
                          "roundabout_approaches": sum(len(r["approaches"]) for r in rbouts),
                          "ends": {t: sum(1 for e in out_ends if e["type"] == t) for t in ("dead_end", "site_exit", "gap")},
                          "legs_per_junction": {str(k): sum(1 for j in junctions if len(j["roads"]) == k)
                                                for k in sorted({len(j["roads"]) for j in junctions})}}}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, **out["counts"], "warnings": len(warnings)})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
