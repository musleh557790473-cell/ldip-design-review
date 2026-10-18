"""Tests for the SPC-0006 Rev I build (phase C2: junction and roundabout layout; DI-1 ... DI-10).
TI1 corner returns (ROD-032, DI-2 a), TI2 design vehicle with a declared departure, TI3 roundabout ICD (ROD-039),
TI4 splitter islands and entry width (ROD-042 / 040), TI5 apron (ROD-043), TI6 driveways and sight triangles (DD,
ROD-033 / 034), TI7 determinism. Synthetic data only (brief §10.2); the real tests are in test_revi_real.py."""
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
PD, DD = "30% Concept Design", "60%"


def run(script, *args):
    p = subprocess.run([PY, str(SK10 / script), *map(str, args)], capture_output=True, text=True)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-2000:]})


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def put(p, obj):
    Path(p).write_text(json.dumps(obj), encoding="utf-8")
    return p


# ------------------------------------------------------------------ synthetic inputs
def arc_pts(c, R, a0, a1, step=0.5):
    n = max(8, int(abs(math.radians(a1 - a0)) * R / step))
    return [[c[0] + R * math.cos(math.radians(a0 + (a1 - a0) * i / n)), c[1] + R * math.sin(math.radians(a0 + (a1 - a0) * i / n))]
            for i in range(n + 1)]


def poly_corner(c, R, a0, a1, lead=20.0):
    """A polyline: a straight lead-in, a curve flattened into ~1 m chords (as skill 1 flattens bulges), a lead-out."""
    pts = arc_pts(c, R, a0, a1, step=1.0)
    (x0, y0), (x1, y1) = pts[0], pts[1]
    L = math.dist((x0, y0), (x1, y1))
    before = [x0 - (x1 - x0) / L * lead, y0 - (y1 - y0) / L * lead]
    (u0, v0), (u1, v1) = pts[-2], pts[-1]
    L = math.dist((u0, v0), (u1, v1))
    after = [u1 + (u1 - u0) / L * lead, v1 + (v1 - v0) / L * lead]
    return [before] + pts + [after]


class World:
    """A plan DWG ingestion file with a line-work side file (skill 1 shape), a network and road classes."""

    def __init__(self, tmp):
        self.tmp, self.recs, self.blocks, self.n = tmp, [], [], 0
        self.net = {"site": "NMAR", "junctions": [], "roundabouts": [], "roads": {}}
        self.cls = {}

    def rec(self, typ, pts, layer="__Curb", closed=False):
        self.n += 1
        h = f"H{self.n:03d}"
        self.recs.append({"handle": h, "layer": f"X$0${layer}", "layer_short": layer, "type": typ, "closed": closed,
                          "points": [list(map(float, p)) for p in pts], "via_block": []})
        return h

    def road(self, name, cls, pts):
        self.net["roads"][name] = {"geometry": [[list(p) for p in pts]]}
        if cls:
            self.cls[name] = cls

    def junction(self, jid, xy, roads):
        self.net["junctions"].append({"id": jid, "xy": list(xy), "roads": roads})

    def files(self, name="P"):
        src = {"path": f"submittals/NMAR/T/{name}.dwg", "sha256": hashlib.sha256(name.encode()).hexdigest(), "file_type": "dwg"}
        put(self.tmp / f"{name}.linework.json", {"schema_version": "1.0", "source": src, "records": self.recs, "units": "m"})
        dwg = put(self.tmp / f"{name}.dwg.json", {"source": src, "content": {
            "side_files": [{"kind": "linework", "path": f"{name}.linework.json"}], "texts": [], "blocks": self.blocks}})
        net = put(self.tmp / "network.json", self.net)
        cls = put(self.tmp / "class.json", {"roads": {n: {"class": c} for n, c in self.cls.items()}, "summary": {}})
        return dwg, net, cls


def P(value, unit, **kw):
    return {"value": value, "unit": unit, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T", "blocking": False, **kw}


def rule(rid, title, params, appl=None, severity="Minor", issue="design_error"):
    r = {"rule_id": rid, "title": title, "statement": f"Synthetic {rid}.", "state": "active", "cap": "Confirmed",
         "parameters": params, "severity_default": severity, "issue_type": issue, "check_type": "value",
         "default_status": {"dwg_vector": "Confirmed", "landxml": "Confirmed", "script_output": "Confirmed",
                            "pdf_vector_text": "Probable"},
         "owner_role": "DMR", "inputs": ["plan_geometry"]}
    if appl:
        r["applicability"] = appl
    return r


def rules_file(tmp, skip=(), advisory=()):
    W = {"value": 0.05}
    rs = [rule("ROD-032", "Corner (curb return) radius at intersections",
               {"corner_radius": P({"sub_collector": [12, 15], "local_access": [6, 9]}, "m", keyed_by=["street_class"]),
                "design_vehicle": P("WB-15 or city bus as appropriate", None),
                "swept_path": P("no encroachment", None, enforcement="advisory", cap="Probable")},
               {"street_class": ["sub_collector", "local_access"]}, severity="Major"),
          rule("ROD-033", "Driveway curb return radius", {"driveway_radius": P({"sub_collector": 6.0, "local_access": 4.0}, "m")},
               {"street_class": ["sub_collector", "local_access"]}, severity="Major"),
          rule("ROD-034", "Clear sight triangle", {"intersection_triangle": P([10, 10], "m"), "driveway_triangle": P([5, 5], "m")},
               severity="Major"),
          rule("ROD-039", "Roundabout inscribed circle diameter",
               {"icd": P({"C-C": [40, 55], "C-L": [30, 40], "L-L": [25, 30]}, "m", keyed_by=["junction_type"])}),
          rule("ROD-040", "Roundabout entry width",
               {"entry_width": P({"C-C": [6.0, 7.0], "C-L": [5.0, 6.0], "L-L": [4.5, 5.5]}, "m", tolerance=W)}),
          rule("ROD-041", "Roundabout entry deflection", {"deflection": P({"C-C": [20, 30]}, "°")}),
          rule("ROD-042", "Splitter island", {"width_min": P(1.8, "m", tolerance=W), "length_min": P(15, "m"),
                                              "length_desirable": P(30, "m", enforcement="advisory", cap="Probable")}),
          rule("ROD-043", "Roundabout apron, circulatory crossfall and drainage",
               {"apron_width": P([1.5, 2.0], "m", tolerance=W), "circulatory_crossfall": P(2.0, "%")})]
    keep = [r for r in rs if r["rule_id"] not in skip]
    for r in keep:
        if r["rule_id"] in advisory:
            r["state"] = "advisory"
    return put(tmp / "rules_RDE.json", {"agent": "RDE", "rulebook_version": "v99", "rules": keep,
                                        "rules_skipped": [{"rule_id": r, "reason": "inactive"} for r in skip],
                                        "stage_map": {PD: {"phase": "PD"}, DD: {"phase": "DD"}}})


def criteria(tmp, text="Passenger Vehicle", deps=True, name="criteria.json"):
    c = {"criteria": {"local_access": {"design_vehicle": {"text": text, "numbers": [], "page": 3, "bbox": [10, 10, 60, 18],
                                                          "label": "Design vehicle"}}} if text else {},
         "departures": [{"ref": "DEP-04", "page": 4, "text": "Design Vehicle Passenger vehicle; WB-15 would need larger "
                                                             "curb return radii"}] if deps else [],
         "source": {"path": "submittals/NMAR/T/R.pdf", "sha256": "e" * 64}}
    return put(tmp / name, c)


def layout(tmp, w, stage=PD, crit=None, rules=None, out="lay.json", extra=()):
    dwg, net, cls = w.files()
    args = ["--network", net, "--class", cls, "--rules", rules or rules_file(tmp), "--stage", stage,
            "--file", f"plan_geometry={dwg}", *extra, "--out", tmp / out]
    if crit:
        args[-2:-2] = ["--criteria", crit]
    rc, o = run("check_layout.py", *args)
    assert rc == 0, o
    return load(tmp / out)


def checks(d, rid, pname=None, result=None):
    return [c for c in d["checks"] if c["rule_id"] == rid and (pname is None or c["parameter"] == pname)
            and (result is None or c["result"] == result)]


def corner_world(tmp):
    """J1 local / local at the origin: corner R 8 (pass), corner R 10 (listed), a road bend R 20 40 m away and a 20°
    arc (both ignored). J2 collector / local: corner R 13 (pass under the highest class). J3 local / local: a
    polyline corner R 8 drawn as chords (found like an ARC)."""
    w = World(tmp)
    w.road("RD-A", "local_access", [(E0 - 200, N0), (E0 + 1200, N0)])
    w.road("RD-B", "local_access", [(E0, N0), (E0, N0 + 200)])
    w.road("RD-C", "sub_collector", [(E0 + 500, N0), (E0 + 500, N0 + 200)])
    w.road("RD-D", "local_access", [(E0 + 1000, N0), (E0 + 1000, N0 + 200)])
    w.junction("J1", (E0, N0), ["RD-A", "RD-B"])
    w.junction("J2", (E0 + 500, N0), ["RD-A", "RD-C"])
    w.junction("J3", (E0 + 1000, N0), ["RD-A", "RD-D"])
    w.rec("ARC", arc_pts((E0 + 15, N0 + 15), 8.0, 180, 270))           # J1 NE corner R 8
    w.rec("ARC", arc_pts((E0 - 17, N0 + 17), 10.0, 270, 360))          # J1 NW corner R 10
    w.rec("ARC", arc_pts((E0 + 40, N0 + 60), 20.0, 200, 260))          # bend 40+ m away
    w.rec("ARC", arc_pts((E0 + 12, N0 - 12), 8.0, 90, 110))            # sweep 20°
    w.rec("ARC", arc_pts((E0 + 520, N0 + 20), 13.0, 180, 270))         # J2 corner R 13
    w.rec("LWPOLYLINE", poly_corner((E0 + 1015, N0 + 15), 8.0, 180, 270))   # J3 polyline corner
    return w


# ------------------------------------------------------------------ TI1
def test_ti1_corner_returns(tmp_path):
    w = corner_world(tmp_path)
    d = layout(tmp_path, w)
    cr = checks(d, "ROD-032", "corner_radius")
    got = sorted((c["junction"], c["observed"], c["class"], c["result"]) for c in cr)
    assert got == [("J1", 8.0, "local_access", "pass"), ("J1", 10.0, "local_access", "fail"),
                   ("J2", 13.0, "sub_collector", "pass"), ("J3", 8.0, "local_access", "pass")]
    j3 = [c for c in cr if c["junction"] == "J3"][0]
    assert "LWPOLYLINE" in j3["evidence"]["extract"] and j3["link_cap"] == "Probable"
    assert d["corners"] == {"local_access": {"8": 2, "10": 1}, "sub_collector": {"13": 1}}
    assert any(n.get("parameter") == "swept_path" for n in d["not_run"])
    assert any(n["rule_id"] == "ROD-041" and "R-I1" in n["reason"] for n in d["not_run"])
    # a junction with a road without a class is not judged (K23)
    del w.cls["RD-D"]
    d2 = layout(tmp_path, w, out="lay2.json")
    assert not [c for c in checks(d2, "ROD-032", "corner_radius") if c["junction"] == "J3"]
    assert any("K23" in n["reason"] and "J3" in n["reason"] for n in d2["not_run"])
    # findings: one per class, Probable, dwg location
    rc, o = run("build_candidates.py", "--results", tmp_path / "lay.json", "--rules", tmp_path / "rules_RDE.json",
                "--run", "NMAR-T-20261018T1300", "--site", "NMAR", "--sub", "T", "--evidence-dir", tmp_path / "ev",
                "--out", tmp_path / "cand.json")
    assert rc == 0, o
    f = load(tmp_path / "cand.json")
    r32 = [x for x in f if x["reference"]["rule_id"] == "ROD-032"]
    assert len(r32) == 1 and r32[0]["proposed_status"] == "Probable" and r32[0]["severity"] == "Major"
    assert r32[0]["agent_versions"]["reviewing-road-design"] == "0.6.0" and "dwg" in r32[0]["location"]
    rc, o = run("validate_findings.py", "--findings", tmp_path / "cand.json", "--rules", tmp_path / "rules_RDE.json")
    assert rc == 0, o


def test_ti1_curb_layer_not_set(tmp_path):
    w = corner_world(tmp_path)
    w.net["site"] = "XXXX"
    d = layout(tmp_path, w)
    assert not checks(d, "ROD-032", "corner_radius")
    assert any(n["rule_id"] == "ROD-032" and "curb layer not set" in n["reason"] for n in d["not_run"])


# ------------------------------------------------------------------ TI2
def test_ti2_design_vehicle(tmp_path):
    w = corner_world(tmp_path)
    d = layout(tmp_path, w, crit=criteria(tmp_path))
    dv = checks(d, "ROD-032", "design_vehicle")
    assert len(dv) == 1 and dv[0]["result"] == "fail" and dv[0]["departures_quoted"] == ["DEP-04"]
    assert "DEP-04" in dv[0]["evidence"]["extract"] and "DEP-04" in dv[0]["subject"]
    assert dv[0]["evidence"]["source_type"] == "pdf_vector_text" and dv[0]["evidence"]["page"] == 3
    d2 = layout(tmp_path, w, crit=criteria(tmp_path, "WB-15", name="c2.json"), out="l2.json")
    assert not checks(d2, "ROD-032", "design_vehicle")
    assert any(p.get("parameter") == "design_vehicle" for p in d2["passed"])
    # not stated: PD -> not run; DD -> Missing information (C5)
    d3 = layout(tmp_path, w, crit=criteria(tmp_path, None, name="c3.json"), out="l3.json")
    assert any(n.get("parameter") == "design_vehicle" and "C5" in n["reason"] for n in d3["not_run"])
    d4 = layout(tmp_path, w, stage=DD, crit=criteria(tmp_path, None, name="c4.json"), out="l4.json")
    m = checks(d4, "ROD-032", "design_vehicle")
    assert len(m) == 1 and m[0]["status_override"] == "Missing information" and "link_cap" not in m[0]
    rc, o = run("build_candidates.py", "--results", tmp_path / "l4.json", "--rules", tmp_path / "rules_RDE.json",
                "--run", "NMAR-T-20261018T1300", "--site", "NMAR", "--sub", "T", "--evidence-dir", tmp_path / "ev",
                "--out", tmp_path / "cand.json")
    assert rc == 0, o
    f = [x for x in load(tmp_path / "cand.json") if x["reference"]["parameter"] == "design_vehicle"]
    assert f[0]["proposed_status"] == "Missing information" and f[0]["issue_type"] == "missing_information"


# ------------------------------------------------------------------ TI3 - TI5 roundabout world
RC = (E0, N0 - 1000)


def rb_world(tmp, classes=("local_access", "local_access"), outer=True, apron=None, islands=True, entry=5.2):
    """RA-1 at RC: island CIRCLE R 12; outer curb R 20.2 in four 60° pieces; approaches RD-N (north) and RD-S (south)
    drawn to the centre. North splitter island 4.3 × 7.3 m; south island 2.0 × 16 m. Entry-side curb of the north
    approach (west, right of a vehicle heading south) at `entry` m from the island; exit-side curb 3 m east."""
    w = World(tmp)
    w.road("RD-N", classes[0], [RC, (RC[0], RC[1] + 150)])
    w.road("RD-S", classes[1], [RC, (RC[0], RC[1] - 150)])
    for n in ("RD-N", "RD-S"):
        if w.cls.get(n) is None:
            w.cls.pop(n, None)
    w.net["roundabouts"].append({"name": "RA-1", "centre": list(RC), "radius": 20.2, "basis": "table",
                                 "approaches": [{"road": "RD-N", "xy": list(RC)}, {"road": "RD-S", "xy": list(RC)}],
                                 "evidence": {"path": "submittals/NMAR/T/SO.dwg", "sha256": "a" * 64}})
    w.rec("CIRCLE", arc_pts(RC, 12.0, 0, 360), closed=True)
    if outer:
        for a0 in (105, 195, 285, 15):
            w.rec("ARC", arc_pts(RC, 20.2, a0, a0 + 60))
    if apron:
        w.rec("CIRCLE", arc_pts(RC, 12.0 + apron, 0, 360), closed=True)
    if islands:
        x, y = RC
        w.rec("LWPOLYLINE", [(x - 2.15, y + 21), (x + 2.15, y + 21), (x + 2.15, y + 28.3), (x - 2.15, y + 28.3),
                             (x - 2.15, y + 21)], closed=True)
        w.rec("LWPOLYLINE", [(x - 1.0, y - 21), (x + 1.0, y - 21), (x + 1.0, y - 37), (x - 1.0, y - 37), (x - 1.0, y - 21)],
              closed=True)
        w.rec("LINE", [(x - 2.15 - entry, y + 21), (x - 2.15 - entry, y + 45)])
        w.rec("LINE", [(x + 2.15 + 3.0, y + 21), (x + 2.15 + 3.0, y + 45)])
    return w


# ------------------------------------------------------------------ TI3
def test_ti3_roundabout_icd(tmp_path):
    d = layout(tmp_path, rb_world(tmp_path, ("sub_collector", "local_access")))
    icd = checks(d, "ROD-039")
    assert [(c["observed"], c["class"], c["result"]) for c in icd] == [(40.4, "C-L", "fail")]
    rb = d["roundabouts"][0]
    assert rb["island_R"] == 12.0 and rb["outer_R"] == 20.2 and rb["outer_basis"] == "outer curb line work"
    assert sorted(x["leg"] for x in rb["legs"]) == ["RD-N", "RD-S"]           # approaches drawn to the centre
    d2 = layout(tmp_path, rb_world(tmp_path, ("sub_collector", "sub_collector")), out="l2.json")
    assert [(c["class"], c["result"]) for c in checks(d2, "ROD-039")] == [("C-C", "pass")]
    d3 = layout(tmp_path, rb_world(tmp_path, ("sub_collector", "local_access"), outer=False), out="l3.json")
    c3 = checks(d3, "ROD-039")[0]
    assert c3["observed"] == 40.4 and "setting-out radius" in c3["outer_basis"]
    d4 = layout(tmp_path, rb_world(tmp_path, ("sub_collector", None)), out="l4.json")
    assert not checks(d4, "ROD-039") and any(n["rule_id"] == "ROD-039" and "unknown" in n["reason"] for n in d4["not_run"])


# ------------------------------------------------------------------ TI4
def test_ti4_splitter_islands_and_entry(tmp_path):
    d = layout(tmp_path, rb_world(tmp_path))
    isl = {i["approach"]: (i["width"], i["length"]) for i in d["roundabouts"][0]["islands"]}
    assert isl == {"RD-N": (4.3, 7.3), "RD-S": (2.0, 16.0)}
    lm = {c["subject"].split(" on ")[1].split()[0]: c["result"] for c in checks(d, "ROD-042", "length_min")}
    assert lm == {"RD-N": "fail", "RD-S": "pass"}
    assert all(c["result"] == "pass" for c in checks(d, "ROD-042", "width_min"))
    ld = checks(d, "ROD-042", "length_desirable")
    assert len(ld) == 1 and ld[0]["result"] == "fail" and ld[0]["severity_override"] == "Observation"
    ew = checks(d, "ROD-040")
    assert [(c["observed"], c["class"], c["result"]) for c in ew if "RD-N" in c["subject"]] == [(5.2, "L-L", "pass")]
    d2 = layout(tmp_path, rb_world(tmp_path, entry=6.5), out="l2.json")
    assert [(c["observed"], c["result"]) for c in checks(d2, "ROD-040") if "RD-N" in c["subject"]] == [(6.5, "fail")]
    d3 = layout(tmp_path, rb_world(tmp_path, islands=False), out="l3.json")
    assert not checks(d3, "ROD-040") and not checks(d3, "ROD-042")
    assert sum(1 for n in d3["not_run"] if n["rule_id"] == "ROD-040" and "not bounded" in n["reason"]) == 2
    assert d3["roundabouts"][0]["approaches_without_island"] == ["RD-N", "RD-S"]


# ------------------------------------------------------------------ TI5
def test_ti5_apron(tmp_path):
    d = layout(tmp_path, rb_world(tmp_path, apron=1.7))
    assert [(c["observed"], c["result"]) for c in checks(d, "ROD-043")] == [(1.7, "pass")]
    d2 = layout(tmp_path, rb_world(tmp_path, apron=1.0), out="l2.json")
    assert [(c["observed"], c["result"]) for c in checks(d2, "ROD-043")] == [(1.0, "fail")]
    d3 = layout(tmp_path, rb_world(tmp_path), out="l3.json")
    assert not checks(d3, "ROD-043")
    assert any(n["rule_id"] == "ROD-043" and n.get("parameter") == "apron_width" for n in d3["not_run"])
    assert any(n["rule_id"] == "ROD-043" and "phase D" in n["reason"] for n in d3["not_run"])
    d4 = layout(tmp_path, rb_world(tmp_path), stage=DD, out="l4.json")
    m = checks(d4, "ROD-043")
    assert len(m) == 1 and m[0]["status_override"] == "Missing information" and m[0]["issue_type"] == "missing_information"


# ------------------------------------------------------------------ TI6
def dd_world(tmp, r_drive=4.0, palm=True):
    """J1 local / local with two corners R 8 (NE: a palm block in its sight triangle; NW: clear); a driveway return
    on RD-A 80 m east of J1, next to a plot outline."""
    w = World(tmp)
    w.road("RD-A", "local_access", [(E0 - 200, N0), (E0 + 300, N0)])
    w.road("RD-B", "local_access", [(E0, N0), (E0, N0 + 200)])
    w.junction("J1", (E0, N0), ["RD-A", "RD-B"])
    w.rec("ARC", arc_pts((E0 + 15, N0 + 15), 8.0, 180, 270))
    w.rec("ARC", arc_pts((E0 - 15, N0 + 15), 8.0, 270, 360))
    w.rec("ARC", arc_pts((E0 + 80 + r_drive, N0 + 7 + r_drive), r_drive, 180, 270))
    w.rec("LWPOLYLINE", [(E0 + 70, N0 + 12), (E0 + 100, N0 + 12), (E0 + 100, N0 + 40), (E0 + 70, N0 + 40), (E0 + 70, N0 + 12)],
          layer="Residential plots", closed=True)
    if palm:
        w.blocks.append({"name": "PALM_TREE_01", "insert": [E0 + 9.0, N0 + 10.0, 0.0], "layer": "L-PLNT", "space": "model",
                         "handle": "B1"})
    return w


def test_ti6_driveways_and_sight_triangles(tmp_path):
    d = layout(tmp_path, dd_world(tmp_path), stage=DD)
    dw = checks(d, "ROD-033")
    assert [(c["observed"], c["class"], c["result"]) for c in dw] == [(4.0, "local_access", "pass")]
    tri = checks(d, "ROD-034", "intersection_triangle")
    assert sorted(c["result"] for c in tri) == ["fail", "pass"]
    bad = [c for c in tri if c["result"] == "fail"][0]
    assert bad["obstructions"] == ["block PALM_TREE_01 (B1)"] and "no landscape plan given" in bad["evidence"]["extract"]
    assert [c["result"] for c in checks(d, "ROD-034", "driveway_triangle")] == ["pass"]
    d2 = layout(tmp_path, dd_world(tmp_path, r_drive=6.0), stage=DD, out="l2.json")
    assert [(c["observed"], c["result"]) for c in checks(d2, "ROD-033")] == [(6.0, "fail")]
    d3 = layout(tmp_path, dd_world(tmp_path), stage=PD, out="l3.json")
    assert not checks(d3, "ROD-033") and not checks(d3, "ROD-034")
    assert {n["rule_id"] for n in d3["not_run"] if "not required at stage" in n["reason"]} == {"ROD-033", "ROD-034"}


# ------------------------------------------------------------------ TI7
def test_ti7_determinism(tmp_path):
    w = rb_world(tmp_path, ("sub_collector", "local_access"))
    for j in corner_world(tmp_path).recs:
        w.recs.append(dict(j, handle="C" + j["handle"]))
    w.net["junctions"] = corner_world(tmp_path).net["junctions"]
    w.net["roads"].update(corner_world(tmp_path).net["roads"])
    w.cls.update(corner_world(tmp_path).cls)
    layout(tmp_path, w, crit=criteria(tmp_path), out="a.json")
    layout(tmp_path, w, crit=criteria(tmp_path), out="b.json")
    a, b = (tmp_path / "a.json").read_bytes(), (tmp_path / "b.json").read_bytes()
    assert a == b and load(tmp_path / "a.json")["counts"]["checks"] > 5


def test_ti4_entry_width_advisory(tmp_path):
    """rules_v7 (owner, 18 Oct 2026 14:23): ROD-040 advisory -> its checks are Observation; other rules unchanged."""
    d = layout(tmp_path, rb_world(tmp_path, entry=6.5), rules=rules_file(tmp_path, advisory=("ROD-040",)))
    ew = checks(d, "ROD-040")
    assert ew and all(c["severity_override"] == "Observation" for c in ew)
    assert all("severity_override" not in c for c in checks(d, "ROD-042", "length_min"))
