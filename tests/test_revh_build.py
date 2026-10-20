"""Tests for the SPC-0006 Rev H build (phase C1: alignment and junction geometry; DH-1 ... DH-10).
TH1 drawn curves (ROD-017 / 018), TH2 arc length and spirals (ROD-019 / 020, D10-5), TH3 junction angles
(ROD-035, through arms, bearing offset), TH4 spacing (ROD-036, discontinuous roads), TH5 grade near a junction
(ROD-037, synthetic LandXML profile), TH6 determinism. Synthetic data only (brief §10.2); the real tests are
in test_revh_real.py."""
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("shapely")
pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parents[1]
SK10 = REPO / "plugin/ldip-design-review/skills/reviewing-road-design/scripts"
PY = sys.executable
E0, N0 = 660000.0, 2750000.0


def run(script, *args, cwd=None):
    p = subprocess.run([PY, str(SK10 / script), *map(str, args)], capture_output=True, text=True, cwd=cwd)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-2000:]})


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def put(p, obj):
    Path(p).write_text(json.dumps(obj), encoding="utf-8")
    return p


# ------------------------------------------------------------------ synthetic inputs
def sta(v):
    return f"{int(v // 1000)}+{v % 1000:07.3f}"


def T(tag, a, b, s0):
    L = math.dist(a, b)
    return {"tag": tag, "type": "tangent", "points": [list(a), list(b)], "radius": None, "length": round(L, 3),
            "stations": [sta(s0), sta(s0 + L)], "control": ["POB", "POE"], "handles": [f"H{tag}"], "delta": None}


def C(tag, pc, pi, pt, R, s0):
    """Curve row: PC / PI / PT, radius; length from the deflection."""
    b1 = math.atan2(pi[1] - pc[1], pi[0] - pc[0])
    b2 = math.atan2(pt[1] - pi[1], pt[0] - pi[0])
    d = abs((b2 - b1 + math.pi) % (2 * math.pi) - math.pi)
    L = R * d
    return {"tag": tag, "type": "curve", "points": [list(pc), list(pi), list(pt)], "radius": R, "length": round(L, 3),
            "stations": [sta(s0), sta(s0 + L)], "control": ["-", "PI", "-"], "handles": [f"H{tag}"],
            "delta": f"{math.degrees(d):.4f}"}


def corner(p_from, v, R, turn_deg, left=True):
    """PC at p_from heading unit v; returns (PC, PI, PT, out_dir) for a circular curve of deflection turn_deg."""
    t = R * math.tan(math.radians(turn_deg) / 2)
    pc = p_from
    pi = (pc[0] + v[0] * t, pc[1] + v[1] * t)
    a = math.radians(turn_deg) * (1 if left else -1)
    w = (v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a))
    pt = (pi[0] + w[0] * t, pi[1] + w[1] * t)
    return pc, pi, pt, w


def al(segs, name):
    return {"segments": segs, "sheets": ["submittals/NMAR/T/SO.dwg"],
            "evidence": {"path": "submittals/NMAR/T/SO.dwg", "sha256": hashlib.sha256(name.encode()).hexdigest(), "title_handle": "T"}}


def curves_world():
    """RD-A local: tangent, R 40 left 90° (radius fail), tangent. RD-D local: R 60, 14° (arc 14.7 m < 20 m). RD-S
    sub-collector: R 100 without spiral; RD-T sub-collector: R 100 between two spirals. RA-1 roundabout (two half
    circles). RD-X: a curve on a road without a class."""
    A = []
    a1 = (E0, N0); a2 = (E0 + 100, N0)
    A.append(T("L1", a1, a2, 0))
    pc, pi, pt, w = corner(a2, (1, 0), 40.0, 90)
    A.append(C("C-1", pc, pi, pt, 40.0, 100))
    A.append(T("L2", pt, (pt[0], pt[1] + 100), 100 + A[-1]["length"]))
    D = []
    d1 = (E0, N0 - 300); d2 = (E0 + 80, N0 - 300)
    D.append(T("L3", d1, d2, 0))
    pc, pi, pt, w = corner(d2, (1, 0), 60.0, 14)
    D.append(C("C-2", pc, pi, pt, 60.0, 80))
    D.append(T("L4", pt, (pt[0] + w[0] * 80, pt[1] + w[1] * 80), 80 + D[-1]["length"]))
    S = []
    s1 = (E0, N0 - 600); s2 = (E0 + 100, N0 - 600)
    S.append(T("L5", s1, s2, 0))
    pc, pi, pt, w = corner(s2, (1, 0), 100.0, 30, left=False)
    S.append(C("C-3", pc, pi, pt, 100.0, 100))
    S.append(T("L6", pt, (pt[0] + w[0] * 100, pt[1] + w[1] * 100), 100 + S[-1]["length"]))
    Tt = []
    t1 = (E0, N0 - 900); t2 = (E0 + 100, N0 - 900)
    Tt.append(T("L7", t1, t2, 0))
    sp1 = {"tag": "S1", "type": "spiral", "points": [list(t2), [t2[0] + 20, t2[1] - 0.5]], "radius": None, "length": 20.0,
           "stations": [sta(100), sta(120)], "handles": ["HS1"]}
    p0 = (t2[0] + 20, t2[1] - 0.5)
    pc, pi, pt, w = corner(p0, (1, 0), 100.0, 20, left=False)
    c4 = C("C-4", pc, pi, pt, 100.0, 120)
    sp2 = {"tag": "S2", "type": "spiral", "points": [list(pt), [pt[0] + w[0] * 20, pt[1] + w[1] * 20]], "radius": None,
           "length": 20.0, "stations": [sta(120 + c4["length"]), sta(140 + c4["length"])], "handles": ["HS2"]}
    Tt += [sp1, c4, sp2]
    RA = [C("C-5", (E0 + 500, N0 - 20.2), (E0 + 480, N0), (E0 + 500, N0 + 20.2), 20.2, 0),
          C("C-6", (E0 + 500, N0 + 20.2), (E0 + 520, N0), (E0 + 500, N0 - 20.2), 20.2, 63.46)]
    RA[0]["length"] = RA[1]["length"] = 63.46
    X = []
    x1 = (E0, N0 - 1200); x2 = (E0 + 50, N0 - 1200)
    X.append(T("L8", x1, x2, 0))
    pc, pi, pt, w = corner(x2, (1, 0), 30.0, 45)
    X.append(C("C-7", pc, pi, pt, 30.0, 50))
    return {"alignments": {"RD-A": al(A, "A"), "RD-D": al(D, "D"), "RD-S": al(S, "S"), "RD-T": al(Tt, "T"),
                           "RA-1": al(RA, "RA"), "RD-X": al(X, "X")}, "counts": {}, "warnings": []}


def class_file(tmp, classes):
    return put(tmp / "class.json", {"roads": {n: {"class": c, "width": 15.0 if c == "local_access" else 25.0}
                                              for n, c in classes.items()}, "summary": {}})


def criteria_file(tmp, local=20, collector=40):
    def cell(v):
        return {"design_speed": {"text": f"{v} km/h", "numbers": [float(v)], "page": 1, "bbox": [0, 0, 1, 1],
                                 "label": "Design speed", "label_bbox": [0, 0, 1, 1]}}
    return put(tmp / "criteria.json", {"criteria": {"local_access": cell(local), "sub_collector": cell(collector)},
                                        "source": {"path": "submittals/NMAR/T/R.pdf", "sha256": "e" * 64}})


def P(value, unit, **kw):
    return {"value": value, "unit": unit, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T", "blocking": False, **kw}


def rule(rid, title, params, classes=("all",), state="active", severity="Minor"):
    return {"rule_id": rid, "title": title, "statement": f"Synthetic {rid}.", "state": state, "cap": "Confirmed",
            "parameters": params, "severity_default": severity, "issue_type": "design_error", "check_type": "value",
            "default_status": {"dwg_vector": "Confirmed", "landxml": "Confirmed", "script_output": "Confirmed",
                               "pdf_vector_text": "Probable"},
            "owner_role": "DMR", "applicability": {"street_class": list(classes)}, "inputs": ["plan_geometry"]}


def rules_file(tmp, skip=(), spacing_state="advisory"):
    rs = [rule("ROD-006", "Design speed local", {"vd_min": P(30, "km/h"), "vd_max": P(40, "km/h")}, ["local_access"]),
          rule("ROD-007", "Design speed sub collector", {"vd_min": P(50, "km/h"), "vd_max": P(60, "km/h")}, ["sub_collector"]),
          rule("ROD-017", "Minimum horizontal radius — sub collector", {"r_min": P(150, "m")}, ["sub_collector"], severity="Major"),
          rule("ROD-018", "Minimum horizontal radius — local access", {"r_min": P(50, "m")}, ["local_access"], severity="Major"),
          rule("ROD-019", "Minimum circular arc length", {"arc_min": P({"30": 20, "40": 25, "50": 30, "60": 35}, "m",
                                                                      keyed_by=["design_speed_kmh"])}),
          rule("ROD-020", "Transition (spiral) curve", {"spiral_upper_radius": P({"40": 95, "50": 148, "60": 213}, "m",
                                                                                 keyed_by=["design_speed_kmh"]),
                                                        "spiral_not_required_up_to": P(30, "km/h")}),
          rule("ROD-035", "Intersection angle", {"angle_range": P([75, 105], "°")}),
          rule("ROD-036", "Spacing between intersections", {"spacing_local": P(150, "m"), "spacing_collector": P(225, "m"),
                                                            "spacing_signalised": P(500, "m")}, state=spacing_state),
          rule("ROD-037", "Grade near intersection", {"g_max": P(3.0, "%", tolerance={"value": 0.05}), "distance": P(30, "m")})]
    keep = [r for r in rs if r["rule_id"] not in skip]
    skipped = [{"rule_id": r, "reason": "inactive"} for r in skip]
    return put(tmp / "rules_RDE.json", {"agent": "RDE", "rulebook_version": "v99", "rules": keep, "rules_skipped": skipped,
                                        "stage_map": {"30% Concept Design": {"phase": "PD"}, "60%": {"phase": "DD"}}})


def geometry(tmp, so, net, cls, rules, stage="30% Concept Design", extra=(), out="geo.json"):
    rc, o = run("check_geometry.py", "--setout", so, "--network", net, "--class", cls, "--rules", rules, "--stage", stage,
                "--criteria", criteria_file(tmp), *extra, "--out", tmp / out)
    assert rc == 0, o
    return load(tmp / out), o


def network_of(tmp, so_obj, name="net.json"):
    so = put(tmp / "so.json", so_obj)
    rc, o = run("build_network.py", "--setout", so, "--site", "NMAR", "--out", tmp / name)
    assert rc == 0, o
    return so, tmp / name


def fails(d, rid):
    return [c for c in d["checks"] if c["rule_id"] == rid and c["result"] == "fail"]


# ------------------------------------------------------------------ TH1
def test_th1_drawn_curves(tmp_path):
    so, net = network_of(tmp_path, curves_world())
    cls = class_file(tmp_path, {"RD-A": "local_access", "RD-D": "local_access", "RD-S": "sub_collector", "RD-T": "sub_collector"})
    d, _ = geometry(tmp_path, so, net, cls, rules_file(tmp_path))
    f18 = fails(d, "ROD-018")
    assert [c["subject"] for c in f18] == ["RD-A C-1 R 40 m"]                       # R 60 on RD-D passes
    assert {c["subject"].split()[0] for c in fails(d, "ROD-017")} == {"RD-S", "RD-T"}  # R 100 < 150
    assert all(c["mode_tag"] == "drawn_curves" and c["link_cap"] == "Probable" for c in f18)
    assert all("RA-1" not in c["subject"] for c in d["checks"])                       # roundabout excluded
    assert any(n["rule_id"] == "ROD-018" and "RD-X" in n["reason"] for n in d["not_run"])   # no class -> not judged
    assert f18[0]["evidence"]["source_type"] == "dwg_vector" and f18[0]["evidence"]["handles"] == ["HC-1"]


# ------------------------------------------------------------------ TH2
def test_th2_arc_length_and_spirals(tmp_path):
    so, net = network_of(tmp_path, curves_world())
    cls = class_file(tmp_path, {"RD-A": "local_access", "RD-D": "local_access", "RD-S": "sub_collector", "RD-T": "sub_collector"})
    d, _ = geometry(tmp_path, so, net, cls, rules_file(tmp_path))
    f19 = fails(d, "ROD-019")
    assert [(c["subject"].split()[0], c["speed_kmh"]) for c in f19] == [("RD-D", 30.0)]   # 14.7 m < 20 m; 20 km/h not a key
    assert any(n["rule_id"] == "ROD-019" and "20 km/h is not a key" in n["reason"] for n in d["not_run"])
    f20 = fails(d, "ROD-020")
    # R 100 m: above the 95 m limit at 40 km/h (no check), at or below 148 m at 50 km/h (ER minimum) -> needs a spiral
    assert sorted((c["subject"].split()[0], c["speed_kmh"]) for c in f20) == [("RD-S", 50.0)]
    assert all("no spiral in the setting-out table" in c["observed"] for c in f20)
    assert [c["result"] for c in d["checks"] if c["rule_id"] == "ROD-020" and c["subject"].startswith("RD-T")] == ["pass"]
    assert any(n["rule_id"] == "ROD-020" and "not required up to 30" in n["reason"] for n in d["not_run"])   # local roads
    # findings: D10-5 stated + ER minimum in one finding; drawn curves apart from criteria (DH-2)
    rc, o = run("build_candidates.py", "--results", tmp_path / "geo.json", "--rules", tmp_path / "rules_RDE.json",
                "--run", "NMAR-T-20261017T1300", "--site", "NMAR", "--sub", "T", "--evidence-dir", tmp_path / "ev",
                "--out", tmp_path / "cand.json")
    assert rc == 0, o
    f = load(tmp_path / "cand.json")
    r20 = [x for x in f if x["reference"]["rule_id"] == "ROD-020"]
    assert len(r20) == 1 and r20[0]["statement"].startswith("1 occurrence(s)") and r20[0]["proposed_status"] == "Probable"
    r17 = [x for x in f if x["reference"]["rule_id"] == "ROD-017"]
    assert len(r17) == 1 and r17[0]["severity"] == "Major" and "dwg" in r17[0]["location"]
    assert r20[0]["agent_versions"]["reviewing-road-design"] == "0.7.0"
    rc, o = run("validate_findings.py", "--findings", tmp_path / "cand.json", "--rules", tmp_path / "rules_RDE.json")
    assert rc == 0, o


# ------------------------------------------------------------------ TH3
def junction_world(angle_deg=60.0, curve_at_start=False):
    """RD-A east-west (0 ... 300); RD-B leaves RD-A at E 100 at `angle_deg`; RD-C1 + RD-C2 (two names, one straight
    line) cross RD-E at E 250 (J051 pattern). With curve_at_start RD-B starts with a short R 20 curve."""
    A = [T("L1", (E0, N0), (E0 + 300, N0), 0)]
    a = math.radians(angle_deg)
    v = (math.cos(a), math.sin(a))
    start = (E0 + 100, N0)
    if curve_at_start:
        pc, pi, pt, w = corner(start, (0, 1), 20.0, 90 - angle_deg, left=False)
        B = [C("C-1", pc, pi, pt, 20.0, 0)]
        B.append(T("L2", pt, (pt[0] + w[0] * 120, pt[1] + w[1] * 120), B[0]["length"]))
    else:
        B = [T("L2", start, (start[0] + v[0] * 120, start[1] + v[1] * 120), 0)]
    C1 = [T("L3", (E0 + 200, N0 + 100), (E0 + 250, N0 + 100), 0)]
    C2 = [T("L4", (E0 + 250, N0 + 100), (E0 + 300, N0 + 100), 0)]
    Ee = [T("L5", (E0 + 250, N0), (E0 + 250, N0 + 200), 0)]
    return {"alignments": {"RD-A": al(A, "A"), "RD-B": al(B, "B"), "RD-C1": al(C1, "C1"), "RD-C2": al(C2, "C2"),
                           "RD-E": al(Ee, "E")}, "counts": {}, "warnings": []}


def test_th3_junction_angles(tmp_path):
    so, net = network_of(tmp_path, junction_world(60.0))
    cls = class_file(tmp_path, {n: "local_access" for n in ("RD-A", "RD-B", "RD-C1", "RD-C2", "RD-E")})
    d, _ = geometry(tmp_path, so, net, cls, rules_file(tmp_path))
    f35 = fails(d, "ROD-035")
    assert len(f35) == 1 and "RD-A / RD-B" in f35[0]["subject"] and abs(f35[0]["observed"] - 60.0) < 0.2
    cross = [c for c in d["checks"] if c["rule_id"] == "ROD-035" and "RD-C1" in c["subject"]]
    assert len(cross) == 1 and cross[0]["result"] == "pass" and abs(cross[0]["observed"] - 90.0) < 0.2   # one through arm
    # 80°: pass; a curve at the start of RD-B: the bearing 10 m out follows the curve, not the tangent beyond it
    so2, net2 = network_of(tmp_path, junction_world(80.0), "net80.json")
    d2, _ = geometry(tmp_path, so2, net2, cls, rules_file(tmp_path), out="g80.json")
    assert not fails(d2, "ROD-035")
    so3, net3 = network_of(tmp_path, junction_world(30.0, curve_at_start=True), "netc.json")
    d3, _ = geometry(tmp_path, so3, net3, cls, rules_file(tmp_path), out="gc.json")
    jb = [c for c in d3["checks"] if c["rule_id"] == "ROD-035" and "RD-B" in c["subject"]][0]
    assert jb["observed"] > 60.0                       # measured 10 m along the curve (near 90°), not 30°
    assert load(net3)["angle_offset_m"] == 10.0


# ------------------------------------------------------------------ TH4
def test_th4_spacing(tmp_path):
    w = junction_world(90.0)
    # RD-A gets side roads at E 100 (RD-B), 220 (RD-F) and 250 (RD-E): spacings 120 m and 30 m
    w["alignments"]["RD-F"] = al([T("L6", (E0 + 220, N0), (E0 + 220, N0 - 80), 0)], "F")
    so, net = network_of(tmp_path, w)
    cls = class_file(tmp_path, {"RD-A": "local_access", "RD-B": "local_access", "RD-C1": "local_access",
                                "RD-C2": "local_access", "RD-E": "sub_collector", "RD-F": "local_access"})
    d, _ = geometry(tmp_path, so, net, cls, rules_file(tmp_path))
    sp = sorted(c["observed"] for c in fails(d, "ROD-036") if c["subject"].startswith("RD-A"))
    assert sp == [30.0, 120.0]
    e = [c for c in d["checks"] if c["rule_id"] == "ROD-036" and c["subject"].startswith("RD-E")]
    assert [c["observed"] for c in e] == [100.0] and e[0]["parameter"] == "spacing_collector"
    assert any(n.get("parameter") == "spacing_signalised" for n in d["not_run"])
    assert all(c["severity_override"] == "Observation" for c in fails(d, "ROD-036"))        # advisory (DH-7)
    # a discontinuous road (two pieces with a gap): chainage from the table stations
    w2 = junction_world(90.0)
    w2["alignments"]["RD-A"] = al([T("L1", (E0, N0), (E0 + 140, N0), 0), T("L1b", (E0 + 145, N0), (E0 + 300, N0), 145)], "A")
    w2["alignments"]["RD-F"] = al([T("L6", (E0 + 220, N0), (E0 + 220, N0 - 80), 0)], "F")
    so2, net2 = network_of(tmp_path, w2, "net2.json")
    n2 = load(net2)
    assert n2["roads"]["RD-A"]["continuous"] is False
    locs = {x["location"]: x for x in n2["roads"]["RD-A"]["locations"]}
    assert sorted(x["chainage"] for x in locs.values()) == [100.0, 220.0, 250.0]
    assert all(x.get("chainage_basis") == "stations" for x in locs.values())


# ------------------------------------------------------------------ TH5
NS = "http://www.landxml.org/schema/LandXML-1.2"


def lx_profile(tmp, pvis, curves=()):
    """Skill 3-like ingestion with one alignment RD-A (sta 0 at E0) and one design profile."""
    tg = [{"sta_from": pvis[i][0], "sta_to": pvis[i + 1][0],
           "grade_pct": round(100 * (pvis[i + 1][1] - pvis[i][1]) / (pvis[i + 1][0] - pvis[i][0]), 4)} for i in range(len(pvis) - 1)]
    ing = {"template_version": "1.1", "source": {"path": "submittals/NMAR/T/R.xml", "sha256": "f" * 64, "file_type": "landxml"},
           "content": {"alignments": [{"name": "RD-A", "sta_start": 0.0, "length": 300.0, "station_equations": [],
                                       "elements": [{"index": 1, "type": "line", "start": [E0, N0], "end": [E0 + 300, N0],
                                                     "length": 300.0, "sta_start": 0.0}]}],
                       "profiles": [{"name": "FG", "alignment": "RD-A", "kind": "design",
                                     "pvis": [{"sta": s, "elev": z} for s, z in pvis], "tangents": tg,
                                     "curves": [{"pvi_sta": s, "length": L, "curve": "sag"} for s, L in curves]}]}}
    return put(tmp / "x.xml.json", ing)


def test_th5_grade_near_junction(tmp_path):
    so, net = network_of(tmp_path, junction_world(90.0))
    cls = class_file(tmp_path, {n: "local_access" for n in ("RD-A", "RD-B", "RD-C1", "RD-C2", "RD-E")})
    r = rules_file(tmp_path)
    # junction RD-A / RD-B at sta 100: 4 % between sta 80 and 120 -> fail
    x = lx_profile(tmp_path, [(0, 10.0), (80, 10.0), (120, 11.6), (300, 11.6)])
    d, _ = geometry(tmp_path, so, net, cls, r, stage="60%", extra=("--landxml", x))
    f = [c for c in fails(d, "ROD-037") if "RD-A" in c["subject"]]
    assert f and abs(f[0]["observed"] - 4.0) < 0.01 and f[0]["evidence"]["source_type"] == "landxml"
    # 2.9 % -> pass
    x2 = lx_profile(tmp_path, [(0, 10.0), (100, 12.9), (300, 12.9)])
    d2, _ = geometry(tmp_path, so, net, cls, r, stage="60%", extra=("--landxml", x2), out="g2.json")
    assert not [c for c in fails(d2, "ROD-037") if "RD-A" in c["subject"]]
    # a vertical curve across the window: grade 0 -> 6 % over a 100 m curve centred at sta 160; window 70 ... 130 reaches
    # the curve between 110 and 130 -> steepest 1.2 % at sta 130 (pass); centred at 120 -> 3.6 % at 130 (fail)
    x3 = lx_profile(tmp_path, [(0, 10.0), (160, 10.0), (300, 18.4)], curves=[(160, 100.0)])
    d3, _ = geometry(tmp_path, so, net, cls, r, stage="60%", extra=("--landxml", x3), out="g3.json")
    a3 = [c for c in d3["checks"] if c["rule_id"] == "ROD-037" and "J" in c["subject"] and "RD-A" in c["subject"]
          and "sta 100" in c["subject"]]
    assert a3 and abs(a3[0]["observed"] - 1.2) < 0.01 and a3[0]["result"] == "pass"
    x4 = lx_profile(tmp_path, [(0, 10.0), (120, 10.0), (300, 20.8)], curves=[(120, 100.0)])
    d4, _ = geometry(tmp_path, so, net, cls, r, stage="60%", extra=("--landxml", x4), out="g4.json")
    a4 = [c for c in d4["checks"] if c["rule_id"] == "ROD-037" and "sta 100" in c["subject"] and "RD-A" in c["subject"]]
    assert a4 and abs(a4[0]["observed"] - 3.6) < 0.01 and a4[0]["result"] == "fail"
    # PD without profiles -> not run
    d5, _ = geometry(tmp_path, so, net, cls, r, out="g5.json")
    assert any(n["rule_id"] == "ROD-037" and "no LandXML design profile" in n["reason"] for n in d5["not_run"])


# ------------------------------------------------------------------ TH6 + rule states + tangents
def test_th6_determinism_inactive_and_tangents(tmp_path):
    so, net = network_of(tmp_path, curves_world())
    cls = class_file(tmp_path, {"RD-A": "local_access", "RD-D": "local_access", "RD-S": "sub_collector", "RD-T": "sub_collector"})
    r = rules_file(tmp_path)
    geometry(tmp_path, so, net, cls, r, out="a.json")
    geometry(tmp_path, so, net, cls, r, out="b.json")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
    d = load(tmp_path / "a.json")
    t = {(x["road"], x["tag"]): x for x in d["tangents"]}
    assert t[("RD-A", "L2")]["between"] == ["C-1"] and d["counts"]["tangents"] == sum(1 for _ in t)
    assert not [c for c in d["checks"] if c["subject"].startswith("RA-1")]
    # inactive rules are listed, never run
    r2 = rules_file(tmp_path, skip=("ROD-019", "ROD-020", "ROD-035", "ROD-036", "ROD-037"))
    d2, _ = geometry(tmp_path, so, net, cls, r2, out="c.json")
    assert {c["rule_id"] for c in d2["checks"]} <= {"ROD-017", "ROD-018"}
    assert {n["rule_id"] for n in d2["not_run"] if n["reason"] == "inactive"} == {"ROD-019", "ROD-020", "ROD-035", "ROD-036", "ROD-037"}
    # LandXML as position source: ROD-017 / 018 left to check_alignment.py (phase A)
    w = curves_world()
    w["position_source"] = "landxml"
    so3, net3 = network_of(tmp_path, w, "net3.json")
    d3, _ = geometry(tmp_path, so3, net3, cls, r, out="d.json")
    assert d3["position_source"] == "landxml" and not [c for c in d3["checks"] if c["rule_id"] in ("ROD-017", "ROD-018")]
    assert any(n["rule_id"] == "ROD-017" and "check_alignment.py" in n["reason"] for n in d3["not_run"])
