#!/usr/bin/env python3
"""EWK-101 / EWK-102 presence checks for skill 13 (SPC-0006 Rev L §4.14.10–11 + L-a DL-14/18).

Locations from EWK-002 groups in slopes.json. EWK-101 stays at 60 % (DD).
EWK-102 runs at 30 % with step height ≥ 1.0 m (DL-18).

Usage:
  python check_presence.py --slopes <slopes.json> --rules <rules_RDE.json>
      --stage "<stage>" [--file carrier=path ...] [--site SITE]
      --out <presence.json> [--required-out <required_inputs.json>]
"""
__version__ = "0.1.0"

import argparse
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ewk_common import (EXIT_OK, EwkError, atomic_write_json, emit, ewk_gate, fail_rec,  # noqa: E402
                        load_params, main_guard, read_json, rules_index)


def _terms(params, key):
    return list(params.get(key) or [])


def _search_hits(files, terms, layers, xy, buffer_m):
    """Return True if any term/layer/block/text is within buffer_m of xy."""
    if xy is None:
        return False, []
    searched = []
    cx, cy = xy
    for carrier, path, ing in files:
        searched.append(f"{carrier}:{path}")
        content = ing.get("content") or {}
        for t in content.get("texts") or []:
            text = t.get("text") or ""
            ins = t.get("insert")
            if not ins:
                continue
            if any(re.search(pat, text, re.I) for pat in terms):
                if math.hypot(float(ins[0]) - cx, float(ins[1]) - cy) <= buffer_m:
                    return True, searched
            layer = t.get("layer") or ""
            if layers and any(L.lower() in layer.lower() for L in layers):
                if math.hypot(float(ins[0]) - cx, float(ins[1]) - cy) <= buffer_m:
                    return True, searched
        for b in content.get("blocks") or []:
            name = str(b.get("name") or "")
            ins = b.get("insert") or [0, 0]
            if any(re.search(pat, name, re.I) for pat in terms):
                if math.hypot(float(ins[0]) - cx, float(ins[1]) - cy) <= buffer_m:
                    return True, searched
        for layer in content.get("layers") or []:
            lname = layer if isinstance(layer, str) else (layer.get("name") or "")
            if any(re.search(pat, lname, re.I) for pat in terms):
                return True, searched
            if layers and any(L.lower() in lname.lower() for L in layers):
                return True, searched
    return False, searched


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--slopes", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--stage", required=True)
    ap.add_argument("--site", default=None)
    ap.add_argument("--file", action="append", default=[], help="carrier=path")
    ap.add_argument("--surfaces", default=None, help="for MIDP missing / walls from slopes")
    ap.add_argument("--out", required=True)
    ap.add_argument("--required-out", default=None)
    a = ap.parse_args(argv)

    def run():
        slopes = read_json(a.slopes)
        rb = read_json(a.rules)
        rules, skipped, stage_map = rules_index(rb)
        params, site_p, doc = load_params(a.site)
        buffer_m = float(params.get("presence_buffer_m", 5.0))
        # DL-18: EWK-102 at 30 % uses 1.0 m
        phase_of_stage = None
        from ewk_common import phase_of
        phase_of_stage, milestone = phase_of(stage_map, a.stage)
        ewk102_min_h = float(params.get("ewk102_min_height_m_pd", 1.0)) if phase_of_stage == "PD" \
            else float(params.get("ewk002_min_height_m", 0.5))

        prot_terms = _terms(doc.get("terms") or params, "slope_protection") or [
            r"slope\s+protection", r"rip-?rap", r"gabion", r"stone\s+pitching",
            r"erosion\s+control", r"geotextile", r"shotcrete", r"rock\s+netting",
        ]
        wall_terms = _terms(doc.get("terms") or params, "retaining") or [
            r"retaining\s+walls?", r"\br\.?\s*w\.?\b", r"\brw-",
        ]
        prot_layers = params.get("protection_layers") or site_p.get("protection_layers") or []
        wall_layers = params.get("wall_layers") or site_p.get("wall_layers") or []

        files = []
        for item in a.file:
            if "=" not in item:
                raise EwkError(2, f"--file needs carrier=path, got {item!r}")
            carrier, path = item.split("=", 1)
            files.append((carrier.strip(), path.strip(), read_json(path.strip())))

        groups = slopes.get("ewk002_groups") or []
        walls_modelled = slopes.get("walls") or []
        checks, passed, not_run = [], [], []
        searched_all = [f"{c}:{p}" for c, p, _ in files] or ["(no file given)"]

        # EWK-101 — DD only
        rule, phase, _ = ewk_gate(rules, skipped, "EWK-101", "required_element", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "EWK-101", "parameter": "required_element", "reason": phase})
        else:
            missing = []
            for g in groups:
                hit, _ = _search_hits(files, prot_terms, prot_layers, g["centroid_en"], buffer_m)
                if not hit:
                    missing.append(g)
            src = {"source_type": "script_output", "extract": f"searched: {', '.join(searched_all)}"}
            if missing:
                for g in missing[:10]:
                    checks.append(fail_rec(
                        "EWK-101", "required_element",
                        f"slope protection missing @ E{g['centroid_en'][0]} N{g['centroid_en'][1]}",
                        0, 1, "present", "locations", src,
                        status_override="Missing information",
                        issue_type="missing_information",
                        mode_tag="presence",
                        xy_pt=g["centroid_en"],
                        note=f"count={len(missing)}; searched: {', '.join(searched_all)}",
                    ))
            else:
                passed.append({"rule_id": "EWK-101", "parameter": "required_element",
                               "result": "pass", "observed": "protection found or no groups"})

        # EWK-102 — PD with height ≥ 1.0 m (DL-18); DD with ewk002_min_height
        rule, phase, _ = ewk_gate(rules, skipped, "EWK-102", "required_element", stage_map, a.stage)
        if rule is None:
            not_run.append({"rule_id": "EWK-102", "parameter": "required_element", "reason": phase})
        else:
            locs = [g for g in groups if g.get("height_m", 0) >= ewk102_min_h]
            missing = []
            for g in locs:
                # modelled wall nearby?
                wall_hit = any(
                    math.hypot(w["centroid"][0] - g["centroid_en"][0],
                               w["centroid"][1] - g["centroid_en"][1]) <= buffer_m
                    for w in walls_modelled if w.get("centroid")
                )
                text_hit, _ = _search_hits(files, wall_terms, wall_layers, g["centroid_en"], buffer_m)
                if not wall_hit and not text_hit:
                    missing.append(g)
            src = {"source_type": "script_output", "extract": f"searched: {', '.join(searched_all)}"}
            if missing:
                for g in missing[:10]:
                    checks.append(fail_rec(
                        "EWK-102", "required_element",
                        f"retaining missing h={g['height_m']} m @ "
                        f"E{g['centroid_en'][0]} N{g['centroid_en'][1]}",
                        0, 1, "present", "locations", src,
                        status_override="Missing information",
                        issue_type="missing_information",
                        mode_tag="presence",
                        xy_pt=g["centroid_en"],
                        note=f"count={len(missing)}; min_height={ewk102_min_h}; "
                             f"searched: {', '.join(searched_all)}",
                    ))
            else:
                passed.append({"rule_id": "EWK-102", "parameter": "required_element",
                               "result": "pass", "observed": "retaining found or no steep groups"})

        # DL-15 MIDP missing models → required_inputs style items
        required_items = []
        if a.surfaces:
            surf = read_json(a.surfaces)
            for miss in surf.get("midp_missing") or []:
                required_items.append({
                    "key": f"midp:{miss.get('deliverable') or miss.get('title')}",
                    "item": f"MIDP model {miss.get('deliverable') or ''} {miss.get('title') or ''}".strip(),
                    "carriers": miss.get("formats_missing") or ["IFC"],
                    "required_from_phase": "PD",
                    "submission_phase": phase_of_stage or "PD",
                    "result": "fail",
                    "rules": ["EWK-001", "EWK-002", "EWK-003"],
                    "searched": searched_all,
                    "source_id": "SRC-14",
                    "clause": "MIDP Data Exchange 04 / DL-15",
                    "reason": miss.get("reason"),
                })

        out = {
            "tool": {"script": "check_presence.py", "version": __version__},
            "mode": "presence",
            "stage": a.stage,
            "checks": checks,
            "passed": passed,
            "not_run": not_run,
            "ewk102_min_height_m": ewk102_min_h,
        }
        atomic_write_json(a.out, out)
        if a.required_out:
            req = {
                "tool": {"script": "check_presence.py", "version": __version__},
                "mode": "required",
                "items": required_items,
            }
            atomic_write_json(a.required_out, req)
        emit({"status": "ok", "out": a.out, "checks": len(checks), "not_run": len(not_run),
              "required_items": len(required_items)})
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
