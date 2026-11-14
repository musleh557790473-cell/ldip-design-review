"""Skill 8 with rulebook schema 1.2 (SPC-0004 Rev M, rules_v10: WUT). Synthetic rulebooks only.
The real rules_v10 check runs only when LDIP_REAL_RULEBOOK_V10 points to a local copy (never in Git)."""
import copy
import os

import pytest

from test_rulebook_skill import book11, p11, road_rule, run, write


def wut_rule(rid, state="advisory", params=None, **kw):
    params = params or {"min_cover": p11(1.2, unit="m", source_id="SRC-04", tolerance_ref="wut_level_m",
                                         enforcement="advisory",
                                         alternatives=[{"value": 1.0, "source_id": "SRC-01",
                                                        "clause": "ER §7.5 p.31", "note": None}])}
    r = road_rule(rid, state, params=params)
    r.update({"agent": "WUT", "skill": "reviewing-wet-utilities", "owner_role": "DMR",
              "inputs": ["hydraulic_model", "long_section"],
              "required_inputs": [{"item": "Long sections", "carrier": ["long_section"], "required_from_phase": "DD"}]})
    if state != "active":
        r["state_reason"] = "Advisory until the Golden Set run (WT-5)."
    r.update(kw)
    return r


def book12(status="approved"):
    b = book11(status)
    b["rulebook_schema_version"] = "1.2"
    b["sources"].append({"source_id": "SRC-04", "title": "CMP Final Report", "status": "approved", "tier": "1",
                         "precedence_tier": "1"})
    b["tolerances"]["wut_level_m"] = p11(0.01, unit="m")
    b["rules"] += [
        wut_rule("WUT-001"),
        wut_rule("WUT-002", params={"pipe_dia": p11({"150": 0.6, "300": 0.9}, unit="m/s", keyed_by=["diameter_mm"],
                                                    enforcement="advisory", source_id="SRC-04")}),
        wut_rule("WUT-101", "inactive", check_type="presence", issue_type="missing_information",
                 state_reason="skill not built — WUT presence checks", target_state="active"),
    ]
    return b


def test_v12_loads_and_v11_still_loads(tmp_path):
    rc, out = run("load_rules.py", write(tmp_path, book12()))
    assert rc == 0 and out["states"] == {"active": 4, "advisory": 4, "inactive": 3}, out
    assert run("load_rules.py", write(tmp_path, book11(), "v11.json"))[0] == 0


def test_v12_get_rules_wut(tmp_path):
    rc, out = run("get_rules.py", "--rulebook", write(tmp_path, book12()), "--agent", "WUT")
    assert rc == 0
    assert [(r["rule_id"], r["cap"]) for r in out["rules"]] == [("WUT-001", "Probable"), ("WUT-002", "Probable")]
    p = out["rules"][0]["parameters"]["min_cover"]
    assert p["alternatives"][0]["clause"] == "ER §7.5 p.31" and p["tolerance"]["value"] == 0.01 and p["cap"] == "Probable"
    assert out["rules_skipped"][0]["rule_id"] == "WUT-101"


def test_v12_lint_l7_l10_apply_to_wut(tmp_path):
    b = book12()
    del b["rules"][-3]["parameters"]["min_cover"]["clause"]
    rc, out = run("load_rules.py", write(tmp_path, b))
    assert rc == 5 and any(e.startswith("L7 WUT-001") for e in out["errors"]), out
    b = book12()
    b["rules"][-2]["required_inputs"] = []
    rc, out = run("load_rules.py", write(tmp_path, b))
    assert rc == 5 and any(e.startswith("L10 WUT-002") for e in out["errors"]), out


@pytest.mark.parametrize("mutate", [
    lambda b: b["sources"][-1].__setitem__("precedence_tier", "6"),
    lambda b: b["rules"][-3]["parameters"]["min_cover"]["alternatives"][0].pop("clause"),
    lambda b: b["rules"][-3].__setitem__("rule_id", "WET-001"),
    lambda b: b.__setitem__("rulebook_schema_version", "1.3"),
])
def test_v12_schema_refusals(tmp_path, mutate):
    b = copy.deepcopy(book12())
    mutate(b)
    assert run("load_rules.py", write(tmp_path, b))[0] == 5


def test_wut_content_not_gated_by_version_label(tmp_path):
    # a 1.1 file may not carry WUT content either: the schema has one rule-id pattern for all versions,
    # so this documents that WUT ids are accepted by structure; the version enum is the release gate.
    b = book12()
    b["rulebook_schema_version"] = "1.1"
    assert run("load_rules.py", write(tmp_path, b))[0] == 0


@pytest.mark.skipif(not os.environ.get("LDIP_REAL_RULEBOOK_V10"), reason="real rulebook is local-only (brief §10.2)")
def test_real_rules_v10():
    path = os.environ["LDIP_REAL_RULEBOOK_V10"]
    rc, out = run("load_rules.py", path)
    assert rc == 0 and out["states"] == {"active": 48, "advisory": 68, "inactive": 64}, out
    rc, out = run("get_rules.py", "--rulebook", path, "--agent", "WUT")
    assert rc == 0 and len(out["rules"]) == 57 and {r["cap"] for r in out["rules"]} == {"Probable"}
