#!/usr/bin/env python3
"""Road low points from the LandXML design profiles, with plan position (SPC-0006 Rev G §4.13D.4; ROD-107, DG-2).

For each design profile (skill 3 `content.profiles`, kind `design`) of an alignment that has plan geometry in
the same ingestion files:
- sag vertical curve (g_in < 0 < g_out, length L > 0): low point at BVC + (−g_in · L) / (g_out − g_in), with
  BVC = PVI station − L / 2 and the elevation of the parabola there;
- a PVI without a curve (or a curve of length 0) where the grade changes from falling to rising: the PVI;
- a profile end reached by a falling grade (first tangent rising away from the start, or last tangent falling
  to the end), only when that end is a `dead_end` of network.json (a road end of the same road within
  `end_match_m`); an end not typed (no network given) is listed in `ends_not_used`.
Profile stations are alignment stations: station equations of the alignment (StaEquation) are applied to get
the distance along the alignment. The plan position is that distance on the alignment geometry rebuilt as in
read_landxml_network.py. Low points of several design profiles of one alignment within 1 m of station are one.
Low points from LandXML keep the rule cap (Rev G §4.13D.8).

Usage: python find_low_points.py --landxml <x.xml.json> [...] [--network <network.json>] --out <low_points.json>
Exit: 0 written · 2 input error · 3 shapely missing. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import EXIT_INPUT, EXIT_OK, RoadError, atomic_write_json, emit, main_guard, read_json, source_of  # noqa: E402
from location_common import shapely_mods, xy  # noqa: E402
from read_landxml_network import alignment_line, segments_of  # noqa: E402

END_MATCH_M = 5.0
DEDUP_STA_M = 1.0


def to_internal(sta, al):
    """Station as printed -> internal station, with the alignment's station equations (regions in order)."""
    eqs = sorted([q for q in al.get("station_equations") or [] if q.get("sta_internal") is not None
                  and q.get("sta_ahead") is not None], key=lambda q: q["sta_internal"])
    s0 = al.get("sta_start") or 0.0
    if not eqs:
        return sta
    regions = [(s0, s0)] + [(q["sta_ahead"], q["sta_internal"]) for q in eqs]
    ends = [q.get("sta_back") for q in eqs] + [None]
    for (start, internal), end in zip(regions, ends):
        if sta >= start - 1e-6 and (end is None or sta <= end + 1e-6):
            return internal + (sta - start)
    return None


def low_points_of(prof):
    """[(kind, station, elevation, detail)] of one design profile (stations as printed)."""
    pv, tg = prof.get("pvis") or [], prof.get("tangents") or []
    curves = {c["pvi_sta"]: c for c in prof.get("curves") or []}
    out = []
    for i in range(1, len(pv) - 1):
        gi, go = tg[i - 1]["grade_pct"], tg[i]["grade_pct"]
        if not (gi < 0 < go):
            continue
        c = curves.get(pv[i]["sta"])
        L = (c or {}).get("length") or 0.0
        if L > 0:
            bvc = pv[i]["sta"] - L / 2
            x = (-gi * L) / (go - gi)
            e_bvc = pv[i]["elev"] - gi / 100 * L / 2
            e = e_bvc + gi / 100 * x + (go - gi) / 100 * x * x / (2 * L)
            out.append(("sag_curve", bvc + x, e, f"sag curve at PVI {pv[i]['sta']:.3f}, L {L:g} m, g {gi:+.3f} % / {go:+.3f} %"))
        else:
            out.append(("pvi", pv[i]["sta"], pv[i]["elev"], f"PVI without curve, g {gi:+.3f} % / {go:+.3f} %"))
    if len(pv) >= 2 and tg:
        if tg[0]["grade_pct"] > 0:
            out.append(("end", pv[0]["sta"], pv[0]["elev"], f"profile start, first grade {tg[0]['grade_pct']:+.3f} % rising away"))
        if tg[-1]["grade_pct"] < 0:
            out.append(("end", pv[-1]["sta"], pv[-1]["elev"], f"profile end, last grade {tg[-1]['grade_pct']:+.3f} % falling to it"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--landxml", nargs="+", required=True)
    ap.add_argument("--network")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, _ = shapely_mods()
        net = read_json(a.network) if a.network else None
        dead = [e for e in (net or {}).get("ends", []) if e.get("type") == "dead_end"]
        lows, unused_ends, not_positioned, warnings, seen = [], [], [], [], {}
        n_prof = 0
        for p in a.landxml:
            ing = read_json(p)
            if (ing.get("source") or {}).get("file_type") not in ("xml", "landxml"):
                raise RoadError(EXIT_INPUT, f"{p}: not a LandXML ingestion file (skill 3)")
            src = source_of(ing)
            c = ing.get("content") or {}
            als = {al.get("name"): al for al in c.get("alignments") or [] if al.get("elements")}
            for prof in c.get("profiles") or []:
                if prof.get("kind") != "design" or not prof.get("pvis"):
                    continue
                n_prof += 1
                aname = prof.get("alignment")
                al = als.get(aname)
                if al is None:
                    not_positioned.append({"alignment": aname, "profile": prof.get("name"), "reason": "alignment without plan geometry"})
                    continue
                line_pts = alignment_line(segments_of(al, warnings))
                line = g.LineString(line_pts) if len(line_pts) >= 2 else None
                for kind, sta, elev, detail in low_points_of(prof):
                    internal = to_internal(sta, al)
                    d = None if internal is None else internal - (al.get("sta_start") or 0.0)
                    if line is None or d is None or d < -0.5 or d > line.length + 0.5:
                        not_positioned.append({"alignment": aname, "profile": prof.get("name"), "station": round(sta, 3),
                                               "reason": "station outside the alignment"})
                        continue
                    q = line.interpolate(min(max(d, 0.0), line.length))
                    rec = {"alignment": aname, "profile": prof.get("name"), "kind": kind, "station": round(sta, 3),
                           "elevation": round(elev, 3), "xy": xy((q.x, q.y)), "detail": detail,
                           "evidence": {"source_type": "landxml", "path": src["path"], "sha256": src["sha256"],
                                        "alignment": aname, "profile": prof.get("name")}}
                    if kind == "end":
                        near = [e for e in dead if e["road"] == aname and math.dist(e["xy"], rec["xy"]) <= END_MATCH_M]
                        if not near:
                            unused_ends.append({**{k: rec[k] for k in ("alignment", "station", "xy")},
                                                "reason": "end is not a dead end of network.json" if net else "no network.json given"})
                            continue
                        rec["kind"], rec["end_id"] = "dead_end", near[0]["id"]
                    key = (aname, kind)
                    if any(abs(sta - s) <= DEDUP_STA_M for s in seen.get(key, [])):
                        continue
                    seen.setdefault(key, []).append(sta)
                    lows.append(rec)
        lows.sort(key=lambda r: (r["alignment"], r["station"]))
        for i, r in enumerate(lows, 1):
            r["id"] = f"LP{i:03d}"
        out = {"tool": {"script": "find_low_points.py", "version": __version__}, "position_source": "landxml",
               "inputs": {"landxml": a.landxml, "network": a.network}, "low_points": lows,
               "ends_not_used": unused_ends, "not_positioned": not_positioned, "warnings": warnings,
               "counts": {"design_profiles": n_prof, "low_points": len(lows),
                          "by_kind": {k: sum(1 for r in lows if r["kind"] == k) for k in ("sag_curve", "pvi", "dead_end")},
                          "ends_not_used": len(unused_ends), "not_positioned": len(not_positioned)}}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, **out["counts"]})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
