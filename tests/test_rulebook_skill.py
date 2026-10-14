"""Tests for skill 8 applying-ldip-rulebook (SPC-0006 §4.5.5). Synthetic rulebooks only.
T1 on the real rules_v0.json runs only when LDIP_REAL_RULEBOOK points to a local copy (never in Git)."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/applying-ldip-rulebook"
PY = sys.executable


def rule(rid, state, params=None, code_lists=None, agent=None, default=None, reason="Synthetic reason text."):
    grp = rid.split("-")[0]
    return {"rule_id": rid, "title": f"Synthetic {rid}", "group": grp, "agent": agent or ("INT" if grp == "PKG" else "GIS"),
            "skill": "controlling-submittal-package" if grp == "PKG" else "checking-cadastral-geometry",
            "owner_role": "SBM" if grp == "PKG" else "Lead GIS", "statement": "X shall be Y.", "inputs": ["dlus"],
            "method": "compare", "parameters": params or {}, "code_lists": code_lists or [],
            "severity_default": "Minor", "issue_type": "inconsistency",
            "default_status": default or {"xlsx_cell": "Confirmed", "pdf_ocr": "Probable"},
            "state": state, "state_reason": reason, "tag": "AGREED"}


def book(status="approved"):
    return {
        "rulebook_schema_version": "1.0", "rulebook_version": "v9", "spec": "AICC-BIM-SPC-0004", "status": status,
        "issued": "2026-09-30", "approved_by": "synthetic",
        "sources": [{"source_id": "SRC-01", "title": "Approved source", "status": "approved"},
                    {"source_id": "SRC-02", "title": "Unknown source", "status": "unknown"}],
        "code_lists": {"section": {"source_id": "SRC-01", "clause": "§1", "values": ["AA", "BB"], "tag": "AGREED"},
                       "draft_list": {"source_id": "SRC-02", "clause": "§2", "values": ["ZZ"], "tag": "PROPOSED"}},
        "rules": [
            rule("PKG-001", "active", {"n": {"value": 10, "tag": "AGREED", "source_id": "SRC-01", "clause": "§1", "blocking": False}},
                 code_lists=["section"]),
            rule("GIS-001", "active", {"soft": {"value": None, "tag": "GAP", "blocking": False}}),
            rule("GIS-008", "advisory", {"buf": {"value": 25, "tag": "PROPOSED", "source_id": "SRC-02", "clause": "§2", "blocking": False}}),
            rule("GIS-004", "inactive", {"map": {"value": None, "tag": "GAP", "blocking": True}}, reason="Needs the layer mapping."),
        ],
    }


def write(tmp_path, obj, name="rules.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return p


def run(script, *args):
    p = subprocess.run([PY, str(SK / "scripts" / script), *map(str, args)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_load_valid(tmp_path):
    rc, out = run("load_rules.py", write(tmp_path, book()))
    assert rc == 0 and out["states"] == {"active": 2, "advisory": 1, "inactive": 1}


def test_t4_draft_refused(tmp_path):
    p = write(tmp_path, book("draft"))
    assert run("load_rules.py", p)[0] == 5
    assert run("get_rules.py", "--rulebook", p, "--agent", "GIS")[0] == 5
    assert run("load_rules.py", p, "--allow-draft")[0] == 0


def m_blocking_gap_active(b):
    b["rules"][3]["state"] = "active"


def m_proposed_in_active(b):
    b["rules"][0]["parameters"]["n"]["tag"] = "PROPOSED"


def m_unapproved_codelist_active(b):
    b["rules"][0]["code_lists"] = ["draft_list"]


def m_nongap_without_value(b):
    b["rules"][0]["parameters"]["n"]["value"] = None


def m_colour_confirmed(b):
    b["rules"][0]["default_status"]["pdf_colour"] = "Confirmed"


def m_duplicate_id(b):
    b["rules"].append(copy.deepcopy(b["rules"][0]))


def m_bad_id_pattern(b):
    b["rules"][0]["rule_id"] = "PKG-1"


def m_gap_with_value(b):
    b["rules"][1]["parameters"]["soft"]["value"] = 3


def m_group_mismatch(b):
    b["rules"][0]["rule_id"] = "GIS-099"


def m_unknown_source(b):
    b["rules"][0]["parameters"]["n"]["source_id"] = "SRC-77"


def m_unknown_code_list(b):
    b["rules"][0]["code_lists"] = ["nope"]


MUTATIONS = [  # (mutation, expected fragment) — first eight = SPC-0004 §8.3
    (m_blocking_gap_active, "L1 GIS-004"), (m_proposed_in_active, "L2 PKG-001.n: PROPOSED"),
    (m_unapproved_codelist_active, "code list draft_list not approved"), (m_nongap_without_value, "schema"),
    (m_colour_confirmed, "schema"), (m_duplicate_id, "duplicate rule_id"), (m_bad_id_pattern, "schema"),
    (m_gap_with_value, "schema"), (m_group_mismatch, "does not match group"), (m_unknown_source, "unknown source SRC-77"),
    (m_unknown_code_list, "unknown code list nope"),
]


@pytest.mark.parametrize("mut,frag", MUTATIONS, ids=[m.__name__ for m, _ in MUTATIONS])
def test_t2_mutations_refused_for_intended_reason(tmp_path, mut, frag):
    b = book()
    mut(b)
    rc, out = run("load_rules.py", write(tmp_path, b))
    assert rc == 5 and any(frag in e for e in out["errors"]), out["errors"]


def test_get_rules_caps_and_skips(tmp_path):
    p = write(tmp_path, book())
    rc, out = run("get_rules.py", "--rulebook", p, "--agent", "GIS")
    assert rc == 0
    caps = {r["rule_id"]: r["cap"] for r in out["rules"]}
    assert caps == {"GIS-001": "Confirmed", "GIS-008": "Probable"}
    assert out["rules_skipped"] == [{"rule_id": "GIS-004", "reason": "inactive: Needs the layer mapping."}]
    assert [r for r in out["rules"] if r["rule_id"] == "GIS-001"][0]["skip_subchecks_for"] == ["soft"]
    rc, out = run("get_rules.py", "--rulebook", p, "--agent", "GIS", "--states", "active")
    assert [r["rule_id"] for r in out["rules"]] == ["GIS-001"]
    assert run("get_rules.py", "--rulebook", p, "--agent", "GIS", "--states", "inactive")[0] == 2
    o = tmp_path / "evidence" / "int" / "out.json"          # parent folder created by the script
    rc, res = run("get_rules.py", "--rulebook", p, "--agent", "INT", "--out", o)
    assert rc == 0 and json.loads(o.read_text())["rules"][0]["code_lists"] == {"section": ["AA", "BB"]}


def test_lookup(tmp_path):
    p = write(tmp_path, book())
    assert run("lookup_code.py", "--rulebook", p, "--list", "section", "--value", "AA")[1]["found"] is True
    assert run("lookup_code.py", "--rulebook", p, "--list", "section", "--value", "QQ")[1]["found"] is False
    assert run("lookup_code.py", "--rulebook", p, "--param", "PKG-001.n")[1]["value"] == 10
    assert run("lookup_code.py", "--rulebook", p, "--list", "nope", "--value", "AA")[0] == 2
    assert run("lookup_code.py", "--rulebook", p, "--param", "PKG-999.n")[0] == 2


def finding(rule_id, status, stage="candidate"):
    f = {"stage": stage, "reference": {"rule_id": rule_id, "rulebook_version": "v9"}}
    if stage == "candidate":
        f["proposed_status"] = status
    else:
        f["verification"] = {"status": status}
    return f


@pytest.mark.parametrize("fs,ok", [
    ([finding("GIS-001", "Confirmed")], True),
    ([finding("GIS-008", "Probable")], True),
    ([finding("GIS-008", "Confirmed")], False),                        # T3
    ([finding("GIS-008", "Missing information", "verified")], False),
    ([finding("GIS-004", "Probable")], False),
    ([finding("GIS-555", "Probable")], False),
    ([finding(None, "Confirmed", "verified")], False),
    ([dict(finding("GIS-001", "Confirmed"), reference={"rule_id": "GIS-001", "rulebook_version": "v1"})], False),
])
def test_check_cap(tmp_path, fs, ok):
    rc, out = run("check_cap.py", "--findings", write(tmp_path, fs, "f.json"), "--rulebook", write(tmp_path, book()))
    assert rc == 0 and (out["result"] == "pass") == ok


@pytest.mark.skipif(not os.environ.get("LDIP_REAL_RULEBOOK"), reason="real rulebook is local-only (brief §10.2)")
def test_t1_real_rules_v0():
    rc, out = run("load_rules.py", os.environ["LDIP_REAL_RULEBOOK"])
    assert rc == 0 and out["states"] == {"active": 15, "advisory": 5, "inactive": 9}


def test_skill_frontmatter_best_practice():
    import yaml
    text = (SK / "SKILL.md").read_text()
    fm = yaml.safe_load(text.split("---")[1])
    assert fm["name"] == "applying-ldip-rulebook" and len(fm["name"]) <= 64
    assert fm["description"].startswith("LDIP design review (AICC JV):") and len(fm["description"]) <= 1024
    assert "claude" not in fm["name"] and "anthropic" not in fm["name"]
    assert len(text.splitlines()) < 500


# ---------------------------------------------------------------- schema 1.1 (SPC-0004 Rev E; skill 8 0.2.0, I11)
def p11(value, **kw):
    d = {"value": value, "tag": "AGREED", "source_id": "SRC-01", "clause": "§7 p.1", "blocking": False}
    d.update(kw)
    return d


def road_rule(rid, state="active", params=None, **kw):
    r = rule(rid, state, params or {"g_max": p11(6.0, unit="%", tolerance_ref="grade_pct")})
    r.update({"agent": "COO" if rid.startswith("COR") else "RDE",
              "skill": "coordinating-utility-corridors" if rid.startswith("COR") else "reviewing-road-design",
              "owner_role": "IM / CM" if rid.startswith("COR") else "DMR", "inputs": ["landxml_profile"],
              "issue_type": "design_error", "check_type": "value",
              "required_inputs": [{"item": "C4 alignment data", "carrier": ["landxml_profile"], "required_from_phase": "DD"}]})
    r.update(kw)
    return r


def book11(status="approved"):
    b = book(status)
    b["rulebook_schema_version"] = "1.1"
    b["sources"][0]["tier"] = "1"
    b["sources"].append({"source_id": "SRC-03", "title": "Tier-2 code", "status": "approved", "tier": "2"})
    b["tolerances"] = {"grade_pct": p11(0.05, unit="%"), "width_m": p11(0.05, unit="m")}
    b["stage_map"] = {"30% Concept Design": {"phase": "PD", "milestone": None},
                      "60%": {"phase": "DD", "milestone": 60}, "IFC": {"phase": "FD", "milestone": None}}
    b["rules"] += [
        road_rule("ROD-001", params={"row_min": p11(16.0, unit="m", tolerance_ref="width_m"),
                                     "row_max": p11(20.0, unit="m", enforcement="advisory")},
                  applicability={"street_class": ["local_access"]}),
        road_rule("ROD-002", params={"k": p11({"30": 2, "40": 4}, keyed_by=["design_speed_kmh"])},
                  applicability={"street_class": ["all"], "design_speed_kmh": [30, 40]}),
        road_rule("ROD-101", "inactive", params={"required_element": p11("sidewalks both sides")},
                  check_type="presence", issue_type="missing_information", required_from_phase="PD",
                  state_reason="skill not built — reviewing-road-design (10)", target_state="active"),
        road_rule("COR-001", "advisory", params={"offset": p11(1.5, unit="m", source_id="SRC-03")}),
    ]
    return b


def test_v11_loads_and_v10_still_loads(tmp_path):
    rc, out = run("load_rules.py", write(tmp_path, book11()))
    assert rc == 0 and out["states"] == {"active": 4, "advisory": 2, "inactive": 2}, out
    assert run("load_rules.py", write(tmp_path, book(), "v10.json"))[0] == 0


def test_v11_get_rules_param_caps_tolerance_stage_map(tmp_path):
    p = write(tmp_path, book11())
    rc, out = run("get_rules.py", "--rulebook", p, "--agent", "RDE")
    assert rc == 0
    r1 = next(r for r in out["rules"] if r["rule_id"] == "ROD-001")
    assert r1["cap"] == "Confirmed" and r1["advisory_parameters"] == ["row_max"]
    assert r1["parameters"]["row_min"]["cap"] == "Confirmed" and r1["parameters"]["row_max"]["cap"] == "Probable"
    assert r1["parameters"]["row_min"]["tolerance"] == {"value": 0.05, "unit": "m", "tag": "AGREED"}
    assert r1["applicability"] == {"street_class": ["local_access"]} and r1["required_inputs"][0]["required_from_phase"] == "DD"
    assert out["stage_map"]["60%"] == {"phase": "DD", "milestone": 60}
    assert out["rules_skipped"] == [{"rule_id": "ROD-101", "reason": "inactive: skill not built — reviewing-road-design (10)",
                                     "target_state": "active"}]
    rc, out = run("get_rules.py", "--rulebook", p, "--agent", "COO")
    assert rc == 0 and [(r["rule_id"], r["cap"], r["parameters"]["offset"]["cap"]) for r in out["rules"]] == [("COR-001", "Probable", "Probable")]
    rc, res = run("lookup_code.py", "--rulebook", p, "--param", "ROD-001.row_max")
    assert rc == 0 and res["cap"] == "Probable" and res["enforcement"] == "advisory"


def r11(b, rid):
    return next(r for r in b["rules"] if r["rule_id"] == rid)


MUTATIONS_11 = [  # (mutation, expected fragment) — SPC-0004 Rev E §4.9 L5-L12
    (lambda b: r11(b, "ROD-001")["parameters"]["row_max"].update(tag="PROPOSED"), "L5 ROD-001.row_max"),
    (lambda b: r11(b, "ROD-101").pop("target_state"), "L6 ROD-101"),
    (lambda b: r11(b, "ROD-001")["parameters"]["row_min"].pop("clause"), "L7 ROD-001.row_min: no clause"),
    (lambda b: r11(b, "ROD-001")["parameters"]["row_min"].update(tolerance_ref="nope"), "L8 ROD-001.row_min: unknown tolerance"),
    (lambda b: r11(b, "ROD-002")["parameters"]["k"]["value"].pop("40"), "L9 ROD-002.k: no value for design_speed_kmh"),
    (lambda b: r11(b, "COR-001").pop("required_inputs"), "L10 COR-001"),
    (lambda b: r11(b, "ROD-101")["parameters"]["required_element"].update(source_id="SRC-03"), "L11 ROD-101"),
    (lambda b: b.pop("stage_map"), "L12"),
    (lambda b: b["stage_map"]["60%"].update(milestone=None), "L12 a DD stage_map entry has no milestone"),
    (lambda b: b["stage_map"].pop("30% Concept Design"), "L12 phase PD used"),
    (lambda b: r11(b, "ROD-001").update(agent="CRD"), "schema"),
]


@pytest.mark.parametrize("mut,frag", MUTATIONS_11, ids=[f"m11_{i}" for i in range(len(MUTATIONS_11))])
def test_v11_mutations_refused_for_intended_reason(tmp_path, mut, frag):
    b = book11()
    mut(b)
    rc, out = run("load_rules.py", write(tmp_path, b))
    assert rc == 5 and any(frag in e for e in out["errors"]), out["errors"]


def test_v11_l8_tolerance_proposed_in_active_rule(tmp_path):
    b = book11()
    b["tolerances"]["width_m"]["tag"] = "PROPOSED"
    rc, out = run("load_rules.py", write(tmp_path, b))
    assert rc == 5 and any(e.startswith("L8 ROD-001.row_min: tolerance 'width_m' is PROPOSED") for e in out["errors"])


def pfinding(rule_id, status, parameter=None):
    f = finding(rule_id, status)
    if parameter is not None:
        f["reference"]["parameter"] = parameter
    return f


@pytest.mark.parametrize("fs,ok", [
    ([pfinding("ROD-001", "Confirmed", "row_min")], True),
    ([pfinding("ROD-001", "Confirmed")], True),                      # no parameter named: rule cap applies
    ([pfinding("ROD-001", "Probable", "row_max")], True),
    ([pfinding("ROD-001", "Confirmed", "row_max")], False),          # advisory parameter -> Probable (Rev E §4.7)
    ([pfinding("ROD-001", "Missing information", "row_max")], False),
    ([pfinding("ROD-001", "Probable", "nope")], False),              # unknown parameter
    ([pfinding("ROD-101", "Missing information")], False),           # inactive
])
def test_v11_check_cap_parameter(tmp_path, fs, ok):
    rc, out = run("check_cap.py", "--findings", write(tmp_path, fs, "f.json"), "--rulebook", write(tmp_path, book11()))
    assert rc == 0 and (out["result"] == "pass") == ok, out


def test_finding_schema_road_rule_ids_and_parameter():
    from jsonschema import Draft202012Validator
    s = json.loads((REPO / "plugin/ldip-design-review/schemas/finding.schema.json").read_text())
    ref = dict(s["$defs"]["reference"], **{"$defs": s["$defs"]})
    v = Draft202012Validator(ref)
    for rid in ("PKG-001", "GIS-012", "ROD-101", "EWK-002", "COR-001"):
        assert v.is_valid({"rule_id": rid, "parameter": "row_min"}), rid
    for rid in ("CRD-001", "ROD-1", "XYZ-001"):
        assert not v.is_valid({"rule_id": rid}), rid
    agent = s["properties"]["agent"]["enum"]
    assert "COO" in agent and "RDE" in agent and "CRD" not in agent


@pytest.mark.skipif(not os.environ.get("LDIP_REAL_RULEBOOK_V2"), reason="real rulebook is local-only (brief §10.2)")
def test_real_rules_v2():
    path = os.environ["LDIP_REAL_RULEBOOK_V2"]
    rc, out = run("load_rules.py", path)
    assert rc == 0 and out["states"] == {"active": 16, "advisory": 5, "inactive": 102}
    rc, out = run("get_rules.py", "--rulebook", path, "--agent", "RDE")
    assert rc == 0 and out["rules"] == [] and len(out["rules_skipped"]) == 90
    assert all(s.get("target_state") for s in out["rules_skipped"])
    rc, out = run("get_rules.py", "--rulebook", path, "--agent", "INT")
    assert rc == 0 and any(s["rule_id"] == "PKG-012" and s["target_state"] == "active" for s in out["rules_skipped"])
