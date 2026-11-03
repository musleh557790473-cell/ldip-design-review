"""Skill 11 0.1.1 — synthetic T11-1…T11-6 + DQ-7a / W3 crown / W8 (SPC-0006 Rev Q §4.19).

No real project data. Synthetic fixture uses the real OpenFlows table/column names
(HMIModelingElement / Conduit / …), not invented Elements/Physical (R1).
"""
from __future__ import annotations

import json
import math
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK = PLUGIN / "skills/reviewing-wet-utilities/scripts"
REF = PLUGIN / "skills/reviewing-wet-utilities/references"
TPL = PLUGIN / "skills/reviewing-wet-utilities/templates"
PY = sys.executable
FT = 0.3048
FT_MM = 304.8


def make_linestring_wkb(points_ft, *, little=True, with_z=False):
    """Build OGC WKB LineString (US-foot coordinates) for synthetic HMIRealGeometry."""
    import struct
    endian = 1 if little else 0
    fmt = "<" if little else ">"
    gtype = 2 + (1000 if with_z else 0)
    parts = [
        struct.pack(f"{fmt}B", endian),
        struct.pack(f"{fmt}I", gtype),
        struct.pack(f"{fmt}I", len(points_ft)),
    ]
    for p in points_ft:
        if with_z:
            x, y, z = p[0], p[1], p[2] if len(p) > 2 else 0.0
            parts.append(struct.pack(f"{fmt}3d", x, y, z))
        else:
            parts.append(struct.pack(f"{fmt}2d", p[0], p[1]))
    return b"".join(parts)


# NMAR Co-17 start in US feet (re-review 5 X2)
CO17_START_FT = (2157576.65, 8905251.05)
CO17_END_FT = (2157601.58, 8905251.05)  # ~7.60 m east


def run(script, *args):
    p = subprocess.run([PY, str(SK / script), *map(str, args)], capture_output=True, text=True)
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "{}"
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        msg = {"raw": p.stdout, "stderr": p.stderr}
    return p.returncode, msg


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    return path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def make_sqlite(path, *, alts=((1, "Base"), (2, "Alt")), conduits=None, nodes=None,
                network="sewer"):
    """Synthetic OpenFlows schema in US feet (DQ-3 / R1 real table names)."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE Alternatives (AlternativeID INTEGER PRIMARY KEY, Label TEXT);
        CREATE TABLE HMIModelingElement (
          ElementID INTEGER PRIMARY KEY, Label TEXT, IsDeleted INTEGER DEFAULT 0
        );
        CREATE TABLE Conduit (DomainElementID INTEGER PRIMARY KEY);
        CREATE TABLE Conduit_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          ConduitDiameter REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE PhysicalLink_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_UpstreamInvert REAL, Physical_DownstreamInvert REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseLink_HMIDataSetTopology_Data (
          DomainElementID INTEGER PRIMARY KEY,
          HMITopologyStartNodeID INTEGER, HMITopologyStopNodeID INTEGER
        );
        CREATE TABLE BaseLink_HmiDataSetGeometry_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          HMIGeometryScaledLength REAL,
          HMIRealGeometry BLOB,
          HMIGeometry BLOB,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseNode_HmiDataSetGeometry_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          HMIRealGeometry BLOB,
          HMIGeometry BLOB,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE GravityLinkBase_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_Material TEXT, Physical_ManningsN REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseNode_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_GroundElevation REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseDirectedNode_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_GroundElevation REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE IdahoPipe (DomainElementID INTEGER PRIMARY KEY);
        CREATE TABLE IdahoPipe_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_PipeMaterial TEXT, Physical_PipeDiameter REAL,
          Physical_HazenWilliamsC REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseIdahoNode_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_NodeElevation REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        """
    )
    for aid, lab in alts:
        c.execute("INSERT INTO Alternatives VALUES (?, ?)", (aid, lab))

    # Default sewer nodes (ground in metres → store as feet)
    nodes = nodes or [
        (101, "MH-1", 1400.0),
        (102, "MH-2", 1398.0),
        (103, "OF-1", 1395.0),
    ]
    for eid, lab, ground_m in nodes:
        c.execute(
            "INSERT INTO HMIModelingElement VALUES (?, ?, 0)", (eid, lab),
        )
        c.execute(
            "INSERT INTO BaseNode_Physical_Data VALUES (?, ?, ?)",
            (eid, 1, ground_m / FT),
        )

    if network in ("water", "irrigation"):
        # Pressure pipes: IdahoPipe family
        conduits = conduits or [
            # eid, start, stop, D_mm, L_m, alt, material, C
            (1, 101, 102, 160.0, 50.0, 1, "DI", 140.0),
            (1, 101, 102, 200.0, 50.0, 2, "DI", 140.0),
            (2, 102, 103, 110.0, 40.0, 1, "PE", 140.0),
            (2, 102, 103, 110.0, 40.0, 2, "PE", 140.0),
        ]
        for eid, s, e, d_mm, L_m, alt, mat, hw in conduits:
            c.execute("INSERT OR IGNORE INTO IdahoPipe VALUES (?)", (eid,))
            c.execute(
                "INSERT OR IGNORE INTO HMIModelingElement VALUES (?, ?, 0)",
                (eid, f"P-{eid}"),
            )
            c.execute(
                "INSERT OR IGNORE INTO BaseLink_HMIDataSetTopology_Data VALUES (?, ?, ?)",
                (eid, s, e),
            )
            c.execute(
                "INSERT INTO IdahoPipe_Physical_Data VALUES (?, ?, ?, ?, ?)",
                (eid, alt, mat, d_mm / FT_MM, hw),
            )
            # Default geometry along +E for pressure pipes
            wkb = make_linestring_wkb([
                (1000.0 + eid * 10, 2000.0),
                (1000.0 + eid * 10 + L_m / FT, 2000.0),
            ])
            c.execute(
                "INSERT INTO BaseLink_HmiDataSetGeometry_Data VALUES (?, ?, ?, ?, ?)",
                (eid, alt, L_m / FT, wkb, None),
            )
            c.execute(
                "INSERT OR IGNORE INTO BaseIdahoNode_Physical_Data VALUES (?, ?, ?)",
                (s, 1, 1390.0 / FT),
            )
    else:
        # Default Co-17 validation values (R1 note) plus a second conduit
        # Co-17: D 200 mm, L 7.60 m, inverts 759.413 / 759.365
        # X2: Co-17 start E 657629.4 / N 2714320.5 m (= CO17_START_FT)
        conduits = conduits or [
            # eid, label, start, stop, D_mm, L_m, inv_s_m, inv_e_m, alt
            (1, "Co-17", 101, 102, 200.0, 7.60, 759.413, 759.365, 1),
            (1, "Co-17", 101, 102, 250.0, 7.60, 759.413, 759.365, 2),  # alt 2
            (2, "C-2", 102, 103, 200.0, 40.0, 759.365, 759.165, 1),
            (2, "C-2", 102, 103, 200.0, 40.0, 759.365, 759.165, 2),
        ]
        for eid, lab, s, e, d_mm, L_m, i0, i1, alt in conduits:
            c.execute("INSERT OR IGNORE INTO Conduit VALUES (?)", (eid,))
            c.execute(
                "INSERT OR IGNORE INTO HMIModelingElement VALUES (?, ?, 0)",
                (eid, lab),
            )
            c.execute(
                "INSERT OR IGNORE INTO BaseLink_HMIDataSetTopology_Data VALUES (?, ?, ?)",
                (eid, s, e),
            )
            c.execute(
                "INSERT INTO Conduit_Physical_Data VALUES (?, ?, ?)",
                (eid, alt, d_mm / FT_MM),
            )
            c.execute(
                "INSERT INTO PhysicalLink_Physical_Data VALUES (?, ?, ?, ?)",
                (eid, alt, i0 / FT, i1 / FT),
            )
            if lab == "Co-17":
                wkb = make_linestring_wkb([CO17_START_FT, CO17_END_FT], little=True)
            else:
                # C-2 continues east of Co-17 end; also exercise big-endian Z WKB
                start = CO17_END_FT
                end = (CO17_END_FT[0] + L_m / FT, CO17_END_FT[1])
                wkb = make_linestring_wkb(
                    [(start[0], start[1], 0.0), (end[0], end[1], 0.0)],
                    little=False, with_z=True,
                )
            c.execute(
                "INSERT INTO BaseLink_HmiDataSetGeometry_Data VALUES (?, ?, ?, ?, ?)",
                (eid, alt, L_m / FT, wkb, None),
            )
            c.execute(
                "INSERT INTO GravityLinkBase_Physical_Data VALUES (?, ?, ?, ?)",
                (eid, alt, "PVC", 0.013),
            )
    conn.commit()
    conn.close()
    return path


def make_dxf(path, texts, lines=None, lwpolylines=None):
    """Minimal DXF with MTEXT labels on real wet layers (R3 / R4).

    ``lwpolylines``: list of (layer, [(x,y[,bulge]), ...], closed).
    """
    import ezdxf
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for layer, text, x, y in texts:
        if layer not in doc.layers:
            doc.layers.add(layer)
        msp.add_mtext(text, dxfattribs={"layer": layer, "insert": (x, y), "char_height": 0.25})
    for layer, x0, y0, x1, y1 in (lines or []):
        if layer not in doc.layers:
            doc.layers.add(layer)
        msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": layer})
    for layer, pts, closed in (lwpolylines or []):
        if layer not in doc.layers:
            doc.layers.add(layer)
        format_ = "xyb" if any(len(p) > 2 for p in pts) else "xy"
        pl = msp.add_lwpolyline(pts, format=format_, dxfattribs={"layer": layer})
        pl.closed = bool(closed)
    path = Path(path)
    doc.saveas(path)
    return path


def check(tmp_path, networks, criteria=None, plan=None, presence=None, **kw):
    net_path = write(tmp_path / "networks.json", networks)
    args = [
        "--networks", net_path,
        "--out-consistency", tmp_path / "consistency.json",
        "--out-candidates", tmp_path / "candidates.json",
        "--site", kw.get("site", "NMAR"),
        "--sub", kw.get("sub", "SYN"),
        "--stage", "30% Concept Design",
    ]
    if criteria is not None:
        args += ["--criteria", write(tmp_path / "criteria.json", criteria)]
    if plan is not None:
        args += ["--plan", write(tmp_path / "plan.json", plan)]
    if presence is not None:
        args += ["--presence", write(tmp_path / "presence.json", presence)]
    rc, msg = run("check_wet.py", *args)
    assert rc == 0, msg
    cons = load(tmp_path / "consistency.json")
    cand = load(tmp_path / "candidates.json")
    Draft202012Validator(load(TPL / "consistency.schema.json")).validate(cons)
    Draft202012Validator(load(TPL / "candidates.schema.json")).validate(cand)
    return cons, cand, msg


# --------------------------------------------------------------------------- T11-1 units
def test_t11_1_reader_units_feet_to_si(tmp_path):
    """T11-1: US feet storage → length m, diameter mm; source sha256 (no unit guessing)."""
    db = make_sqlite(tmp_path / "syn.stsw.sqlite")
    out = tmp_path / "networks.json"
    rc, msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    Draft202012Validator(load(TPL / "networks.schema.json")).validate(doc)
    assert doc["units"]["length"] == "m" and doc["units"]["diameter"] == "mm"
    assert doc["units"]["source_storage"] == "US_feet"
    assert doc["source"]["sha256"] and len(doc["source"]["sha256"]) == 64
    assert doc["source"]["schema"] == "openflows_conduit"
    assert doc["summary"]["conduit_count"] == 2  # base alt only
    # Co-17 expected SI values
    by_id = {c["id"]: c for c in doc["conduits"]}
    c0 = by_id["Co-17"]
    assert abs(c0["diameter_mm"] - 200) < 0.5
    assert abs(c0["length_m"] - 7.60) < 0.02
    assert abs(c0["invert_start_m"] - 759.413) < 0.02
    assert abs(c0["invert_stop_m"] - 759.365) < 0.02
    assert c0["units_source"] == "US_feet"
    # X2: HMIRealGeometry WKB (ft → m); Co-17 start E/N
    assert c0.get("has_geometry") is True
    assert abs(c0["e_m"] - 657629.4) < 0.1
    assert abs(c0["n_m"] - 2714320.5) < 0.1
    assert len(c0.get("vertices_m") or []) >= 2
    c2 = by_id["C-2"]
    assert c2.get("has_geometry") is True  # big-endian Z LineString
    assert sum(1 for c in doc["conduits"] if c.get("has_geometry")) == 2


# --------------------------------------------------------------------------- T11-2 base alternative
def test_t11_2_base_alternative_lowest_id(tmp_path):
    """T11-2: base alternative = lowest AlternativeID (ignores alt 2 diameters)."""
    db = make_sqlite(tmp_path / "syn.stsw.sqlite")
    out = tmp_path / "networks.json"
    rc, msg = run("read_bentley_sqlite.py", "--sqlite", db, "--out", out)
    assert rc == 0, msg
    assert msg["base_alternative_id"] == 1
    assert msg["alternatives"] == [1, 2]
    doc = load(out)
    assert doc["base_alternative_id"] == 1
    assert all(c["alternative_id"] == 1 for c in doc["conduits"])
    assert all(abs(c["diameter_mm"] - 200) < 0.5 for c in doc["conduits"])


# --------------------------------------------------------------------------- T11-3 label parse
def test_t11_3_dxf_label_parse(tmp_path):
    """T11-3: real node CL:/IL: and pipe [<D>]L=/S= labels (R4); real layers (R3)."""
    node_txt = "MH-1\nCL: 412.50m\nIL: 410.20m\nE: 1000.00\nN: 2000.00"
    pipe_txt = "200L=50.00\nS=0.50%"
    dxf = make_dxf(tmp_path / "swn.dxf", [
        ("CI-Ss_50_35_08_30-P-SS_Annotation Main Sewer", node_txt, 1000, 2000),
        ("CI-Ss_50_35_08_30-P-SS_Pipe Dia 200", pipe_txt, 1050, 2000),
        ("C-PW-FIRE HYDRANT COVER CATCH", "FH-1", 50, 50),
    ], lines=[
        ("CI-Ss_50_35_08_30-P-SS_Pipe Dia 200", 0, 0, 50, 0),
    ])
    out = tmp_path / "plan.json"
    rc, msg = run("read_network_plan.py", "--dxf", dxf, "--site", "NMAR",
                  "--network", "sewer", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    assert doc["counts"]["nodes"] >= 1
    node = doc["nodes"][0]
    assert node["id"] == "MH-1"
    assert abs(node["cl_m"] - 412.50) < 1e-6
    assert abs(node["il_m"] - 410.20) < 1e-6
    assert abs(node["e_m"] - 1000.0) < 1e-6
    labels = [p for p in doc["pipes"] if p.get("kind") == "pipe_label"]
    assert labels and abs(labels[0]["length_m"] - 50.0) < 1e-6
    assert abs(labels[0]["slope_pct"] - 0.50) < 1e-6
    assert abs(labels[0]["diameter_mm"] - 200) < 1e-6
    assert any(f["kind"] == "hydrant" for f in doc["fixtures"])
    # Diameter from layer pattern
    geom = [p for p in doc["pipes"] if p.get("kind") == "pipe_geometry"]
    assert geom and abs(geom[0]["diameter_mm"] - 200) < 1e-6
    assert geom[0]["utility"] == "sewer"


def test_t11_y1_force_main_labels(tmp_path):
    """Y1: FORCE MAIN texts → force_main_label; W2 flags Ø ≠ report 110 mm."""
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("rnp", SK / "read_network_plan.py")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    p110 = mod.parse_force_main_label("FORCE MAIN HDPE Ø110mm")
    assert p110 and abs(p110["diameter_mm"] - 110) < 1e-9
    assert p110["material"] and "HDPE" in p110["material"].upper()
    p1000 = mod.parse_force_main_label(
        "PROPOSED FORCE MAIN LINE- HDPE SDR11 PN16 Ø1000mm"
    )
    assert p1000 and abs(p1000["diameter_mm"] - 1000) < 1e-9
    assert mod.parse_force_main_label("L=10\nS=0.5%") is None

    # DXF with 8 × Ø110 + 1 × Ø1000 (site coordinates)
    texts = []
    for i in range(7):
        texts.append((
            "CI-Ss_50_35_08_30-P-SS_Annotation Main Sewer",
            "FORCE MAIN HDPE Ø110mm", 650000 + i, 2714000 + i,
        ))
    texts.append((
        "CI-Ss_50_35_08_30-P-SS_Annotation Main Sewer",
        "PROPOSED FORCE MAIN LINE- HDPE SDR11 PN16 Ø110mm",
        650010, 2714010,
    ))
    texts.append((
        "CI-Ss_50_35_08_30-P-SS_Annotation Main Sewer",
        "FORCE MAIN HDPE Ø1000mm", 650020, 2714020,
    ))
    dxf = make_dxf(tmp_path / "fm.dxf", texts)
    out = tmp_path / "plan.json"
    rc, msg = run("read_network_plan.py", "--dxf", dxf, "--site", "NMAR",
                  "--network", "sewer", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    fm = [p for p in doc["pipes"] if p.get("kind") == "force_main_label"]
    assert len(fm) == 9
    assert sum(1 for p in fm if abs(p["diameter_mm"] - 110) < 0.5) == 8
    assert sum(1 for p in fm if abs(p["diameter_mm"] - 1000) < 0.5) == 1
    assert all(p.get("e_m") is not None and p.get("n_m") is not None for p in fm)

    presence = {
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    }
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "b" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "C1", "element_id": 1, "source": "model",
            "diameter_mm": 200, "length_m": 10.0, "slope_pct": 0.5,
            "invert_start_m": 100.0, "invert_stop_m": 99.95, "cover_m": 1.5,
            "start_node_id": 1, "stop_node_id": 2,
        }],
        "nodes": [],
        "summary": {"conduit_count": 1, "length_km": 0.01},
        "plan": fm,
    }
    criteria = {
        "declared_totals": {},
        "criteria": {"sewer": {"force_main_diameter_mm": {"value": 110, "clause": "§6.3.4"}}},
    }
    cons, cand, _ = check(
        tmp_path / "y1", networks, criteria=criteria, presence=presence,
    )
    draft = [
        f for f in cand["findings"]
        if "force-main" in f["title"].casefold() or "force main" in f["title"].casefold()
    ]
    assert len(draft) == 1, [f["title"] for f in cand["findings"]]
    assert draft[0]["severity"] == "Minor"
    assert draft[0]["proposed_status"] == "Confirmed"
    assert "1000" in draft[0]["statement"]
    assert "Ø1000" in draft[0]["statement"] or "1000mm" in draft[0]["statement"].casefold()
    assert "650020" in draft[0]["statement"] or "E 650020" in draft[0]["statement"]


def test_t11_3b_pipe_label_no_diameter_prefix(tmp_path):
    """Pipe label without diameter prefix still parses L= / S=."""
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("rnp", SK / "read_network_plan.py")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = mod.parse_pipe_label("L=63.01\nS=1.50%")
    assert p and abs(p["length_m"] - 63.01) < 1e-9
    assert abs(p["slope_pct"] - 1.50) < 1e-9
    assert p["diameter_mm"] is None


def test_t11_3c_water_layer_pct_c(tmp_path):
    """Water %%c160mm layer yields diameter 160."""
    dxf = make_dxf(tmp_path / "pwt.dxf", [
        ("%%c160mm", "160L=12.00\nS=0.00%", 10, 10),
    ], lines=[("%%c160mm", 0, 0, 12, 0)])
    out = tmp_path / "plan.json"
    rc, msg = run("read_network_plan.py", "--dxf", dxf, "--site", "NMAR",
                  "--network", "water", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    pipes = doc["pipes"]
    assert any(p.get("diameter_mm") == 160 for p in pipes)


# --------------------------------------------------------------------------- T11-4 W3 thresholds ±0.01
def test_t11_4_w3_gradient_threshold(tmp_path):
    """T11-4: 200 mm at 0.49 % flags; 0.50 % passes (± 0.01)."""
    def net(slope):
        return {
            "networks_version": "1.0",
            "network": "sewer",
            "source": {"path": "syn", "sha256": "a" * 64, "kind": "synthetic"},
            "conduits": [{
                "id": "C1", "element_id": 1, "source": "model", "network": "sewer",
                "diameter_mm": 200, "length_m": 50.0,
                "invert_start_m": 100.0,
                "invert_stop_m": 100.0 - 50.0 * slope / 100.0,
                "slope_pct": slope, "cover_m": 1.5,
                "start_node_id": 1, "stop_node_id": 2,
            }],
            "nodes": [
                {"element_id": 1, "label": "MH1", "element_type": "manhole"},
                {"element_id": 2, "label": "OF", "element_type": "outfall"},
            ],
            "summary": {"conduit_count": 1, "length_km": 0.05},
            "plan": [],
        }

    cons_ok, _, _ = check(tmp_path / "ok", net(0.50), presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    slope_rows = [r for r in cons_ok["comparisons"]
                  if r["check"] == "W3" and str(r["quantity"]).startswith("min_slope")]
    assert slope_rows and slope_rows[0]["result"] == "pass"

    cons_bad, cand, _ = check(tmp_path / "bad", net(0.49), presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    flags = [r for r in cons_bad["comparisons"]
             if r["check"] == "W3" and str(r["quantity"]).startswith("min_slope")
             and r["result"] == "flag"]
    assert flags and flags[0]["severity"] == "Major"
    finds = [f for f in cand["findings"] if "gradient" in f["title"].casefold()]
    assert finds and finds[0]["proposed_status"] == "Probable"
    assert finds[0]["agent"] == "WUT"
    assert finds[0]["reference"]["rule_id"] is None
    assert finds[0]["report_only"] is True
    assert finds[0]["owner"]["role"] == "DMR"


# --------------------------------------------------------------------------- T11-5 adverse
def test_t11_5_adverse_grade(tmp_path):
    """T11-5: rising invert toward outfall → W3 adverse Major Probable."""
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "b" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "ADV", "element_id": 1, "source": "model",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 100.0, "invert_stop_m": 100.10,
            "slope_pct": -0.25, "cover_m": 1.5,
            "start_node_id": 1, "stop_node_id": 2,
        }],
        "nodes": [
            {"element_id": 1, "label": "MH1", "element_type": "manhole"},
            {"element_id": 2, "label": "OF", "element_type": "outfall"},
        ],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    cons, cand, _ = check(tmp_path, networks, presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    adv = [r for r in cons["comparisons"]
           if r["check"] == "W3" and str(r["quantity"]).startswith("adverse")]
    assert adv and adv[0]["result"] == "flag" and adv[0]["severity"] == "Major"
    assert any("adverse" in f["title"].casefold() for f in cand["findings"])


def test_t11_5b_abs_slope_stop_to_start_no_adverse(tmp_path):
    """T1: pipe stored stop→start with |S| above min → no gradient flag, no adverse."""
    # Topology: start=outfall(2), stop=upstream(1). Inverts drop toward start.
    # Stored slope negative; |S|=1.25 % ≥ 0.50 %. Flow toward outfall is downhill.
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "e" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "REV", "element_id": 1, "source": "model", "network": "sewer",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 99.50, "invert_stop_m": 100.00,
            "slope_pct": -1.25, "cover_m": 1.5,
            "start_node_id": 2, "stop_node_id": 1,
        }],
        "nodes": [
            {"element_id": 1, "label": "MH1", "element_type": "manhole"},
            {"element_id": 2, "label": "OF", "element_type": "outfall"},
        ],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    presence = {
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    }
    cons, cand, _ = check(tmp_path, networks, presence=presence)
    grad = [r for r in cons["comparisons"]
            if r["check"] == "W3" and str(r["quantity"]).startswith("min_slope")]
    assert grad and grad[0]["result"] == "pass", grad
    adv = [r for r in cons["comparisons"]
           if r["check"] == "W3" and str(r["quantity"]).startswith("adverse")
           and r["result"] == "flag"]
    assert not adv, adv
    assert not any("gradient below" in f["title"].casefold() for f in cand["findings"])
    assert not any("adverse" in f["title"].casefold() for f in cand["findings"])


# --------------------------------------------------------------------------- T11-6 consistency tolerance edges
def test_t11_x1_bipartite_beats_greedy(tmp_path):
    """X1: maximum bipartite matching finds 2 pairs where greedy model-order finds 1."""
    sys.path.insert(0, str(SK))
    from wet_common import match_conduits_to_labels, dq9_ls_compatible

    conduits = [
        {"id": "C1", "length_m": 1.00, "slope_pct": 1.0, "diameter_mm": 200},
        {"id": "C2", "length_m": 1.04, "slope_pct": 1.0, "diameter_mm": 200},
    ]
    labels = [
        {"kind": "pipe_label", "length_m": 1.03, "slope_pct": 1.0, "raw": "L1"},
        {"kind": "pipe_label", "length_m": 0.96, "slope_pct": 1.0, "raw": "L2"},
    ]
    # L1 matches both; L2 matches only C1 → greedy C1→L1 leaves C2 unmatched
    greedy = 0
    used = set()
    for c in conduits:
        for j, p in enumerate(labels):
            if j in used:
                continue
            if dq9_ls_compatible(c, p):
                greedy += 1
                used.add(j)
                break
    assert greedy == 1
    pairs, n_left = match_conduits_to_labels(conduits, labels)
    assert n_left == 2 and len(pairs) == 2

    # W2 uses the same matcher — both element:L rows pass
    presence = {
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    }
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "a" * 64, "kind": "synthetic"},
        "conduits": [
            {**c, "element_id": i + 1, "source": "model",
             "invert_start_m": 100.0, "invert_stop_m": 99.0, "cover_m": 1.5,
             "start_node_id": 1, "stop_node_id": 2}
            for i, c in enumerate(conduits)
        ],
        "nodes": [],
        "summary": {"conduit_count": 2, "length_km": 0.002},
        "plan": labels,
    }
    cons, _, _ = check(tmp_path / "x1", networks, presence=presence)
    match_row = next(
        r for r in cons["comparisons"]
        if r["check"] == "W2" and r["quantity"] == "element_match_count"
    )
    assert match_row["values"]["matched"] == 2
    assert match_row["values"].get("method") == "maximum_bipartite"


def test_t11_6_w2_tolerance_edges(tmp_path):
    """T11-6: L ± 0.05 m edge — 0.05 pass, 0.06 flag Confirmed."""
    def networks(delta, slope_model=0.50, slope_plan=0.50):
        return {
            "networks_version": "1.0",
            "network": "sewer",
            "source": {"path": "syn", "sha256": "c" * 64, "kind": "synthetic"},
            "conduits": [{
                "id": "P1", "element_id": 1, "source": "model",
                "diameter_mm": 200, "length_m": 50.0,
                "invert_start_m": 100.0, "invert_stop_m": 99.75,
                "slope_pct": slope_model, "cover_m": 1.5,
                "start_node_id": 1, "stop_node_id": 2,
            }],
            "nodes": [],
            "summary": {"conduit_count": 1, "length_km": 0.05, "length_m": 50.0},
            "plan": [{
                "id": "P1", "source": "plan", "kind": "pipe_label",
                "raw": "P1 L=%.2f m S=%.2f %% Ø200" % (50.0 + delta, slope_plan),
                "length_m": 50.0 + delta, "slope_pct": slope_plan, "diameter_mm": 200,
            }],
        }

    presence = {
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    }
    cons_ok, cand_ok, _ = check(tmp_path / "edge_ok", networks(0.05), presence=presence)
    L_rows = [r for r in cons_ok["comparisons"]
              if r["check"] == "W2" and str(r["quantity"]).startswith("element:L")]
    assert L_rows and L_rows[0]["result"] == "pass"
    assert not any("length label" in f["title"] for f in cand_ok["findings"])

    cons_bad, cand_bad, _ = check(tmp_path / "edge_bad", networks(0.06), presence=presence)
    L_bad = [r for r in cons_bad["comparisons"]
             if r["check"] == "W2" and str(r["quantity"]).startswith("element:L")
             and r["result"] == "flag"]
    assert L_bad
    finds = [f for f in cand_bad["findings"] if "length label" in f["title"]]
    assert finds and finds[0]["proposed_status"] == "Confirmed"
    assert finds[0]["severity"] == "Minor"
    stmt = finds[0]["statement"]
    assert "50.06" in stmt or ("50.0" in stmt and "0.06" in stmt)

    # V2: stored-reversed model slope still matches positive plan S=
    cons_rev, cand_rev, _ = check(
        tmp_path / "edge_rev", networks(0.0, slope_model=-0.50, slope_plan=0.50),
        presence=presence,
    )
    S_rows = [r for r in cons_rev["comparisons"]
              if r["check"] == "W2" and str(r["quantity"]).startswith("element:S")]
    assert S_rows and S_rows[0]["result"] == "pass", S_rows
    assert not any("slope label" in f["title"].casefold() for f in cand_rev["findings"])


def test_t11_w6_er_conflict_observations(tmp_path):
    """W6 emits DQ-15 ER storm conflict Observations even without storm geometry."""
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "d" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "C1", "element_id": 1, "source": "model",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 100.0, "invert_stop_m": 99.8,
            "slope_pct": 0.5, "cover_m": 1.5,
            "start_node_id": 1, "stop_node_id": 2,
        }],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    cons, cand, _ = check(tmp_path, networks, presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    obs = [f for f in cand["findings"]
           if f["issue_type"] == "er_ambiguity" and f["severity"] == "Observation"]
    assert len(obs) >= 4
    assert all(f["reference"]["rule_id"] is None for f in obs)
    assert all(f["agent"] == "WUT" for f in cand["findings"])


def test_t11_shp_co17_cross_check(tmp_path):
    """DQ-3 SHP cross-check for Co-17 D/L/inverts (JSON export equals sqlite)."""
    db = make_sqlite(tmp_path / "syn.stsw.sqlite", alts=((1, "Base"),))
    shp = write(tmp_path / "co17.json", [
        {
            "label": "Co-17",
            "diameter_mm": 200,
            "length_m": 7.60,
            "invert_start_m": 759.413,
            "invert_stop_m": 759.365,
            "e": None, "n": None,
        },
    ])
    out = tmp_path / "networks.json"
    rc, msg = run(
        "read_bentley_sqlite.py", "--sqlite", db, "--out", out,
        "--shp", shp, "--label", "Co-17",
    )
    assert rc == 0, msg
    assert msg["shp_cross_check"] == "pass"
    doc = load(out)
    assert doc["shp_cross_check"]["result"] == "pass"
    checks = doc["shp_cross_check"]["checks"]
    assert checks["diameter_mm"]["result"] == "pass"
    assert checks["length_m"]["result"] == "pass"
    assert checks["invert_start_m"]["result"] == "pass"
    assert checks["invert_stop_m"]["result"] == "pass"


def test_t11_idaho_pipe_water_reader(tmp_path):
    """Water / irrigation uses IdahoPipe tables; Hazen-Williams C preserved."""
    db = make_sqlite(tmp_path / "syn.wtg.sqlite", network="water", alts=((1, "Base"), (2, "Alt")))
    out = tmp_path / "networks.json"
    rc, msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "water", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    assert doc["source"]["schema"] == "openflows_idaho_pipe"
    assert doc["summary"]["conduit_count"] == 2
    assert all(c["roughness_type"] == "HazenWilliams" for c in doc["conduits"])
    assert all(abs(c["roughness"] - 140) < 1e-6 for c in doc["conduits"])


def test_t11_4c_gradient_er_exact_or_declared_or_not_run(tmp_path):
    """U1: 250 mm → not_run (no ER/declared); 400 mm → declared 0.20 %."""
    presence = {
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    }

    def net(d_mm, slope):
        return {
            "networks_version": "1.0",
            "network": "sewer",
            "source": {"path": "syn", "sha256": "a" * 64, "kind": "synthetic"},
            "conduits": [{
                "id": "C1", "element_id": 1, "source": "model", "network": "sewer",
                "diameter_mm": d_mm, "length_m": 50.0,
                "invert_start_m": 100.0,
                "invert_stop_m": 100.0 - 50.0 * slope / 100.0,
                "slope_pct": slope, "cover_m": 1.5,
                "start_node_id": 1, "stop_node_id": 2,
            }],
            "nodes": [
                {"element_id": 1, "label": "MH1", "element_type": "manhole"},
                {"element_id": 2, "label": "OF", "element_type": "outfall"},
            ],
            "summary": {"conduit_count": 1, "length_km": 0.05},
            "plan": [],
        }

    # 250 mm: ER silent, no declared row → not_run, no finding
    cons250, cand250, _ = check(tmp_path / "d250", net(250, 0.10), presence=presence)
    g250 = [r for r in cons250["comparisons"]
            if r["check"] == "W3" and str(r["quantity"]).startswith("min_slope")]
    assert g250 and g250[0]["result"] == "not_run"
    assert "no ER / declared minimum for D 250" in (g250[0].get("note") or "")
    assert not any("gradient below" in f["title"].casefold() for f in cand250["findings"])

    # 400 mm with declared Table 6.3 peak 2.0 mm/m = 0.20 %
    criteria = {
        "gradient_tables": {
            "sewer": [{
                "diameter_mm": 400,
                "min_slope_mm_per_m": 2.0,
                "slope_peak_mm_per_m": 2.0,
                "slope_half_full_mm_per_m": 1.5,
                "min_slope_pct": 0.20,
                "units": "mm/m",
            }],
        },
        "declared_totals": {},
        "storm_pipe_table": [],
    }
    cons400, cand400, _ = check(
        tmp_path / "d400", net(400, 0.15), criteria=criteria, presence=presence,
    )
    g400 = [r for r in cons400["comparisons"]
            if r["check"] == "W3" and str(r["quantity"]).startswith("min_slope")
            and r["result"] == "flag"]
    assert g400, cons400["comparisons"]
    assert abs(g400[0]["values"].get("min_pct", 0) - 0.20) < 1e-9
    assert any("gradient below" in f["title"].casefold() for f in cand400["findings"])

    cons400ok, _, _ = check(
        tmp_path / "d400ok", net(400, 0.20), criteria=criteria, presence=presence,
    )
    g400ok = [r for r in cons400ok["comparisons"]
              if r["check"] == "W3" and str(r["quantity"]).startswith("min_slope")]
    assert g400ok and g400ok[0]["result"] == "pass"


def test_t11_4b_w3_mh_spacing_threshold(tmp_path):
    """S1: 80.0007 m no flag; 80.02 m flags (w3_threshold_tol 0.01)."""
    def net(length):
        return {
            "networks_version": "1.0",
            "network": "sewer",
            "source": {"path": "syn", "sha256": "a" * 64, "kind": "synthetic"},
            "conduits": [{
                "id": "C1", "element_id": 1, "source": "model", "network": "sewer",
                "diameter_mm": 200, "length_m": length,
                "invert_start_m": 100.0,
                "invert_stop_m": 99.6,
                "slope_pct": 0.50, "cover_m": 1.5,
                "start_node_id": 1, "stop_node_id": 2,
            }],
            "nodes": [
                {"element_id": 1, "label": "MH1", "element_type": "manhole"},
                {"element_id": 2, "label": "OF", "element_type": "outfall"},
            ],
            "summary": {"conduit_count": 1, "length_km": length / 1000.0},
            "plan": [],
        }

    presence = {
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    }
    cons_ok, _, _ = check(tmp_path / "sp_ok", net(80.0007), presence=presence)
    sp_ok = [r for r in cons_ok["comparisons"]
             if r["check"] == "W3" and str(r["quantity"]).startswith("mh_spacing")]
    assert sp_ok and sp_ok[0]["result"] == "pass"

    cons_bad, cand, _ = check(tmp_path / "sp_bad", net(80.02), presence=presence)
    sp_bad = [r for r in cons_bad["comparisons"]
              if r["check"] == "W3" and str(r["quantity"]).startswith("mh_spacing")
              and r["result"] == "flag"]
    assert sp_bad and sp_bad[0]["severity"] == "Major"
    assert any("spacing" in f["title"].casefold() for f in cand["findings"])


def test_t11_lwpolyline_length_with_bulge(tmp_path):
    """T2: LWPOLYLINE length from vertices; bulges as arcs; layer 0 dropped."""
    storm_layer = "CI-Ss_50_35_08_85-M-SW_Pipe Dia 400"
    # Straight 100 m segment + semicircle bulge (chord 20 m → arc π*10)
    dxf = make_dxf(
        tmp_path / "sto.dxf",
        texts=[],
        lwpolylines=[
            (storm_layer, [(0.0, 0.0), (100.0, 0.0)], False),
            (storm_layer, [(0.0, 10.0, 1.0), (20.0, 10.0, 0.0)], False),
            # layer 0 noise — must be ignored
            ("0", [(0.0, 0.0), (1000.0, 0.0)], False),
        ],
    )
    out = tmp_path / "plan.json"
    rc, msg = run("read_network_plan.py", "--dxf", dxf, "--site", "NMAR",
                  "--network", "storm", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    geoms = [p for p in doc["pipes"] if p.get("kind") == "pipe_geometry"]
    assert all(p.get("layer") != "0" for p in geoms)
    lengths = sorted(p["length_m"] for p in geoms if p.get("length_m"))
    assert len(lengths) == 2
    assert abs(lengths[0] - math.pi * 10.0) < 0.05 or abs(lengths[1] - math.pi * 10.0) < 0.05
    assert any(abs(L - 100.0) < 1e-6 for L in lengths)


def test_t11_criteria_reader(tmp_path):
    """read_wet_criteria: PW §5.3, Tables 6.1/6.3 rows, Table 10.9 10-col, TOC skip."""
    text = {
        "pages": [{
            "page": 5,
            "text": (
                "Table of Contents\n"
                "5.3 Potable Water Design Criteria .......... 60\n"
                "6.3.2 Manholes .............................. 64\n"
                "6.5.1 Configuration ......................... 81\n"
            ),
        }, {
            # V3: real NMAR sentence forms (report p.72–73), not "minimum pressure … bar"
            "page": 72,
            "text": (
                "§5.3 Potable Water Design Criteria\n"
                "The residual pressure in the distribution system shall not be "
                "less than 1.50 bars under all operating conditions.\n"
                "The pressure of the network will not exceed 6.0 bars of maximum "
                "operating pressure.\n"
                "110mm for distribution mains.\n"
                "160mm for the pipeline provides a fire flow.\n"
                "Velocity 0.2 – 2.0 m/s\n"
                "Table 5.3 head-loss 20 m/km for D < 600 and 10 m/km for 600–1000\n"
                "140 for High Density Polyethylene (HDPE) pipes.\n"
                "130 for Ductile Iron (DI) pipes.\n"
                "Isolating valves will be provided at a maximum distance of "
                "every 1000 meters along the network.\n"
                "Cover 1.2 / 1.0 m\n"
                "Separation 3 m / 500 mm from sewers\n"
            ),
        }, {
            "page": 64,
            "text": (
                "Sewer network design criteria §6.3\n"
                "§6.3.4 Minimum diameter gravity sewer 200 mm\n"
                "House connections minimum diameter 160 mm\n"
                "Table 6.1 Manhole spacing\n"
                # C1: spaced ASCII hyphen; 1200 value continues after parenthetical
                "150 - 200 → 100\n"
                "300 – 500: 120 m\n"
                "600 – 800: 160 m\n"
                "≤ 1000: 200 m\n"
                "1200 and above → 300 (in agreement with ER manhole spacing)\n"
                "Table 6.3 Minimum gradients mm/m\n"
                "200: 5.00 / 3.20\n"
                "300: 3.50 / 2.50\n"
                "400: 2.00 / 1.50\n"
                "1200 and larger 0.50 / 0.35\n"
            ),
        }, {
            "page": 94,
            "text": (
                # U3: stray row must not enter Table 6.3
                "Appendix notes\n00 1 1\n"
            ),
        }, {
            "page": 70,
            "text": (
                "§5.5.1 Configuration\n"
                "Total network length 11.0 km residual pressure 2.76 / 5.27 bar\n"
            ),
        }, {
            "page": 81,
            "text": (
                "§6.5.1 Configuration\n"
                "Total network length 9.70 km\n"
                "Proposed No. of manholes: 173 manholes\n"
                "The sewer network depth ranges from 1.40 m to 3.50 m\n"
            ),
        }, {
            "page": 100,
            "text": (
                "Storm water drainage §10.2\n"
                "§10.5.5 Configuration\n"
                "Total network length 6.14 km\n"
            ),
        }, {
            "page": 142,
            "text": (
                "Table 10.9 Storm pipe schedule\n"
                "Label Invert Start Invert Stop Length Slope Section Size Material n Cover\n"
                # V1: length 25.30 must not abort table via false "5.3" PW switch
                "P-055 760.87 760.50 25.30 1.46 Circular 400 Concrete 0.013 0.18\n"
                "P-066 759.00 758.70 20.00 1.50 Circular 400 Concrete 0.013 0.90\n"
            ),
        }, {
            "page": 151,
            "text": (
                # V1: repeated header + rows after P-066 (pages 151–153)
                "Label Invert Start Invert Stop Length Slope Section Size Material n Cover\n"
                "P-118 758.20 757.90 40.00 0.75 Circular 500 Concrete 0.013 0.63\n"
                "P-119 755.00 754.60 30.00 1.33 Circular 600 Concrete 0.013 1.15\n"
            ),
        }, {
            "page": 153,
            "text": (
                "Label Invert Start Invert Stop Length Slope Section Size Material n Cover\n"
                "P-092 750.00 749.20 110.6 0.72 Circular 800 Concrete 0.013 1.50\n"
                "P-088 748.00 747.40 100.7 0.60 Circular 700 Concrete 0.013 1.40\n"
                "TABLE 10.9 HYDRAULIC DESIGN RESULTS – STORM WATER NETWORK\n"
            ),
        }],
    }
    src = write(tmp_path / "report.pdf.json", text)
    out = tmp_path / "criteria.json"
    rc, msg = run("read_wet_criteria.py", "--text", src, "--out", out)
    assert rc == 0, msg
    doc = load(out)
    Draft202012Validator(load(TPL / "criteria.schema.json")).validate(doc)
    # T3 / S3 (a) PW §5.3
    pw = doc["criteria"]["potable_water"]
    assert abs(pw["pressure_pair_bar"]["min_bar"] - 1.50) < 1e-9
    assert abs(pw["pressure_pair_bar"]["max_bar"] - 6.0) < 1e-9
    dpair = pw["min_diameter_mm_pair"]
    assert abs(dpair["distribution_mains_mm"] - 110) < 1e-9
    assert abs(dpair["fire_flow_mm"] - 160) < 1e-9
    assert pw.get("velocity_range_ms") or pw.get("min_velocity_ms")
    assert abs(pw["hw_c_pair"]["c_hdpe"] - 140) < 1e-9
    assert abs(pw["hw_c_pair"]["c_di"] - 130) < 1e-9
    assert abs(float(pw["valve_spacing_m"]["value"]) - 1000) < 1e-9
    assert pw.get("cover_m_pair") or pw.get("cover_m")
    hl = pw["head_loss_table"]
    assert abs(hl["lt_600_mm_m_per_km"] - 20) < 1e-9
    assert abs(hl["d_600_to_1000_m_per_km"] - 10) < 1e-9
    # C1: Table 6.1 all 5 rows — spacing 100 / 120 / 160 / 200 / 300
    spans = sorted(float(r["mh_spacing_m"]) for r in doc["mh_spacing_table"]
                   if r.get("mh_spacing_m") is not None)
    assert spans[:5] == [100.0, 120.0, 160.0, 200.0, 300.0] or set(spans) >= {
        100.0, 120.0, 160.0, 200.0, 300.0
    }, doc["mh_spacing_table"]
    assert any(abs(r["mh_spacing_m"] - 100) < 0.5
               and r.get("diameter_min_mm") == 150
               and r.get("diameter_max_mm") == 200
               for r in doc["mh_spacing_table"]), doc["mh_spacing_table"]
    assert any(
        abs(r["mh_spacing_m"] - 300) < 0.5
        and (r.get("diameter_min_mm") == 1200 or abs(r["diameter_mm"] - 1200) < 0.5)
        for r in doc["mh_spacing_table"]
    ), doc["mh_spacing_table"]
    grads = doc["gradient_tables"]["sewer"]
    assert not any(r["diameter_mm"] < 100 for r in grads)
    g200 = next(r for r in grads if r["diameter_mm"] == 200)
    assert abs(g200["min_slope_mm_per_m"] - 5.00) < 1e-9
    assert abs(g200["min_slope_pct"] - 0.50) < 1e-9  # ÷ 10
    assert abs(g200["slope_half_full_mm_per_m"] - 3.20) < 1e-9
    g1200 = next(r for r in grads if abs(r["diameter_mm"] - 1200) < 0.5)
    assert abs(g1200["min_slope_mm_per_m"] - 0.50) < 1e-9
    assert abs(g1200["slope_half_full_mm_per_m"] - 0.35) < 1e-9
    # Gravity min D 200, not house-connection 160
    assert abs(doc["criteria"]["sewer"]["min_diameter_mm"]["value"] - 200) < 1e-9
    # S3 (c) totals from Configuration, not TOC
    assert abs(doc["declared_totals"]["sewer:total_length_km"]["value"] - 9.70) < 1e-9
    assert int(doc["declared_totals"]["sewer:manhole_count"]["value"]) == 173
    assert abs(doc["declared_totals"]["potable_water:total_length_km"]["value"] - 11.0) < 1e-9
    assert abs(doc["declared_totals"]["storm:total_length_km"]["value"] - 6.14) < 1e-9
    depth = doc["declared_totals"]["sewer:depth_range_m"]
    assert abs(depth["min_m"] - 1.40) < 1e-9 and abs(depth["max_m"] - 3.50) < 1e-9
    assert 5 in (doc["meta"].get("toc_pages") or [])
    clauses = {t["clause"] for t in doc["declared_tables"]}
    assert "§5.3" in clauses and "§6.3" in clauses and "§10.2" in clauses
    assert "Table 6.1" in clauses and "Table 6.3" in clauses and "Table 10.9" in clauses
    # S2/V1 Table 10.9 continues past P-066 / length 25.30; unique by label
    by_id = {r["id"]: r for r in doc["storm_table_109"]}
    assert len(by_id) == len(doc["storm_table_109"])
    assert "P-066" in by_id and "P-118" in by_id and "P-119" in by_id
    assert abs(by_id["P-055"]["cover_m"] - 0.18) < 1e-9
    assert abs(by_id["P-055"]["invert_start_m"] - 760.87) < 1e-9
    assert abs(by_id["P-055"]["length_m"] - 25.30) < 1e-9
    assert abs(by_id["P-118"]["cover_m"] - 0.63) < 1e-9
    assert abs(by_id["P-119"]["cover_m"] - 1.15) < 1e-9
    assert abs(by_id["P-092"]["length_m"] - 110.6) < 1e-9
    assert abs(by_id["P-088"]["length_m"] - 100.7) < 1e-9
    total_109_km = sum(r["length_m"] for r in doc["storm_table_109"]) / 1000.0
    assert total_109_km > 0.3  # synthetic subset; real ≈ 6.07 km


def test_t11_params_source_tiers_dq7a():
    """DQ-7a: every governing threshold has source_tier / source / value; crown from CMP."""
    p = load(REF / "params.json")
    thr = p["thresholds"]
    for key, row in thr.items():
        assert "source_tier" in row, key
        assert 1 <= int(row["source_tier"]) <= 5, key
        assert row.get("source"), key
        # other-D gradient may be null until CMP confirmed [GAP]
        if key != "sewer.min_slope_pct.other":
            assert row.get("value") is not None, key
    assert thr["sewer.cover_to_crown_m"]["value"] == 1.2
    assert thr["sewer.cover_to_crown_m"]["source_tier"] == 1
    assert "CMP Q185" in thr["sewer.cover_to_crown_m"]["source"]
    assert thr["sewer.max_depth_to_crown_m"]["value"] == 6.0
    assert thr["water.valve_spacing_m"]["value"] == 500
    assert thr["water.pressure_min_bar"].get("approx") is True
    assert p["er"]["sewer"]["cover_to_crown_m"] == 1.2
    assert p["er"]["sewer"]["max_depth_to_crown_m"] == 6.0
    assert p["declared"]["sewer"]["cover_m"] == 1.0  # soffit — W8 only
    assert p["declared"]["sewer"]["source_tier"] == 5
    assert "W8" in p["phase_scope"]
    assert len(p["same_tier_alternatives"]) >= 4
    assert len(p["w8_comparisons"]) >= 8


def test_t11_wet_layers_agreed_real_names():
    """R3: wet_layers.json uses AGREED tag and real NMAR layer names (no invented)."""
    doc = load(REF / "wet_layers.json")
    assert "[AGREED 21:55]" in doc["tag"]
    layers = doc["shared_layers"]
    assert "C-SSWR-MNHL-P" in layers
    assert "CI-Ss_50_35_08_30-P-SS_Annotation Main Sewer" in layers
    assert "C-PW-FIRE HYDRANT COVER CATCH" in layers
    for banned in ("SWN-PIPE", "C-PIPE-200", "-02- Sewerage", "-05- Hydrant"):
        assert banned not in layers
    assert any("SS_Pipe Dia" in p["regex"] for p in doc["layer_patterns"])
    assert any("%%c" in p["regex"] for p in doc["layer_patterns"])


def test_t11_finding_schema_accepts_wut():
    """finding.schema.json agent enum + finding_id pattern include WUT."""
    s = json.loads((PLUGIN / "schemas/finding.schema.json").read_text(encoding="utf-8"))
    assert "WUT" in s["properties"]["agent"]["enum"]
    assert "WUT" in s["properties"]["finding_id"]["pattern"]
    from jsonschema import Draft202012Validator
    finding = {
        "schema_version": "1.0",
        "finding_id": "NMAR-SYN-19700101T0000/WUT-001",
        "stage": "candidate",
        "run_id": "NMAR-SYN-19700101T0000",
        "site": "NMAR",
        "sub_id": "SYN",
        "agent": "WUT",
        "title": "Synthetic WUT finding title ok",
        "statement": "Synthetic statement long enough for schema validation.",
        "issue_type": "design_error",
        "location": {"document_no": "SWN-MOD-00001", "package_level": True},
        "severity": "Major",
        "confidence": "Medium",
        "evidence": [{
            "evidence_id": "E01", "tier": "deterministic",
            "source_type": "script_output", "extract": "test",
        }],
        "reference": {"rule_id": None, "source_doc": "ER", "clause": "W3"},
        "proposed_status": "Probable",
        "owner": {"role": "DMR", "name": None},
        "created_at": "2026-11-01T00:00:00Z",
    }
    Draft202012Validator(s).validate(finding)


def test_t11_w1_missing_storm_model(tmp_path):
    """W1 / DQ-8: missing storm model → missing_information."""
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "e" * 64, "kind": "synthetic"},
        "conduits": [],
        "summary": {"conduit_count": 0, "length_km": 0},
        "plan": [],
    }
    cons, cand, _ = check(tmp_path, networks, presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": False},
        },
        "hydraulic_calculations": False,
    })
    storm = [f for f in cand["findings"] if "storm" in f["title"].casefold() and "model" in f["title"].casefold()]
    assert storm and storm[0]["issue_type"] == "missing_information"
    assert storm[0]["severity"] == "Minor"
    calc = [f for f in cand["findings"] if "hydraulic calculation" in f["title"].casefold()]
    assert calc


def test_t11_w3_crown_cover_cmp_and_w5_declared_min_d(tmp_path):
    """DQ-7a: W3 crown cover uses CMP 1.2 m; 0.8 → Minor; W5 min D stays declared 110."""
    sewer = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "f" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "SHALLOW", "element_id": 1, "source": "model",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 100.0, "invert_stop_m": 99.8,
            "slope_pct": 0.5, "cover_m": 0.8,
            "start_node_id": 1, "stop_node_id": 2,
        }],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    cons, cand, _ = check(tmp_path / "sewer", sewer, presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    cover = [r for r in cons["comparisons"]
             if r["check"] == "W3" and str(r["quantity"]).startswith("cover")
             and r["result"] == "flag"]
    assert cover and "CMP Q185" in str(cover[0]["values"].get("source"))
    assert cover[0]["severity"] == "Minor"
    assert any(
        "allowed to 0.5 m if protected" in f["statement"].casefold()
        for f in cand["findings"]
    )

    water = {
        "networks_version": "1.0",
        "network": "water",
        "source": {"path": "syn", "sha256": "g" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "TINY", "element_id": 1, "source": "model", "network": "water",
            "diameter_mm": 90, "length_m": 20.0, "cover_m": 1.5,
        }],
        "summary": {"conduit_count": 1, "length_km": 0.02},
        "plan": [],
    }
    cons_w, cand_w, _ = check(tmp_path / "water", water, presence={
        "networks": {
            "sewer": {"plan": True, "model": True},
            "water": {"plan": True, "model": True},
            "irrigation": {"plan": True, "model": True},
            "storm": {"plan": True, "model": True},
        },
        "hydraulic_calculations": True,
    })
    mind = [r for r in cons_w["comparisons"]
            if r["check"] == "W5" and str(r["quantity"]).startswith("min_D")
            and r["result"] == "flag"]
    assert mind and mind[0]["values"].get("source") == "declared"
    assert any("5.3.7" in f["statement"] for f in cand_w["findings"])


def test_skill_md_under_500_and_frontmatter():
    text = (SK.parent / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert len(text.splitlines()) < 500
    assert 'name: reviewing-wet-utilities' in text
    assert 'version: "0.1.1"' in text
    assert "WUT" in text
    assert "DQ-7a" in text or "source_tier" in text or "W8" in text


PRESENCE_OK = {
    "networks": {
        "sewer": {"plan": True, "model": True},
        "water": {"plan": True, "model": True},
        "irrigation": {"plan": True, "model": True},
        "storm": {"plan": True, "model": True},
    },
    "hydraulic_calculations": True,
}


def _sewer_net(cover, *, slope=0.5, length=40.0, d_mm=200):
    return {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "a" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "C1", "element_id": 1, "source": "model", "network": "sewer",
            "diameter_mm": d_mm, "length_m": length,
            "invert_start_m": 100.0,
            "invert_stop_m": 100.0 - length * slope / 100.0,
            "slope_pct": slope, "cover_m": cover,
            "start_node_id": 1, "stop_node_id": 2,
        }],
        "nodes": [
            {"element_id": 1, "label": "MH1", "element_type": "manhole"},
            {"element_id": 2, "label": "OF", "element_type": "outfall"},
        ],
        "summary": {"conduit_count": 1, "length_km": length / 1000.0},
        "plan": [],
    }


def test_t11_source_tier_wins(tmp_path):
    """DQ-7a: governing = lowest source_tier (CMP/ER tier 1 over declared tier 5)."""
    sys.path.insert(0, str(SK))
    from wet_common import governing_threshold, load_params
    params = load_params(REF / "params.json")
    cover = governing_threshold(params, "sewer.cover_to_crown_m")
    assert cover and cover["source_tier"] == 1 and abs(cover["value"] - 1.2) < 1e-9
    assert "CMP" in cover["source"]
    depth = governing_threshold(params, "sewer.max_depth_to_crown_m")
    assert depth and depth["source_tier"] == 1 and abs(depth["value"] - 6.0) < 1e-9
    # Declared soffit 1.0 must not govern W3
    cons, _, _ = check(tmp_path, _sewer_net(1.05), presence=PRESENCE_OK)
    cover_rows = [r for r in cons["comparisons"]
                  if r["check"] == "W3" and str(r["quantity"]).startswith("cover")]
    assert cover_rows and cover_rows[0]["result"] == "flag"
    assert abs(cover_rows[0]["values"]["min_m"] - 1.2) < 1e-9


def test_t11_er_over_differing_cmp_observation(tmp_path):
    """Same-tier CMP≠ER → Observation findings (DQ-15 style); ER remains governing."""
    _cons, cand, _ = check(tmp_path, _sewer_net(1.5), presence=PRESENCE_OK)
    alts = [
        f for f in cand["findings"]
        if f["issue_type"] == "er_ambiguity"
        and ("same-tier" in f["statement"].casefold() or "Same-tier" in f["title"])
        and "CMP" in f["statement"]
    ]
    assert len(alts) >= 4, [f["title"] for f in cand["findings"] if f["issue_type"] == "er_ambiguity"]
    assert all(f["severity"] == "Observation" for f in alts)
    # Governing MH spacing still ER 80 (not CMP 100–200)
    cons2, _cand2, _ = check(tmp_path / "mh", _sewer_net(1.5, length=80.02), presence=PRESENCE_OK)
    sp = [r for r in cons2["comparisons"]
          if r["check"] == "W3" and str(r["quantity"]).startswith("mh_spacing")
          and r["result"] == "flag"]
    assert sp and abs(sp[0]["values"]["max_m"] - 80) < 1e-9


def test_t11_w8_equal_stricter_weaker(tmp_path):
    """W8: equal → no finding; stricter → Observation; weaker → Minor Confirmed."""
    criteria = {
        "criteria": {
            "potable_water": {
                "pressure_min_bar": {
                    "value": 1.50, "page": 72, "clause": "§5.3", "text": "1.50 bars",
                    "as_written": "Minimum residual pressure 1.50 bars",
                },
                "valve_spacing_m": {
                    "value": 1000, "page": 72, "clause": "§5.3.11", "text": "1000 m",
                },
                "max_velocity_ms": {
                    "value": 2.0, "page": 72, "clause": "§5.3", "text": "2.0 m/s",
                },
                "head_loss_table": {
                    "lt_600_mm_m_per_km": 20, "page": 72, "clause": "Table 5.3",
                    "as_written": "Head loss <600 mm: 20 m/km",
                },
                "fire_residual_bar": {
                    "value": 1.40, "page": 63, "clause": "§5.5", "text": "1.40 bars",
                    "as_written": "residual pressure at the fire hydrants shall not be less than 1.40 bars",
                },
            },
            "sewer": {
                "cover_m": {"value": 1.0, "page": 64, "clause": "§6.3.3", "note": "soffit"},
                "max_depth_m": {
                    "value": {"min": 8.0, "max": 10.0},
                    "min_m": 8.0, "max_m": 10.0,
                    "page": 64, "clause": "§6.3",
                },
                "d_over_d_max": {"value": 0.70, "page": 64, "clause": "§6.3"},
            },
            "storm": {
                "return_period_yr": {"value": 10, "page": 100, "clause": "§10.2"},
                "cover_m": {"value": 1.2, "page": 100, "clause": "§10.2"},
            },
        },
        "mh_spacing_table": [{
            "diameter_mm": 150, "diameter_min_mm": 150, "diameter_max_mm": 200,
            "mh_spacing_m": 100, "page": 64, "text": "150-200 → 100",
        }],
        "gradient_tables": {
            "sewer": [{
                "diameter_mm": 200, "min_slope_mm_per_m": 5.0,
                "min_slope_pct": 0.50, "page": 64, "text": "200: 5.00 / 3.20",
            }],
        },
        "declared_totals": {},
        "storm_pipe_table": [],
    }
    cons, cand, _ = check(
        tmp_path, _sewer_net(1.5), criteria=criteria, presence=PRESENCE_OK,
    )
    w8 = [f for f in cand["findings"] if f["issue_type"] == "inconsistency"
          and "governing" in f["statement"].casefold()]
    titles = " | ".join(f["title"] for f in w8)
    stmts = " | ".join(f["statement"] for f in w8)
    # weaker
    assert any("pressure" in f["title"].casefold() and "weaker" in f["statement"]
               for f in w8), titles
    assert any("head loss" in f["title"].casefold() and "weaker" in f["statement"]
               for f in w8), titles
    assert any("isolation" in f["title"].casefold() and "weaker" in f["statement"]
               for f in w8), titles
    assert any("fire residual" in f["title"].casefold() and "weaker" in f["statement"]
               for f in w8), titles
    assert any("mh spacing" in f["title"].casefold() and "weaker" in f["statement"]
               for f in w8), titles
    assert any("cover" in f["title"].casefold() and "weaker" in f["statement"]
               for f in w8), titles
    assert any("max depth" in f["title"].casefold() and "weaker" in f["statement"]
               for f in w8), titles
    # stricter
    assert any("d/d" in f["title"].casefold() and "stricter" in f["statement"]
               for f in w8), titles
    assert all(
        f["severity"] == ("Observation" if "stricter" in f["statement"] else "Minor")
        and f["proposed_status"] == "Confirmed"
        for f in w8
    )
    # equal / inside range → no finding
    assert "max velocity" not in titles.casefold()
    assert "min gradient 200" not in titles.casefold()
    assert "return period" not in titles.casefold()
    assert "storm cover" not in titles.casefold()
    # A2: quote declared as written with parsed number in [brackets]
    assert any(
        "1.50 bars" in f["statement"] and "[1.5" in f["statement"]
        for f in w8 if "pressure" in f["title"].casefold()
    ), stmts
    assert any(
        "[" in f["statement"] and "]" in f["statement"] for f in w8
    ), stmts
    # pass rows for equals
    eq = [r for r in cons["comparisons"]
          if r["check"] == "W8" and r["result"] == "pass"
          and r["values"].get("relation") == "equal"]
    assert any(r["values"].get("criterion") == "pw_velocity_max" for r in eq)
    assert any(r["values"].get("criterion") == "sewer_min_grad_200" for r in eq)
    assert any(r["values"].get("criterion") == "storm_cover" for r in eq)
    assert "1.50" in stmts and "~3.5" in stmts or "3.5" in stmts


def test_t11_w8_range_edge(tmp_path):
    """Declared value on governing range edge counts as equal (no W8 finding)."""
    criteria = {
        "criteria": {
            "potable_water": {
                "max_velocity_ms": {"value": 3.0, "page": 1, "clause": "§5.3"},
            },
        },
        "gradient_tables": {"sewer": []},
        "mh_spacing_table": [],
        "declared_totals": {},
        "storm_pipe_table": [],
    }
    cons, cand, _ = check(
        tmp_path, _sewer_net(1.5), criteria=criteria, presence=PRESENCE_OK,
    )
    assert not any(
        "max velocity" in f["title"].casefold() and "governing" in f["statement"].casefold()
        for f in cand["findings"]
    )
    row = next(
        r for r in cons["comparisons"]
        if r["check"] == "W8" and r["values"].get("criterion") == "pw_velocity_max"
    )
    assert row["result"] == "pass" and row["values"]["relation"] == "equal"


def test_t11_w3_crown_cover_1_19_and_0_49(tmp_path):
    """Crown cover 1.19 → Minor; 0.49 → Major (CMP Q185)."""
    cons119, cand119, _ = check(
        tmp_path / "c119", _sewer_net(1.19), presence=PRESENCE_OK,
    )
    c119 = [r for r in cons119["comparisons"]
            if r["check"] == "W3" and str(r["quantity"]).startswith("cover")
            and r["result"] == "flag"]
    assert c119 and c119[0]["severity"] == "Minor"
    assert any("protected — confirm" in f["statement"] for f in cand119["findings"])

    cons049, cand049, _ = check(
        tmp_path / "c049", _sewer_net(0.49), presence=PRESENCE_OK,
    )
    c049 = [r for r in cons049["comparisons"]
            if r["check"] == "W3" and str(r["quantity"]).startswith("cover")
            and r["result"] == "flag"]
    assert c049 and c049[0]["severity"] == "Major"
    assert any("protected minimum" in f["title"].casefold() for f in cand049["findings"])


def test_t11_w3_depth_6_00_and_6_02(tmp_path):
    """Depth to crown 6.00 pass; 6.02 Major (max 6.0 + 0.01 tol)."""
    cons600, _, _ = check(tmp_path / "d600", _sewer_net(6.00), presence=PRESENCE_OK)
    d600 = [r for r in cons600["comparisons"]
            if r["check"] == "W3" and str(r["quantity"]).startswith("max_depth")]
    assert d600 and d600[0]["result"] == "pass"

    cons602, cand602, _ = check(tmp_path / "d602", _sewer_net(6.02), presence=PRESENCE_OK)
    d602 = [r for r in cons602["comparisons"]
            if r["check"] == "W3" and str(r["quantity"]).startswith("max_depth")
            and r["result"] == "flag"]
    assert d602 and d602[0]["severity"] == "Major"
    assert any(
        f["issue_type"] == "design_error" and "depth to crown" in f["title"].casefold()
        and "CMP" in (f["reference"].get("source_doc") or "")
        for f in cand602["findings"]
    )


def test_t11_criteria_fire_residual_and_sewer_w8_fields(tmp_path):
    """B1: read Table 5.3 / fire 1.40 / d/D 0.7 / storm 10-yr+1.2 / sewer soffit sentence."""
    text = {
        "pages": [{
            "page": 73,
            "text": (
                "Table 5.3 Head loss gradients\n"
                "1000 > D ≥ 600 → 10\n"
                "D < 600 → 20\n"
            ),
        }, {
            "page": 75,
            "text": (
                "§5.5 Fire fighting\n"
                "The system shall maintain a pressure of 1.40 bar at each fire hydrant "
                "during the fire scenario.\n"
            ),
        }, {
            "page": 77,
            "text": (
                "Sewer network design criteria §6.3\n"
                "§6.3.3 Where the depth of the pipe soffit is less than 1.0m, "
                "the pipeline should be protected with special measures.\n"
                "Maximum depth 8 – 10 m\n"
            ),
        }, {
            "page": 78,
            "text": (
                "§6.3.6 The design depth of flow shall not exceed 0.7 (70%) of the "
                "pipe diameters at peak flow.\n"
            ),
        }, {
            "page": 130,
            "text": (
                "Storm water drainage §10.2\n"
                "The network is designed for the 10-year return period storm event. "
                "In addition, a 20% climate change allowance is applied.\n"
                # A2: misreads that must NOT become storm cover_m / max_depth_m
                "Sheet cover reference 3512 m easting noise\n"
                "maximum depth of channel 30 m in the catchment description\n"
            ),
        }, {
            "page": 131,
            "text": (
                "§10.2.7 Soil cover above top of pipe shall not be less than 1.2m, "
                "otherwise pipe shall be encased in concrete.\n"
            ),
        }],
    }
    src = write(tmp_path / "report.pdf.json", text)
    out = tmp_path / "criteria.json"
    rc, msg = run("read_wet_criteria.py", "--text", src, "--out", out)
    assert rc == 0, msg
    doc = load(out)
    pw = doc["criteria"]["potable_water"]
    hl = pw.get("head_loss_table") or {}
    assert abs(float(hl.get("lt_600_mm_m_per_km")) - 20) < 1e-9, hl
    assert abs(float(hl.get("d_600_to_1000_m_per_km")) - 10) < 1e-9, hl
    assert hl.get("page") == 73
    fr = pw.get("fire_residual_bar")
    assert fr and abs(float(fr["value"]) - 1.40) < 1e-9
    assert fr.get("page") == 75
    assert "1.40" in (fr.get("as_written") or "")
    sewer = doc["criteria"]["sewer"]
    assert abs(float(sewer["cover_m"]["value"]) - 1.0) < 1e-9
    assert sewer["cover_m"].get("page") == 77
    assert "soffit" in (sewer["cover_m"].get("as_written") or "").casefold()
    assert "protect" in (sewer["cover_m"].get("as_written") or "").casefold()
    md = sewer["max_depth_m"]
    assert abs(float(md.get("min_m") or md["value"]["min"]) - 8.0) < 1e-9
    assert abs(float(md.get("max_m") or md["value"]["max"]) - 10.0) < 1e-9
    assert abs(float(sewer["d_over_d_max"]["value"]) - 0.70) < 1e-9
    assert sewer["d_over_d_max"].get("page") == 78
    storm = doc["criteria"]["storm"]
    assert storm.get("return_period_yr") and int(storm["return_period_yr"]["value"]) == 10
    assert storm["return_period_yr"].get("page") == 130
    assert storm.get("cover_m") and abs(float(storm["cover_m"]["value"]) - 1.2) < 1e-9
    assert storm["cover_m"].get("page") == 131
    assert storm.get("max_depth_m") is None  # 30 m rejected
    assert float(storm["cover_m"]["value"]) != 3512


def test_t11_a1_both_ends_cover_depth_report_mh(tmp_path):
    """A1: both ends checked; finding quotes end + MH label."""
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "a" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "C-DEEP", "element_id": 1, "source": "model", "network": "sewer",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 100.0, "invert_stop_m": 99.8,
            "slope_pct": 0.5,
            "cover_start_m": 1.5, "cover_stop_m": 6.24,
            "cover_m": 6.24,
            "start_node_id": 1, "stop_node_id": 2,
            "start_label": "MH-1", "stop_label": "MH-3",
        }],
        "nodes": [
            {"element_id": 1, "label": "MH-1", "element_type": "manhole",
             "depth_to_crown_m": 1.5},
            {"element_id": 2, "label": "MH-3", "element_type": "manhole",
             "depth_to_crown_m": 6.24},
            {"element_id": 3, "label": "OF-1", "element_type": "outfall"},
        ],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    # Link OF for adverse walk completeness
    networks["conduits"][0]["stop_node_id"] = 2
    cons, cand, _ = check(tmp_path, networks, presence=PRESENCE_OK)
    depth_flags = [
        r for r in cons["comparisons"]
        if r["check"] == "W3" and str(r["quantity"]).startswith("max_depth")
        and r["result"] == "flag"
    ]
    assert depth_flags, cons["comparisons"]
    assert any(r["values"].get("mh") == "MH-3" and r["values"].get("end") == "stop"
               for r in depth_flags)
    assert any(
        "MH-3" in f["statement"] and "stop end" in f["statement"]
        for f in cand["findings"] if "depth to crown" in f["title"].casefold()
    )
    # Shallow start end still passes cover
    cover_start = [
        r for r in cons["comparisons"]
        if r["check"] == "W3" and r["quantity"] == "cover:C-DEEP:start"
    ]
    assert cover_start and cover_start[0]["result"] == "pass"


def test_t11_a3_adverse_outfall_only_no_topology_infer(tmp_path):
    """A3: without Outfall element_type, adverse is not_run (no stops−starts infer)."""
    networks = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "b" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "ADV", "element_id": 1, "source": "model",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 100.0, "invert_stop_m": 100.10,
            "slope_pct": -0.25, "cover_m": 1.5,
            "start_node_id": 1, "stop_node_id": 2,
            "start_label": "MH-1", "stop_label": "MH-2",
        }],
        "nodes": [
            {"element_id": 1, "label": "MH-1", "element_type": "manhole"},
            # Topology sink but NOT an Outfall model element
            {"element_id": 2, "label": "MH-2", "element_type": "manhole"},
        ],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    cons, cand, _ = check(tmp_path, networks, presence=PRESENCE_OK)
    adv_flag = [
        r for r in cons["comparisons"]
        if r["check"] == "W3" and str(r["quantity"]).startswith("adverse")
        and r["result"] == "flag"
    ]
    assert not adv_flag
    assert not any("adverse" in f["title"].casefold() for f in cand["findings"])
    assert any(
        r["check"] == "W3" and r["quantity"] == "adverse" and r["result"] == "not_run"
        for r in cons["comparisons"]
    )


def test_t11_a3_adverse_reversed_and_real_rise(tmp_path):
    """A3 synthetic: reversed-stored downhill → no flag; real rise toward Outfall → 1 flag."""
    # Reversed-stored: start=outfall, stop=upstream; inverts drop toward outfall
    rev = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "c" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "REV", "element_id": 1, "source": "model",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 99.50, "invert_stop_m": 100.00,
            "slope_pct": -1.25, "cover_m": 1.5,
            "start_node_id": 2, "stop_node_id": 1,
            "start_label": "OF-1", "stop_label": "MH-1",
        }],
        "nodes": [
            {"element_id": 1, "label": "MH-1", "element_type": "manhole"},
            {"element_id": 2, "label": "OF-1", "element_type": "outfall"},
        ],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    cons_rev, cand_rev, _ = check(tmp_path / "rev", rev, presence=PRESENCE_OK)
    assert not any(
        r["check"] == "W3" and str(r["quantity"]).startswith("adverse")
        and r["result"] == "flag" for r in cons_rev["comparisons"]
    )
    assert not any("adverse" in f["title"].casefold() for f in cand_rev["findings"])

    rise = {
        "networks_version": "1.0",
        "network": "sewer",
        "source": {"path": "syn", "sha256": "d" * 64, "kind": "synthetic"},
        "conduits": [{
            "id": "RISE", "element_id": 1, "source": "model",
            "diameter_mm": 200, "length_m": 40.0,
            "invert_start_m": 100.0, "invert_stop_m": 100.10,
            "slope_pct": -0.25, "cover_m": 1.5,
            "start_node_id": 1, "stop_node_id": 2,
            "start_label": "MH-1", "stop_label": "OF-1",
        }],
        "nodes": [
            {"element_id": 1, "label": "MH-1", "element_type": "manhole"},
            {"element_id": 2, "label": "OF-1", "element_type": "outfall"},
        ],
        "summary": {"conduit_count": 1, "length_km": 0.04},
        "plan": [],
    }
    cons_rise, cand_rise, _ = check(tmp_path / "rise", rise, presence=PRESENCE_OK)
    adv = [
        r for r in cons_rise["comparisons"]
        if r["check"] == "W3" and str(r["quantity"]).startswith("adverse")
        and r["result"] == "flag"
    ]
    assert len(adv) == 1
    assert sum(1 for f in cand_rise["findings"] if "adverse" in f["title"].casefold()) == 1


def test_t11_a4_wkb_srid_z_m_flags(tmp_path):
    """A4: EWKB type word with Z + SRID flags yields correct E/N (US ft → m)."""
    import struct
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("rbs", SK / "read_bentley_sqlite.py")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Little-endian Point with Z + SRID: type = 1 | 0x80000000 | 0x20000000
    # Coordinates in US feet ≈ NMAR Co-17 start
    x_ft, y_ft, z_ft = CO17_START_FT[0], CO17_START_FT[1], 100.0
    gtype = 1 | 0x80000000 | 0x20000000
    srid = 32637
    blob = b"".join([
        struct.pack("<B", 1),
        struct.pack("<I", gtype),
        struct.pack("<i", srid),
        struct.pack("<3d", x_ft, y_ft, z_ft),
    ])
    verts = mod.parse_wkb_vertices_ft(blob)
    assert len(verts) == 1
    assert abs(verts[0][0] - x_ft) < 1e-6
    assert abs(verts[0][1] - y_ft) < 1e-6
    geo = mod.geometry_from_wkb_ft(blob)
    assert geo["has_geometry"]
    assert abs(geo["e_m"] - 657629.4) < 0.1
    assert abs(geo["n_m"] - 2714320.5) < 0.1

    # ZM + SRID Point
    gtype_zm = 1 | 0x80000000 | 0x40000000 | 0x20000000
    blob_zm = b"".join([
        struct.pack("<B", 1),
        struct.pack("<I", gtype_zm),
        struct.pack("<i", srid),
        struct.pack("<4d", x_ft, y_ft, z_ft, 0.0),
    ])
    verts_zm = mod.parse_wkb_vertices_ft(blob_zm)
    assert len(verts_zm) == 1 and abs(verts_zm[0][0] - x_ft) < 1e-6


def test_t11_b2_hmigeometry_fallback_when_real_null(tmp_path):
    """B2: HMIGeometry 2D point WKB (feet) used when HMIRealGeometry is null."""
    import sqlite3
    import struct
    from importlib.util import spec_from_file_location, module_from_spec

    # O-1 example from S35 re-review (21-byte LE Point, US feet)
    o1_blob = bytes.fromhex("0101000000764647353e774041c3794264a2fc6041")
    db = tmp_path / "hmigeom.sqlite"
    # Minimal OpenFlows sewer with null RealGeometry, filled HMIGeometry
    conn = sqlite3.connect(db)
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE Alternatives (AlternativeID INTEGER PRIMARY KEY, Label TEXT);
        CREATE TABLE HMIModelingElement (
          ElementID INTEGER PRIMARY KEY, Label TEXT, IsDeleted INTEGER DEFAULT 0
        );
        CREATE TABLE Conduit (DomainElementID INTEGER PRIMARY KEY);
        CREATE TABLE Conduit_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER, ConduitDiameter REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE PhysicalLink_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_UpstreamInvert REAL, Physical_DownstreamInvert REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseLink_HMIDataSetTopology_Data (
          DomainElementID INTEGER PRIMARY KEY,
          HMITopologyStartNodeID INTEGER, HMITopologyStopNodeID INTEGER
        );
        CREATE TABLE BaseLink_HmiDataSetGeometry_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          HMIGeometryScaledLength REAL,
          HMIRealGeometry BLOB, HMIGeometry BLOB,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE GravityLinkBase_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_Material TEXT, Physical_ManningsN REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseNode_Physical_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          Physical_GroundElevation REAL,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE BaseNode_HmiDataSetGeometry_Data (
          DomainElementID INTEGER, AlternativeID INTEGER,
          HMIRealGeometry BLOB, HMIGeometry BLOB,
          PRIMARY KEY (DomainElementID, AlternativeID)
        );
        CREATE TABLE Outfall (DomainElementID INTEGER PRIMARY KEY);
        """
    )
    c.execute("INSERT INTO Alternatives VALUES (1, 'Base')")
    c.execute("INSERT INTO HMIModelingElement VALUES (101, 'O-1', 0)")
    c.execute("INSERT INTO HMIModelingElement VALUES (102, 'MH-2', 0)")
    c.execute("INSERT INTO HMIModelingElement VALUES (1, 'C-1', 0)")
    c.execute("INSERT INTO Outfall VALUES (101)")
    c.execute("INSERT INTO Conduit VALUES (1)")
    c.execute("INSERT INTO BaseLink_HMIDataSetTopology_Data VALUES (1, 101, 102)")
    c.execute(
        "INSERT INTO Conduit_Physical_Data VALUES (1, 1, ?)",
        (200.0 / FT_MM,),
    )
    c.execute(
        "INSERT INTO PhysicalLink_Physical_Data VALUES (1, 1, ?, ?)",
        (100.0 / FT, 99.8 / FT),
    )
    c.execute(
        "INSERT INTO BaseNode_Physical_Data VALUES (101, 1, ?)", (110.0 / FT,),
    )
    c.execute(
        "INSERT INTO BaseNode_Physical_Data VALUES (102, 1, ?)", (109.5 / FT,),
    )
    # Link: RealGeometry null; HMIGeometry = short 2D line from O-1
    x0, y0 = struct.unpack("<2d", o1_blob[5:21])
    link_wkb = make_linestring_wkb([(x0, y0), (x0 + 10.0, y0)], little=True)
    c.execute(
        "INSERT INTO BaseLink_HmiDataSetGeometry_Data VALUES (1, 1, ?, NULL, ?)",
        (40.0 / FT, link_wkb),
    )
    c.execute(
        "INSERT INTO GravityLinkBase_Physical_Data VALUES (1, 1, 'PVC', 0.013)",
    )
    # Node O-1: RealGeometry null; HMIGeometry = 21-byte point
    c.execute(
        "INSERT INTO BaseNode_HmiDataSetGeometry_Data VALUES (101, 1, NULL, ?)",
        (o1_blob,),
    )
    conn.commit()
    conn.close()

    out = tmp_path / "networks.json"
    rc, msg = run("read_bentley_sqlite.py", "--sqlite", db, "--network", "sewer",
                  "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    o1 = next(n for n in doc["nodes"] if n.get("label") == "O-1")
    assert o1.get("has_geometry")
    assert abs(o1["e_m"] - 657820.71) < 0.05, o1
    assert abs(o1["n_m"] - 2714549.75) < 0.05, o1
    c1 = next(c for c in doc["conduits"] if c.get("id") == "C-1")
    assert c1.get("has_geometry")
    assert abs(c1["e_m"] - 657820.71) < 0.05, c1
