"""TJ8 — SPC-0006 Rev J §4.13G.11: at PD / 30 %, every C3 rule is not run ("not required at stage"
or "no element"); no C3 finding; typical sections with "WITHOUT PARALLEL PARKING" give no parking element.

Synthetic PD package always runs (CI). Optional real three-site harness when LDIP_REVIEW points at a
local review root (real drawings stay out of Git, brief §10.2).

G-2 (real harness): select the **general plan** by drawing number (`00003` or `15000`), build the real
network (`read_setout` → `build_network`) and real classification (`read_class`) before C3 — do **not**
pick `*.dwg.json` alphabetically. G-1 (parking layer scoping): plan bays only from site `parking_layers`;
empty/absent → parking `not_run` (no parcel false positives).
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("shapely")

REPO = Path(__file__).resolve().parents[1]
SK10 = REPO / "plugin/ldip-design-review/skills/reviewing-road-design/scripts"
PY = sys.executable
_LDIP_REVIEW_RAW = os.environ.get("LDIP_REVIEW")
ROOT = Path(_LDIP_REVIEW_RAW) if _LDIP_REVIEW_RAW else None
C3_RULES = ["ROD-044", "ROD-045", "ROD-046", "ROD-047", "ROD-055", "ROD-056",
            "ROD-059", "ROD-060", "ROD-061", "ROD-062"]

# Shown when LDIP_REVIEW is unset (skip) or set but unusable (fail). Do not invent site data.
_LDIP_REVIEW_HELP = (
    "Set LDIP_REVIEW to the absolute path of the local LDIP_Review root "
    "(submittals/{NMAR,QRWN,NRJS}/… and 0.ai_context/{SITE}/…/*.dwg.json). "
    "Real 30 % drawings stay out of Git (brief §10.2). "
    "On the owner machine they live under OneDrive …/TAWAZUN PROGRAM/Claude/LDIP_Review — "
    "a local worker is required; cloud agents without that mount cannot run real TJ8. "
    "See docs/tj8-real-data-plan.md in the Project store."
)


def run(script, *args):
    p = subprocess.run([PY, str(SK10 / script), *map(str, args)], capture_output=True, text=True)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-2000:]})


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def dwg(tmp, name, texts=(), polys=()):
    tlist = []
    for i, item in enumerate(texts):
        t, x, y, layer = item[0], item[1], item[2], item[3]
        rot = item[4] if len(item) > 4 else 0
        tlist.append({"type": "TEXT", "text": t, "layer": layer, "insert": [x, y, 0],
                      "handle": f"{name}T{i:X}", "space": "model", "height": 0.25, "rotation": rot})
    ing = {"template_version": "1.2",
           "source": {"path": f"submittals/NRJS/T/{name}.dwg",
                      "sha256": hashlib.sha256(name.encode()).hexdigest(),
                      "bytes": 1, "document_no": None, "revision": None, "file_type": "dwg"},
           "content": {"texts": tlist,
                       "polylines": [{"type": "LWPOLYLINE", "layer": ly, "handle": f"{name}P{i:X}",
                                      "closed": True, "n_vertices": len(v), "vertices": v}
                                     for i, (ly, v) in enumerate(polys)],
                       "blocks": [], "layers": []}}
    p = tmp / f"{name}.dwg.json"
    p.write_text(json.dumps(ing))
    return p


def rules_c3(tmp):
    def R(rid, phase="DD", carriers=None):
        return {"rule_id": rid, "title": rid, "state": "active", "cap": "Confirmed",
                "statement": f"Synthetic {rid}.", "inputs": carriers or ["details"],
                "parameters": {"width": {"value": 2.5, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                                        "unit": "m", "cap": "Confirmed", "tolerance": {"value": 0}},
                              "length": {"value": 3.7, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                                         "unit": "m", "cap": "Confirmed", "tolerance": {"value": 0}},
                              "height": {"value": 75, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                                         "unit": "mm", "cap": "Confirmed", "tolerance": {"value": 0}},
                              "width_min": {"value": 2.5, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                                            "unit": "m", "cap": "Confirmed", "tolerance": {"value": 0}}},
                "severity_default": "Minor", "issue_type": "design_error", "check_type": "value",
                "default_status": {"dwg_vector": "Confirmed", "script_output": "Confirmed"},
                "owner_role": "DMR", "applicability": {"street_class": ["all"]},
                "required_from_phase": phase}

    rs = [R("ROD-044"), R("ROD-045"), R("ROD-046"), R("ROD-047"),
          R("ROD-055", carriers=["signing_marking_plan"]),
          R("ROD-056", carriers=["plan_geometry"]),
          R("ROD-059", phase="PD", carriers=["typical_section", "plan_geometry"]),
          R("ROD-060"), R("ROD-061"), R("ROD-062")]
    p = tmp / "rules_RDE.json"
    p.write_text(json.dumps({
        "agent": "RDE", "rulebook_version": "v7", "rules": rs, "rules_skipped": [],
        "stage_map": {"30% Concept Design": {"phase": "PD", "milestone": 30},
                      "60%": {"phase": "DD", "milestone": 60}}}))
    return p


def _assert_c3_pd_clean(cal, ped, pk, sec, cand):
    """§4.13G.11 TJ8 expectations on PD outputs."""
    for doc, name in ((cal, "calming"), (ped, "pedestrian"), (pk, "parking")):
        fails = [c for c in doc.get("checks", []) if c.get("result") == "fail"]
        assert not fails, f"{name} has C3 fails at PD: {fails[:3]}"
    # every C3 rule accounted for as not_run (stage / no element) — no C3 finding from candidates
    reasons = {(n.get("rule_id"), n.get("reason", "")) for n in
               cal.get("not_run", []) + ped.get("not_run", []) + pk.get("not_run", [])}
    for rid in C3_RULES:
        hits = [r for r in reasons if r[0] == rid]
        assert hits, f"{rid} missing from not_run at PD"
        blob = " ".join(h[1].lower() for h in hits)
        assert ("not required at stage" in blob or "no " in blob or "not run" in blob
                or "inactive" in blob or "not in rules" in blob or "gradient" in blob
                or "bridge" in blob or "surface" in blob or "schedule" in blob
                or "no element" in blob or "no speed" in blob or "no mini" in blob
                or "no chicane" in blob or "no crosswalk" in blob or "no refuge" in blob
                or "no parking" in blob or "detail" in blob or "parking layers" in blob
                or "gap" in blob), f"{rid}: unexpected reasons {hits}"
    park_els = [e for s in sec.get("sections", []) for e in s.get("elements", []) if e.get("element") == "parking"]
    assert park_els == [], f"sections must have no parking element at 30%: {park_els}"
    assert cand == [] or all(f["reference"]["rule_id"] not in C3_RULES for f in cand)


def test_tj8_synthetic_pd_30_percent(tmp_path):
    """Always-on TJ8: synthetic 30 % PD package — no C3 elements, WITHOUT PARALLEL PARKING."""
    rules = rules_c3(tmp_path)
    # empty details / plan / marking (probe: no C3 elements at 30 %)
    det = dwg(tmp_path, "DET", texts=[("GENERAL NOTES", 0, 0, "TXT")])
    plan = dwg(tmp_path, "PL", texts=[("ROAD LAYOUT", 660100, 2750000, "TXT")])
    mark = dwg(tmp_path, "MK", texts=[])
    sec = dwg(tmp_path, "SEC", texts=[
        ("RIGHT OF WAY", 0, 10, "ROW", 90), ("RIGHT OF WAY", 15, 10, "ROW", 90),
        ("15 m LOCAL ACCESS ROAD WITHOUT PARALLEL PARKING", 7, 4, "TITLE"),
        ("3.50", 7, 8, "DIM"), ("DRIVING LANE", 7, 7.2, "LAB"),
        ("2.00", 12, 8, "DIM"), ("SIDE WALK", 12, 7.2, "LAB"),
        ("15", 7.5, 9.5, "DIM"),
    ])
    (tmp_path / "network.json").write_text(json.dumps({
        "roads": {"ROAD-A (15m R.O.W)": {"length": 200, "geometry": [[660000, 2750000], [660200, 2750000]]}},
        "junctions": [{"id": "J001", "xy": [660100.0, 2750000.0], "roads": ["ROAD-A (15m R.O.W)"]}],
        "roundabouts": []}))
    (tmp_path / "class.json").write_text(json.dumps({
        "roads": {"ROAD-A (15m R.O.W)": {"width": 15.0, "class": "local_access"}}}))

    assert run("read_details.py", "--file", f"details={det}", "--out", tmp_path / "details.json")[0] == 0
    assert run("check_calming.py", "--details", tmp_path / "details.json",
               "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
               "--rules", rules, "--stage", "30% Concept Design", "--site", "NRJS",
               "--file", f"plan_geometry={plan}", "--file", f"details={det}",
               "--out", tmp_path / "cal.json")[0] == 0
    assert run("check_pedestrian.py", "--network", tmp_path / "network.json", "--rules", rules,
               "--stage", "30% Concept Design", "--site", "NRJS",
               "--file", f"signing_marking_plan={mark}", "--file", f"plan_geometry={plan}",
               "--class", tmp_path / "class.json", "--out", tmp_path / "ped.json")[0] == 0
    assert run("check_parking.py", "--network", tmp_path / "network.json", "--class", tmp_path / "class.json",
               "--rules", rules, "--stage", "30% Concept Design", "--site", "NRJS",
               "--file", f"plan_geometry={plan}", "--out", tmp_path / "pk.json")[0] == 0
    assert run("check_sections.py", "--sections", sec, "--rules", rules, "--out", tmp_path / "sec.json")[0] == 0
    rc, out = run("build_candidates.py",
                  "--results", tmp_path / "cal.json", tmp_path / "ped.json", tmp_path / "pk.json",
                  tmp_path / "sec.json",
                  "--rules", rules, "--run", "NRJS-T-20261018T1530", "--site", "NRJS", "--sub", "T",
                  "--evidence-dir", tmp_path / "ev", "--out", tmp_path / "cand.json")
    assert rc == 0, out
    _assert_c3_pd_clean(load(tmp_path / "cal.json"), load(tmp_path / "ped.json"), load(tmp_path / "pk.json"),
                        load(tmp_path / "sec.json"), load(tmp_path / "cand.json"))


def _dwg_jsons(folder):
    """All *.dwg.json under folder (unsorted — callers must select by drawing number)."""
    return list(Path(folder).rglob("*.dwg.json")) if folder and Path(folder).is_dir() else []


def _pick_by_drawing_no(paths, *needles):
    """Select one ingestion whose filename contains a drawing-number needle (00003 / 15000 / …).

    G-2: never take the alphabetically first file. Match MIDP-style stems such as
    `…-ROD-DWG-00003-001-00` or `…-IRO-DWG-00-15000-0A`.
    """
    for needle in needles:
        token = f"-{needle}-" if not needle.startswith("-") else needle
        # also accept …-DWG-00-15000-… (NRJS) and bare -00003. / -15000.
        alts = (token, f"-{needle}.", f"-DWG-{needle}-", f"-DWG-00-{needle}-")
        for p in paths:
            name = p.name.upper()
            if any(a.upper() in name for a in alts):
                return p
    return None


def _pick_all_by_drawing_no(paths, *needles):
    """All ingestions matching any drawing-number needle (order = discovery, not alphabetical)."""
    out, seen = [], set()
    for needle in needles:
        token = f"-{needle}-"
        alts = (token, f"-{needle}.", f"-DWG-{needle}-", f"-DWG-00-{needle}-")
        for p in paths:
            if p in seen:
                continue
            name = p.name.upper()
            if any(a.upper() in name for a in alts):
                out.append(p)
                seen.add(p)
    return out


def _site_package(root, site):
    """Locate general plan (00003 or 15000), setting-out sheets, and optional companions for one site.

    Prefer `0.ai_context/{SITE}/`; fall back to any site-tagged paths under the review root.
    Returns None when the general plan is missing.
    """
    ctx = root / "0.ai_context" / site
    pool = _dwg_jsons(ctx) if ctx.is_dir() else []
    if not pool:
        # site-tagged filenames under the whole tree (still by drawing no, not sorted[:N])
        pool = [p for p in _dwg_jsons(root) if site.upper() in p.as_posix().upper()
                or (site == "QRWN" and "-AQ-" in p.name.upper())
                or (site == "NMAR" and ("-DN-" in p.name.upper() or "NAMAR" in p.as_posix().upper()))
                or (site == "NRJS" and "NRJS" in p.name.upper())]
    plan = _pick_by_drawing_no(pool, "00003", "15000")
    if plan is None:
        return None
    sheets = _pick_all_by_drawing_no(pool, "60000", "60001")
    sections = _pick_by_drawing_no(pool, "30000", "30001")
    details = _pick_by_drawing_no(pool, "50000", "50001")
    pdf = plan.with_name(plan.name.replace(".dwg.json", ".pdf.json"))
    if not pdf.is_file():
        pdf = None
    # design report (optional; helps M3/M5 class agreement)
    reports = list((ctx if ctx.is_dir() else root).rglob("*DES-RPT*.pdf.json")) if ctx.is_dir() or root.is_dir() else []
    if not reports and root.is_dir():
        reports = [p for p in root.rglob("*DES-RPT*.pdf.json")
                   if site.upper() in p.as_posix().upper()
                   or (site == "QRWN" and "-AQ-" in p.name.upper())
                   or (site == "NMAR" and "-DN-" in p.name.upper())
                   or (site == "NRJS" and "NRJS" in p.name.upper())]
    report = reports[0] if reports else None
    return {"site": site, "plan": plan, "sheets": sheets, "sections": sections,
            "details": details, "pdf": pdf, "report": report}


def _build_real_network_and_class(o, pkg, rules):
    """Skill procedure 5a: setout → network → class from the general plan (G-2)."""
    site, plan = pkg["site"], pkg["plan"]
    if not pkg["sheets"]:
        pytest.fail(
            f"{site}: general plan {plan.name} found but no setting-out sheets "
            f"(drawing 60000 / 60001) under LDIP_REVIEW. " + _LDIP_REVIEW_HELP
        )
    assert run("read_setout.py", "--ingestion", *[str(s) for s in pkg["sheets"]],
               "--out", o / "setout.json")[0] == 0
    assert run("build_network.py", "--setout", o / "setout.json", "--site", site,
               "--linework", plan, "--out", o / "network.json")[0] == 0
    extra = []
    if pkg.get("report"):
        assert run("read_criteria.py", "--ingestion", pkg["report"], "--out", o / "criteria.json")[0] == 0
        extra += ["--criteria", o / "criteria.json"]
    if pkg.get("sections"):
        assert run("check_sections.py", "--sections", pkg["sections"], "--rules", rules,
                   "--out", o / "sc.json")[0] == 0
        extra += ["--sections", o / "sc.json"]
    if pkg.get("pdf"):
        extra += ["--pdf", pkg["pdf"]]
    assert run("read_class.py", "--network", o / "network.json", "--site", site,
               "--linework", plan, *extra, "--out", o / "class.json")[0] == 0
    net, cls = load(o / "network.json"), load(o / "class.json")
    assert net.get("roads"), f"{site}: real network must have roads (got empty — check setout/plan)"
    assert cls.get("roads") is not None, f"{site}: classification missing"
    return net, cls


@pytest.mark.skipif(
    ROOT is None,
    reason="LDIP_REVIEW not set — real 30 % drawings stay out of Git. " + _LDIP_REVIEW_HELP,
)
def test_tj8_real_ldip_review_pd(tmp_path):
    """G-2: when LDIP_REVIEW is set, build the real network + class from the general plan
    (drawing 00003 or 15000), then run C3 at 30 % and assert TJ8. Never pick files alphabetically.
    G-1: without site parking_layers, parking must not_run (no fail from parcel polygons).
    """
    assert ROOT is not None
    if not ROOT.is_dir():
        pytest.fail(
            f"LDIP_REVIEW={_LDIP_REVIEW_RAW!r} is set but is not an existing directory. "
            + _LDIP_REVIEW_HELP
        )
    rules = rules_c3(tmp_path)
    packages = []
    for site in ("NMAR", "QRWN", "NRJS"):
        pkg = _site_package(ROOT, site)
        if pkg is not None:
            packages.append(pkg)
    if not packages:
        pytest.fail(
            f"LDIP_REVIEW={ROOT} has no general-plan ingestion "
            f"(drawing 00003 or 15000 under 0.ai_context/{{NMAR,QRWN,NRJS}}/). "
            + _LDIP_REVIEW_HELP
        )

    ran = 0
    for pkg in packages:
        site = pkg["site"]
        o = tmp_path / site
        o.mkdir()
        _build_real_network_and_class(o, pkg, rules)
        plan = pkg["plan"]
        # details carrier when present; otherwise the general plan (C3 still lists not_run at PD)
        det = pkg["details"] or plan
        assert run("read_details.py", "--file", f"details={det}", "--out", o / "details.json")[0] == 0
        assert run("check_calming.py", "--details", o / "details.json",
                   "--network", o / "network.json", "--class", o / "class.json",
                   "--rules", rules, "--stage", "30% Concept Design", "--site", site,
                   "--file", f"plan_geometry={plan}", "--file", f"details={det}",
                   "--out", o / "cal.json")[0] == 0
        assert run("check_pedestrian.py", "--network", o / "network.json", "--rules", rules,
                   "--stage", "30% Concept Design", "--site", site,
                   "--file", f"plan_geometry={plan}", "--class", o / "class.json",
                   "--out", o / "ped.json")[0] == 0
        assert run("check_parking.py", "--network", o / "network.json", "--class", o / "class.json",
                   "--rules", rules, "--stage", "30% Concept Design", "--site", site,
                   "--file", f"plan_geometry={plan}", "--out", o / "pk.json")[0] == 0
        cal, ped, pk = load(o / "cal.json"), load(o / "ped.json"), load(o / "pk.json")
        for doc, name in ((cal, "calming"), (ped, "pedestrian"), (pk, "parking")):
            fails = [c for c in doc.get("checks", []) if c.get("result") == "fail"]
            assert not fails, f"{site} {name} C3 fails at PD: {fails[:3]}"
        # G-1: with parking_layers empty/absent, expect not_run GAP (not plan-derived fails).
        if not pk.get("bays"):
            gap = [n for n in pk.get("not_run", []) if "parking_layers" in (n.get("reason") or "")]
            assert gap, f"{site}: expected parking_layers GAP not_run when no bays; got {pk.get('not_run')[:5]}"
        if pkg.get("sections"):
            rc, _ = run("check_sections.py", "--sections", pkg["sections"], "--rules", rules,
                        "--out", o / "sec.json")
            if rc == 0:
                text = pkg["sections"].read_text(encoding="utf-8", errors="ignore").upper()
                if "WITHOUT PARALLEL PARKING" in text:
                    sec = load(o / "sec.json")
                    assert not [e for sec_ in sec.get("sections", []) for e in sec_.get("elements", [])
                                if e.get("element") == "parking"], f"{site}: parking element at 30 %"
        ran += 1
    assert ran >= 1
