"""Real-data checks of the SPC-0006 Rev H build (phase C1, TH7 on NMAR 30 %). Real drawings stay out of Git (brief
§10.2): the test runs only when LDIP_REVH_DATA names a local folder holding the skill 1 / 2 ingestion files of the
NMAR 30 % set (setting-out sheets 60000-001 ... 003, hierarchy plan 00003 with line work and PDF paths, typical
sections 30000, design report) and `rules_RDE_c1.json` (get_rules.py output of a probe copy of rules_v5 with ROD-019,
020, 035, 037 active and ROD-036 advisory — DH-5; test copy only). Expected values measured in build-027
(17 Oct 2026), ezdwg 0.16.0."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DATA = os.environ.get("LDIP_REVH_DATA")
pytestmark = pytest.mark.skipif(not DATA, reason="LDIP_REVH_DATA not set (real drawings stay out of Git)")
REPO = Path(__file__).resolve().parents[1]
SK = REPO / "plugin/ldip-design-review/skills/reviewing-road-design/scripts"
PY = sys.executable
D = Path(DATA or ".")
N = "LDIP-RCRC-SAP-03-DN-ROD-DWG"


def run(script, *args):
    p = subprocess.run([PY, str(SK / script), *map(str, args)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def geo(tmp_path_factory):
    o = tmp_path_factory.mktemp("revh")
    rules = D / "rules_RDE_c1.json"
    H = D / f"{N}-00003-001-00"
    run("read_criteria.py", "--ingestion", D / "LDIP-RCRC-SAP-03-DN-DES-RPT-00001.pdf.json", "--out", o / "criteria.json")
    run("check_sections.py", "--sections", D / f"{N}-30000-001-00.dwg.json", "--rules", rules, "--out", o / "sc.json")
    run("read_setout.py", "--ingestion", *[D / f"{N}-60000-00{i}-00.dwg.json" for i in (1, 2, 3)], "--out", o / "setout.json")
    run("build_network.py", "--setout", o / "setout.json", "--site", "NMAR", "--linework", f"{H}.dwg.json", "--out", o / "network.json")
    run("cross_check.py", "--network", o / "network.json", "--site", "NMAR", "--linework", f"{H}.dwg.json", "--out", o / "xc.json")
    run("read_class.py", "--network", o / "network.json", "--site", "NMAR", "--linework", f"{H}.dwg.json", "--pdf", f"{H}.pdf.json",
        "--criteria", o / "criteria.json", "--sections", o / "sc.json", "--out", o / "class.json")
    run("check_geometry.py", "--setout", o / "setout.json", "--network", o / "network.json", "--class", o / "class.json",
        "--rules", rules, "--stage", "30% Concept Design", "--criteria", o / "criteria.json", "--cross-check", o / "xc.json",
        "--out", o / "geo.json")
    return load(o / "geo.json"), load(o / "network.json")


def fails(d, rid, cls=None):
    return [c for c in d["checks"] if c["rule_id"] == rid and c["result"] == "fail" and (cls is None or c["class"] == cls)]


def test_th7_curves_arcs_spirals(geo):
    d, _ = geo
    assert len(fails(d, "ROD-018")) == 12 and len(fails(d, "ROD-017")) == 4
    assert sorted(c["observed"] for c in fails(d, "ROD-017")) == [40.0, 50.0, 60.0, 110.0]
    assert len(fails(d, "ROD-019", "local_access")) == 5 and len(fails(d, "ROD-019", "sub_collector")) == 3
    f20 = fails(d, "ROD-020")
    assert len(f20) == 7 and sorted({c["speed_kmh"] for c in f20}) == [40.0, 50.0]
    assert d["counts"]["tangents"] == 67 and d["counts"]["reverse_tangents"] == 3


def test_th7_junctions(geo):
    d, n = geo
    f35 = fails(d, "ROD-035")
    assert [c["subject"].split()[0] for c in f35] == ["J047"] and f35[0]["observed"] == 61.2
    assert sum(1 for c in d["checks"] if c["rule_id"] == "ROD-035") == 51
    loc = [c for c in d["checks"] if c["rule_id"] == "ROD-036" and c["class"] == "local_access"]
    col = [c for c in d["checks"] if c["rule_id"] == "ROD-036" and c["class"] == "sub_collector"]
    assert (len(loc), sum(c["result"] == "fail" for c in loc)) == (50, 44)
    assert (len(col), sum(c["result"] == "fail" for c in col)) == (16, 15)
    assert n["not_positioned"] == [] and sum(1 for r in n["roads"].values() if not r["continuous"]) == 12
