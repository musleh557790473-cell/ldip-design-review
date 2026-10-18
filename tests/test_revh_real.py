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


# ------------------------------------------------------------------ TH8 (recorded at build, 17 Oct 2026; DH-10)
QD = os.environ.get("LDIP_REVH_DATA_QRWN")
JD = os.environ.get("LDIP_REVH_DATA_NRJS")


def _geo_site(tmp, d, site, sheets, sections, plan=None, pdf=None, report=None):
    rules = D / "rules_RDE_c1.json" if DATA else Path(d) / "rules_RDE_c1.json"
    crit = []
    if report:
        run("read_criteria.py", "--ingestion", report, "--out", tmp / "criteria.json")
        crit = ["--criteria", tmp / "criteria.json"]
    run("check_sections.py", "--sections", sections, "--rules", rules, "--out", tmp / "sc.json")
    run("read_setout.py", "--ingestion", *sheets, "--out", tmp / "setout.json")
    run("build_network.py", "--setout", tmp / "setout.json", "--site", site, *(["--linework", plan] if plan else []),
        "--out", tmp / "network.json")
    run("read_class.py", "--network", tmp / "network.json", "--site", site, *(["--linework", plan] if plan else []),
        *(["--pdf", pdf] if pdf else []), *crit, "--sections", tmp / "sc.json", "--out", tmp / "class.json")
    run("check_geometry.py", "--setout", tmp / "setout.json", "--network", tmp / "network.json", "--class", tmp / "class.json",
        "--rules", rules, "--stage", "30% Concept Design", *crit, "--out", tmp / "geo.json")
    return load(tmp / "geo.json")


@pytest.mark.skipif(not JD, reason="LDIP_REVH_DATA_NRJS not set (real drawings stay out of Git)")
def test_th8_nrjs(tmp_path):
    d = Path(JD)
    n = "LDIP-RCRC-SAP-01-NRJS-IRO-DWG-00"
    g = _geo_site(tmp_path, d, "NRJS", [d / f"{n}-60001-0A.dwg.json"], d / f"{n}-30001-0A.dwg.json",
                  d / f"{n}-15000-0A.dwg.json", d / f"{n}-15000-0A.pdf.json")
    assert [c["observed"] for c in fails(g, "ROD-018")] == [10.2, 10.2] and len(fails(g, "ROD-019")) == 2
    assert not fails(g, "ROD-035") and sum(1 for c in g["checks"] if c["rule_id"] == "ROD-035") == 7
    assert sorted(c["observed"] for c in fails(g, "ROD-036")) == [68.0, 68.0, 68.0, 89.4, 114.3, 114.4]


@pytest.mark.skipif(not QD, reason="LDIP_REVH_DATA_QRWN not set (real drawings stay out of Git)")
def test_th8_qrwn(tmp_path):
    """QRWN rev 0A: setting-out 60000-001 / 002; plan 00003 read by the reader chain (ezdwg fails, LibreDWG converts —
    scratch test only, L-1 open) with line work; plan PDF paths; design report AQ-DES-RPT-00001-001 (owner, 17 Oct)."""
    d = Path(QD)
    n = "LDIP-RCRC-SAP-01-AQ-ROD-DWG"
    g = _geo_site(tmp_path, d, "QRWN", [d / f"{n}-60000-00{i}-0A.dwg.json" for i in (1, 2)], d / f"{n}-30000-001-0A.dwg.json",
                  d / f"{n}-00003-001-0A.dwg.json", d / f"{n}-00003-001-0A.pdf.json", d / "LDIP-RCRC-SAP-01-AQ-DES-RPT-00001-001.pdf.json")
    net = load(tmp_path / "network.json")["counts"]
    assert (net["junctions"], net["roundabouts"], net["roundabout_approaches"]) == (329, 7, 26)
    cls = load(tmp_path / "class.json")["summary"]
    # K25 (SPC-0006 Rev I §4.13F.6, build 18 Oct 2026): the QRWN typical sections are now read, so the 20 m local
    # section maps the E roads (M6): 210 -> 219 local, 9 -> 0 unknown; ROD-036 local 307 / 358 -> 325 / 382
    assert cls["classes"] == {"local_access": 219, "sub_collector": 21} and cls["class_unknown"] == 0
    assert len(fails(g, "ROD-018")) == 73 and min(c["observed"] for c in fails(g, "ROD-018")) == 13.0
    assert not fails(g, "ROD-017") and sum(1 for c in g["checks"] if c["rule_id"] == "ROD-017") == 17
    assert (len(fails(g, "ROD-019", "local_access")), len(fails(g, "ROD-019", "sub_collector"))) == (20, 4)
    f35 = sorted(c["observed"] for c in fails(g, "ROD-035"))
    assert len(f35) == 16 and f35[0] == 42.2
    assert (len(fails(g, "ROD-036", "local_access")), len(fails(g, "ROD-036", "sub_collector"))) == (325, 64)
    assert g["counts"]["tangents"] == 335 and g["counts"]["reverse_tangents"] == 12
