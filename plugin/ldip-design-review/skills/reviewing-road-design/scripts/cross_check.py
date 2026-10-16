#!/usr/bin/env python3
"""Cross-check of road positions between two sources (SPC-0006 Rev F §4.13C.2; K19-2).

Compares each road of network.json (setting-out tables, S2, or LandXML, S3) with the plan centreline line
work (S1, skill 1 linework side file; layers from references/road_class_params.json): the share of each
segment's length within `cross_check_m` (0.5 m) of the plan centreline. A segment of 5 m or more with a
share below `cross_check_min_share` is a disagreement; every road with disagreements gives one
`inconsistency` check (rule ROD-001, road register), listing its segments, their share and largest offset.
The overall shares (table on plan, plan on table) are reported for the reviewer. Positions from drawings
cap at Probable.
0.2.0 (SPC-0006 Rev G §4.13D.3, DG-1): at DD the network comes from LandXML (S3) and, when setting-out tables
also exist, the tables are the reference instead of the plan centreline: --setout <setout.json> (S2), the
table segments rebuilt as in build_network.py. Exactly one of --linework and --setout is given.

Usage: python cross_check.py --network <network.json> --site <SITE>
           (--linework <plan.dwg.json | x.linework.json> [...] | --setout <setout.json>) --out <cross_check.json>
Exit: 0 written · 2 input error · 3 shapely missing. No network, no shell calls.
"""
__version__ = "0.2.0"

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import EXIT_INPUT, EXIT_OK, RoadError, atomic_write_json, emit, main_guard, read_json  # noqa: E402
from location_common import lines_of, load_linework, segment_points, shapely_mods, site_params, xy  # noqa: E402

RULE, PARAM = "ROD-001", "classes"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NMAR", "NRJS"])
    ap.add_argument("--linework", nargs="+")
    ap.add_argument("--setout")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, ops = shapely_mods()
        sp, P = site_params(a.site)
        tol, share_min = P["network"]["cross_check_m"], P["network"]["cross_check_min_share"]
        net = read_json(a.network)
        if bool(a.linework) == bool(a.setout):
            raise RoadError(EXIT_INPUT, "give exactly one reference: --linework (plan centreline) or --setout (tables)")
        if a.setout:
            so = read_json(a.setout)
            parts = [g.LineString(p) for al in so.get("alignments", {}).values() for s in al["segments"]
                     if (p := segment_points(s))]
            if not parts:
                raise RoadError(EXIT_INPUT, f"{a.setout}: no setting-out segment to compare with")
            CL = ops.unary_union(parts)
            first = next(iter(so["alignments"].values()))["evidence"]
            src_path, src_sha = first.get("path"), first.get("sha256")
            ref_name, ref_key = "setting-out tables", {"S2": {"path": a.setout, "sheets": sorted({s["path"] for s in so.get("sources", [])})}}
        else:
            cl = lines_of(load_linework(a.linework), sp["centreline_layers"])
            if not cl:
                raise RoadError(EXIT_INPUT, f"no plan centreline on layers {sp['centreline_layers']} in the linework given")
            CL = ops.unary_union([c[0] for c in cl])
            src_path, src_sha = cl[0][2], cl[0][3]
            ref_name = f"plan centreline ({Path(src_path).name}, layers {', '.join(sp['centreline_layers'])})"
            ref_key = {"S1": {"path": src_path, "sha256": src_sha, "layers": sp["centreline_layers"]}}
        own = "LandXML alignment" if net.get("position_source") == "landxml" else "setting-out table"
        buf = CL.buffer(tol)
        checks, roads, tot, inside = [], {}, 0.0, 0.0
        for name, r in sorted(net["roads"].items()):
            bad = []
            rl, ri = 0.0, 0.0
            for i, pts in enumerate(r["geometry"]):
                seg = g.LineString(pts)
                L = seg.length
                ins = seg.intersection(buf).length
                rl, ri = rl + L, ri + ins
                if L >= 5.0 and ins / L < share_min:
                    far = max((g.Point(c).distance(CL) for c in seg.coords), default=0.0)
                    mid = seg.interpolate(0.5, normalized=True)
                    bad.append({"segment": (r.get("tags") or [None] * (i + 1))[i] or str(i + 1), "length": round(L, 1), "share": round(ins / L, 3),
                                "max_offset_m": round(far, 2), "xy": xy((mid.x, mid.y))})
            tot, inside = tot + rl, inside + ri
            roads[name] = {"length": round(rl, 1), "share_within": round(ri / rl, 4) if rl else None, "disagreements": bad}
            if bad:
                ev = r.get("evidence") or {}
                checks.append({"rule_id": RULE, "parameter": PARAM, "class": None, "result": "fail",
                               "subject": f"{name}: position on the {own} vs {ref_name.split(' (')[0]}",
                               "observed": "; ".join(f"segment {b['segment']} ({b['length']:g} m): {100 * b['share']:.0f} % within "
                                                     f"{tol:g} m, up to {b['max_offset_m']:g} m off" for b in bad[:5]),
                               "required": f"the same position in both sources (within {tol:g} m)", "comparison": "consistent",
                               "unit": None, "issue_type": "inconsistency", "link_cap": "Probable",
                               "mode_tag": "position_inconsistency",
                               "evidence": {"source_type": ev.get("source_type", "dwg_vector"), "path": ev.get("path"), "sha256": ev.get("sha256"),
                                            "handles": [h for h in [ev.get("title_handle")] if h], "xy": bad[0]["xy"],
                                            "extract": f"{name}: {own} segments off the {ref_name}: "
                                                       + "; ".join(f"segment {b['segment']} at E {b['xy'][0]:.1f} N {b['xy'][1]:.1f} "
                                                                   f"share {100 * b['share']:.0f} %" for b in bad[:5])}})
        tab = ops.unary_union([g.LineString(p) for r in net["roads"].values() for p in r["geometry"]])
        plan_on_table = CL.intersection(tab.buffer(tol)).length / CL.length if CL.length else None
        out = {"tool": {"script": "cross_check.py", "version": __version__}, "mode": "cross_check",
               "sources": {"S2_or_S3": a.network, "position_source": net.get("position_source", "tables"), **ref_key},
               "tolerance_m": tol, "min_share": share_min,
               "summary": {"table_on_plan": round(inside / tot, 4) if tot else None,
                           "plan_on_table": round(plan_on_table, 4) if plan_on_table is not None else None,
                           "roads": len(roads), "roads_disagreeing": len(checks)},
               "roads": roads, "checks": checks, "not_run": []}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, **out["summary"]})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
