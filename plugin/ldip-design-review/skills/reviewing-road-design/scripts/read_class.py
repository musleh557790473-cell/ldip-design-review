#!/usr/bin/env python3
"""Road class per road by every available method (SPC-0006 Rev F §4.13C.3; RC-1 ... RC-4).

For each road of network.json the ROW width is read by:
  M1  ROW class layer of the hierarchy plan on the centreline (share of the road within `m1_buffer_m` of a
      class layer >= `m1_min_share`; linework side file of skill 1);
  M2  ROW width written in the alignment name ("ROAD-01 (15m R.O.W)");
  M3  the site naming code (QRWN letters B 36 / C 30 / D 25 / E 20 / F 18 / G 15 m, K22);
  M4  legend colour of the hierarchy-plan PDF (skill 2 `--paths` side file), after fitting the PDF to the
      road network (scale and offset, least squares on nearest points; fit rejected above
      `m4_max_residual_m`; paths right of `m4_page_x_max_share` of the page width — legend and title
      block — are not used); only with a colour map confirmed for the site (RC-4); a road is read when
      >= `m4_min_share` of its samples carry one class colour;
  M5  measured ROW width between the ROW boundary lines of the same road-layout reference as the
      centrelines: `m5_cuts` cuts across each tangent of `m5_min_tangent_m` or more; per side the lower
      quartile of the distances (a cut through a junction opening reaches a farther line), summed and
      snapped to a standard width within `width_tolerance_m`).
M6 maps the width to a road class: the criteria-table columns (read_criteria.py `columns`, ROW widths of
each column) and the typical-section titles (check_sections.py). RC-2 agreement rule: methods that give a
width agree -> used (two or more: `agreement` >= 2; one: the single method is named); they disagree -> no
width, one `inconsistency` check for that road; a width that M6 does not map to one class -> class unknown
and one Missing-information check for all such roads (K23 route: RFI / CRS). Parameters:
references/road_class_params.json. Classes from drawings cap at Probable.

Usage: python read_class.py --network <network.json> --site <SITE> [--linework <hierarchy.dwg.json> ...]
           [--pdf <hierarchy.pdf.json>] [--criteria <criteria.json>] [--sections <section_checks.json>]
           --out <road_class.json>
Exit: 0 written · 2 input error · 3 shapely / numpy / scipy missing. No network, no shell calls.
"""
__version__ = "0.1.0"

import argparse
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from road_common import (EXIT_DEPENDENCY, EXIT_OK, RoadError, atomic_write_json, class_of,  # noqa: E402
                         emit, main_guard, read_json)
from location_common import lines_of, load_linework, shapely_mods, site_params, snap_width, xy  # noqa: E402

RULE, PARAM = "ROD-001", "classes"


def m1(roads, lw, sp, C, g, ops):
    pat = re.compile(sp["class_layer_pattern"], re.I)
    by_w = {}
    for r, path, sha in lw:
        m = pat.match(r.get("layer_short") or "")
        if m and len(r["points"]) >= 2:
            by_w.setdefault(float(m.group(1)), []).append(g.LineString(r["points"]))
    if not by_w:
        return {}, "no ROW class layer in the linework"
    bufs = {w: ops.unary_union(v).buffer(C["m1_buffer_m"]) for w, v in by_w.items()}
    out = {}
    for n, geom in roads.items():
        L = geom.length
        share = {w: geom.intersection(b).length / L for w, b in bufs.items()} if L else {}
        if not share:
            continue
        w = max(share, key=lambda k: (share[k], -k))
        out[n] = {"width": w if share[w] >= C["m1_min_share"] else None, "share": round(share[w], 3),
                  "layers": sorted(f"{k:g} m" for k, v in share.items() if v >= 0.2)}
    return out, None


def m2(names, sp):
    pat = re.compile(sp["row_in_name"], re.I)
    out = {}
    for n in names:
        m = pat.search(n)
        if m:
            out[n] = {"width": float(m.group(1)), "text": m.group(0)}
    return out


def m3(names, sp):
    code = sp.get("name_code")
    if not code:
        return {}, "no naming code for the site"
    pat = re.compile(code["pattern"])
    out = {}
    for n in names:
        m = pat.match(n)
        if m:
            w = code["letters"].get(m.group(1))
            out[n] = {"width": float(w) if w else None, "letter": m.group(1)}
    return out, None


def m5(net, lw, sp, C, g, ops, widths):
    pats = sp["row_boundary_layers"]
    bl = lines_of(lw, pats)
    # boundaries of the same road-layout drawing as the centrelines (same xref): copies of the boundary
    # inside other references (title block, cut-sheet overview) are not road edges at this scale
    def origin(r):          # the reference a record comes from: insert chain, else the bound-xref layer prefix
        return tuple(r.get("via_block") or [])[:1] or (str(r.get("layer", "")).rsplit("$", 2)[0],)
    cl_origin = {origin(r) for _, r, _, _ in lines_of(lw, sp["centreline_layers"])}
    same = [x for x in bl if origin(x[1]) in cl_origin]
    bl = same or bl
    if not bl:
        return {}, "no ROW boundary layer in the linework"
    B = ops.unary_union([x[0] for x in bl])
    out = {}
    for n, r in net["roads"].items():
        left, right = [], []
        for tag, pts in zip(r.get("tags") or [None] * len(r["geometry"]), r["geometry"]):
            if (tag and not tag.startswith("L")) or len(pts) != 2:
                continue
            (x0, y0), (x1, y1) = pts
            Ls = math.dist(pts[0], pts[1])
            if Ls < C["m5_min_tangent_m"]:
                continue
            ux, uy = (x1 - x0) / Ls, (y1 - y0) / Ls
            nx, ny = -uy, ux
            k = C["m5_cuts"]
            for j in range(k):
                t = 0.25 + 0.5 * j / max(k - 1, 1)
                cx, cy = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
                for sg, acc in ((1, left), (-1, right)):
                    ray = g.LineString([(cx, cy), (cx + sg * nx * C["m5_ray_m"], cy + sg * ny * C["m5_ray_m"])])
                    hit = ray.intersection(B)
                    pts_ = [hit] if hit.geom_type == "Point" else [q for q in getattr(hit, "geoms", []) if q.geom_type == "Point"]
                    if pts_:
                        acc.append(min(g.Point(cx, cy).distance(q) for q in pts_))
        if len(left) >= 3 and len(right) >= 3:
            # a cut through a junction opening reaches a farther line: per side the lower quartile is taken
            ql = statistics.quantiles(left, n=4)[0] if len(left) > 3 else min(left)
            qr = statistics.quantiles(right, n=4)[0] if len(right) > 3 else min(right)
            w = ql + qr
            out[n] = {"width": snap_width(w, widths, C["width_tolerance_m"]), "measured": round(w, 2),
                      "sides": [round(ql, 2), round(qr, 2)], "cuts": min(len(left), len(right))}
    return out, None


def m4(net, pdf_path, sp, C, np, cKDTree):
    """Legend colour of the hierarchy plan, after a scale + offset fit of the PDF to the network."""
    cmap = sp.get("pdf_colours")
    xmax_share = C.get("m4_page_x_max_share", 1.0)
    if not cmap or not sp.get("pdf_colours_confirmed"):
        return {}, None, "no confirmed PDF colour map for the site (RC-4)"
    ing = read_json(pdf_path)
    sides = [s for s in (ing.get("content") or {}).get("side_files", []) if s.get("kind") == "paths"]
    if not sides:
        return {}, None, f"{Path(pdf_path).name}: no paths side file (run skill 2 extract_pdf.py --paths)"
    pj = read_json(Path(pdf_path).parent / sides[0]["path"])
    page = pj["pages"][0]
    key_w = {}
    for w, rgb in cmap.items():
        k = "(" + ",".join(f"{v:.3f}" for v in rgb) + ")"
        key_w[k] = float(re.match(r"\d+(?:\.\d+)?", w).group(0))
    cp, cw = [], []
    xlim = xmax_share * page["width_pt"]
    for k, grp in page["colours"].items():
        if k not in key_w:
            continue
        for p in grp["paths"]:
            pts = p["points"]
            if max(q[0] for q in pts) > xlim:          # legend and title-block strip (right of the drawing)
                continue
            for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                n = max(1, int(math.dist((xa, ya), (xb, yb)) / 0.5))
                for i in range(n + 1):
                    cp.append((xa + (xb - xa) * i / n, ya + (yb - ya) * i / n))
                    cw.append(key_w[k])
            if len(pts) == 1:
                cp.append(tuple(pts[0]))
                cw.append(key_w[k])
    if len(cp) < 50:
        return {}, None, "too few class-coloured paths on the PDF page"
    cp = np.array(cp)
    T = cKDTree(cp)
    samp = {}
    for n, r in net["roads"].items():
        pts = []
        for line in r["geometry"]:
            for (xa, ya), (xb, yb) in zip(line, line[1:]):
                m = max(1, int(math.dist((xa, ya), (xb, yb)) / C["m4_sample_m"]))
                pts += [(xa + (xb - xa) * i / m, ya + (yb - ya) * i / m) for i in range(m)]
        if pts:
            samp[n] = np.array(pts)
    W = np.vstack(list(samp.values()))
    lo_c, hi_c = np.percentile(cp, 1, axis=0), np.percentile(cp, 99, axis=0)
    lo_w, hi_w = np.percentile(W, 1, axis=0), np.percentile(W, 99, axis=0)
    sx = (hi_c[0] - lo_c[0]) / max(hi_w[0] - lo_w[0], 1e-6)
    sy = (hi_c[1] - lo_c[1]) / max(hi_w[1] - lo_w[1], 1e-6)
    best = None
    for a0 in sorted({sx, sy, (sx + sy) / 2}):
        a, bx, by = a0, lo_c[0] - a0 * lo_w[0], lo_c[1] + a0 * hi_w[1]
        for it in range(60):
            Pp = np.c_[a * W[:, 0] + bx, -a * W[:, 1] + by]
            d, i = T.query(Pp)
            keep = d < np.percentile(d, 50 if it < 20 else 80)
            Q, X = cp[i[keep]], W[keep]
            A = np.vstack([np.c_[X[:, 0], np.ones(len(X)), np.zeros(len(X))],
                           np.c_[-X[:, 1], np.zeros(len(X)), np.ones(len(X))]])
            a, bx, by = np.linalg.lstsq(A, np.r_[Q[:, 0], Q[:, 1]], rcond=None)[0]
        d, _ = T.query(np.c_[a * W[:, 0] + bx, -a * W[:, 1] + by])
        res = float(np.median(d)) / a if a > 0 else 1e9
        if best is None or res < best[0]:
            best = (res, a, bx, by)
    res, a, bx, by = best
    fit = {"scale_pt_per_m": round(float(a), 6), "scale_1_to": round(72 / 25.4 / float(a) * 1000) if a > 0 else None,
           "offset_pt": [round(float(bx), 2), round(float(by), 2)], "median_residual_m": round(res, 2)}
    if res > C["m4_max_residual_m"]:
        return {}, fit, f"PDF fit rejected: median residual {res:.2f} m > {C['m4_max_residual_m']} m"
    out = {}
    for n, pts in samp.items():
        d, i = T.query(np.c_[a * pts[:, 0] + bx, -a * pts[:, 1] + by])
        ok = d / a < C["m4_match_m"]
        c = Counter(cw[j] for j in i[ok])
        top = c.most_common(1)
        share = top[0][1] / len(pts) if top else 0.0
        out[n] = {"width": top[0][0] if top and share >= C["m4_min_share"] else None, "share": round(share, 2),
                  "cover": round(float(ok.mean()), 2), "mix": {f"{k:g}": v for k, v in sorted(c.items())}}
    return out, fit, None


def m6_map(crit, secs):
    """ROW width -> set of rulebook classes, with the sources (M6)."""
    wmap = {}
    for c in (crit or {}).get("columns", []):
        for w in c.get("row_widths") or []:
            wmap.setdefault(float(w), {}).setdefault(c["class"], []).append(
                f"criteria table '{c.get('sub_heading') or c.get('heading')}'")
    for s in (secs or {}).get("sections", []):
        w, cls = s.get("title_width_m"), s.get("class") or class_of(s.get("title"))
        if w and cls:
            wmap.setdefault(float(w), {}).setdefault(cls, []).append(f"typical section '{s.get('title')}'")
    return wmap


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True)
    ap.add_argument("--site", required=True, choices=["QRWN", "NMAR", "NRJS"])
    ap.add_argument("--linework", nargs="*", default=[])
    ap.add_argument("--pdf")
    ap.add_argument("--criteria")
    ap.add_argument("--sections")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    def run():
        g, ops = shapely_mods()
        sp, P = site_params(a.site)
        C = P["class"]
        widths = [float(w) for w in C["standard_widths_m"]]
        net = read_json(a.network)
        roads = {}
        for n, r in net["roads"].items():
            parts = [g.LineString(p) for p in r["geometry"]]
            roads[n] = ops.unary_union(parts)
        names = sorted(roads)
        lw = load_linework(a.linework) if a.linework else []
        notes = {}
        meth = {}
        meth["M1"], notes["M1"] = m1(roads, lw, sp, C, g, ops) if lw else ({}, "no linework given")
        meth["M2"] = m2(names, sp)
        meth["M3"], notes["M3"] = m3(names, sp)
        fit = None
        if a.pdf:
            try:
                import numpy as np
                from scipy.spatial import cKDTree
            except ImportError as exc:
                raise RoadError(EXIT_DEPENDENCY, f"numpy / scipy needed for M4: {exc}") from exc
            meth["M4"], fit, notes["M4"] = m4(net, a.pdf, sp, C, np, cKDTree)
        else:
            meth["M4"], notes["M4"] = {}, "no hierarchy-plan PDF given"
        meth["M5"], notes["M5"] = m5(net, lw, sp, C, g, ops, widths) if lw else ({}, "no linework given")
        crit = read_json(a.criteria) if a.criteria else None
        secs = read_json(a.sections) if a.sections else None
        wmap = m6_map(crit, secs)
        out_roads, checks, unknown = {}, [], []
        for n in names:
            vals = {m: v[n]["width"] for m, v in meth.items() if n in v and v[n].get("width") is not None}
            distinct = sorted(set(vals.values()))
            rec = {"methods": {m: v[n] for m, v in meth.items() if n in v}, "width": None, "agreement": 0,
                   "class": None, "class_basis": None}
            ev = net["roads"][n].get("evidence") or {}
            mid = roads[n].interpolate(0.5, normalized=True)
            if len(distinct) == 1:
                rec["width"], rec["agreement"] = distinct[0], len(vals)
                rec["width_basis"] = ", ".join(sorted(vals)) + (" (single method)" if len(vals) == 1 else "")
                cls = wmap.get(distinct[0], {})
                if len(cls) == 1:
                    k = next(iter(cls))
                    rec["class"], rec["class_basis"] = k, "; ".join(cls[k][:2])
                else:
                    rec["class_note"] = ("width mapped to several classes: " + ", ".join(sorted(cls))) if cls else \
                        f"no class stated for a {distinct[0]:g} m ROW (M6)"
                    unknown.append((n, distinct[0], rec["class_note"], ev, xy((mid.x, mid.y))))
            elif len(distinct) > 1:
                rec["disagreement"] = {m: w for m, w in sorted(vals.items())}
                checks.append({"rule_id": RULE, "parameter": PARAM, "class": None, "result": "fail",
                               "subject": f"{n}: ROW width / class by method",
                               "observed": ", ".join(f"{m} {w:g} m" for m, w in sorted(vals.items())),
                               "required": "one road class for each road (methods agree, RC-2)", "comparison": "consistent",
                               "unit": None, "issue_type": "inconsistency", "link_cap": "Probable",
                               "mode_tag": "class_inconsistency",
                               "evidence": {"source_type": "dwg_vector", "path": ev.get("path"), "sha256": ev.get("sha256"),
                                            "handles": [h for h in [ev.get("title_handle")] if h], "xy": xy((mid.x, mid.y)),
                                            "extract": f"{n}: " + "; ".join(f"{m} {w:g} m" for m, w in sorted(vals.items()))
                                                       + " — the methods disagree; no class used for this road"}})
            out_roads[n] = rec
        if unknown:
            for n, w, note, ev, pos in unknown:
                checks.append({"rule_id": RULE, "parameter": PARAM, "class": None, "result": "fail",
                               "subject": f"{n} ({w:g} m ROW)", "observed": note,
                               "required": "the road class of every road (C1)", "comparison": "present", "unit": None,
                               "issue_type": "missing_information", "status_override": "Missing information",
                               "mode_tag": "class_unknown",
                               "evidence": {"source_type": "dwg_vector", "path": ev.get("path"), "sha256": ev.get("sha256"),
                                            "handles": [h for h in [ev.get("title_handle")] if h], "xy": pos,
                                            "extract": f"{n}: ROW {w:g} m; {note}. Ask for the class (RFI / CRS, M7)."}})
        summary = {"roads": len(names), "with_width": sum(1 for r in out_roads.values() if r["width"] is not None),
                   "with_class": sum(1 for r in out_roads.values() if r["class"]),
                   "disagreements": sum(1 for r in out_roads.values() if r.get("disagreement")),
                   "class_unknown": len(unknown),
                   "by_method": {m: sum(1 for v in meth[m].values() if v.get("width") is not None) for m in sorted(meth)},
                   "classes": dict(Counter(r["class"] for r in out_roads.values() if r["class"]))}
        agree = {}
        for m in ("M1", "M2", "M3", "M4", "M5"):
            for o in ("M1", "M2", "M3", "M4", "M5"):
                if m < o:
                    both = [n for n in names if (meth[m].get(n) or {}).get("width") is not None
                            and (meth[o].get(n) or {}).get("width") is not None]
                    if both:
                        same = sum(1 for n in both if meth[m][n]["width"] == meth[o][n]["width"])
                        agree[f"{m}={o}"] = f"{same} / {len(both)}"
        out = {"tool": {"script": "read_class.py", "version": __version__}, "mode": "road_class", "site": a.site,
               "inputs": {"network": a.network, "linework": a.linework, "pdf": a.pdf, "criteria": a.criteria,
                          "sections": a.sections},
               "m4_fit": fit, "method_notes": {k: v for k, v in notes.items() if v},
               "m6_width_map": {f"{w:g}": v for w, v in sorted(wmap.items())},
               "summary": summary, "pairwise_agreement": agree, "roads": out_roads, "checks": checks, "not_run": []}
        atomic_write_json(a.out, out)
        emit({"status": "ok", "out": a.out, **summary, "m4_fit_residual_m": (fit or {}).get("median_residual_m")})
        return EXIT_OK
    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
