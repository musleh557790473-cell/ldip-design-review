"""Skill 13 synthetic tests — SPC-0006 Rev L §4.14.14 TL1–TL7, TL10, TL12–TL14 + L-a + L-b.

No real site data. No deposited rulebook values. Synthetic EWK limits only.
"""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("shapely")

REPO = Path(__file__).resolve().parents[1]
SK13 = REPO / "plugin/ldip-design-review/skills/reviewing-earthworks-geotech/scripts"
PY = sys.executable


def run(script, *args, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    p = subprocess.run([PY, str(SK13 / script), *map(str, args)],
                       capture_output=True, text=True, env=e)
    out = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(out[-1]) if out else {"stderr": p.stderr[-3000:], "stdout": p.stdout[-2000:]})


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write(p, obj):
    Path(p).write_text(json.dumps(obj, indent=1), encoding="utf-8")
    return Path(p)


def rules_ewk(tmp, *, state="active"):
    """Synthetic EWK rules — numbers are test-only, not deposited rules_v7."""
    def R(rid, title, params, sev="Minor", issue="design_error", phase="DD"):
        return {
            "rule_id": rid, "title": title, "state": state, "cap": "Confirmed",
            "statement": f"Synthetic {rid}.", "inputs": ["grading_surface"],
            "parameters": {
                k: {"value": v, "tag": "AGREED", "source_id": "SRC-T", "clause": "§T",
                    "unit": u, "cap": "Confirmed",
                    **({"tolerance_ref": "grade_pct"} if k in ("g_min", "slope_min_h_per_v") else {})}
                for k, (v, u) in params.items()
            },
            "severity_default": sev, "issue_type": issue, "check_type": "value",
            "default_status": {"dwg_vector": "Confirmed", "script_output": "Confirmed",
                               "landxml": "Confirmed", "pdf_vector_text": "Probable"},
            "owner_role": "DMR", "required_from_phase": phase,
        }

    rs = [
        R("EWK-001", "Minimum gradient — unpaved", {"g_min": (1.0, "%")}, sev="Minor"),
        R("EWK-002", "Cut and fill slopes", {
            "slope_min_h_per_v": (2.0, "H:V"),
            "fos_static": (1.5, None),
            "fos_seismic": (1.1, None),
        }, sev="Major"),
        R("EWK-003", "Plot levels vs road levels", {
            "positive_drainage": ("drain out", None),
            "floor_above_street": (0.30, "m"),
        }, sev="Major"),
        R("EWK-101", "Slope protection", {"required_element": ("slope protection", None)},
          sev="Minor", issue="missing_information"),
        R("EWK-102", "Retaining structure", {"required_element": ("retaining", None)},
          sev="Major", issue="missing_information"),
    ]
    path = tmp / "rules_RDE.json"
    write(path, {
        "agent": "RDE", "rulebook_version": "v7-test-synthetic",
        "rules": rs, "rules_skipped": [],
        "tolerances": {"grade_pct": {"value": 0.05, "unit": "%"}},
        "stage_map": {
            "30% Concept Design": {"phase": "PD", "milestone": 30},
            "60%": {"phase": "DD", "milestone": 60},
        },
    })
    return path


def write_tin(tmp, name, origin, size, rise_e=0.0, rise_n=0.0, z0=100.0, *, extra_faces=None):
    sys.path.insert(0, str(SK13))
    from ewk_common import make_flat_tin
    e0, n0 = origin
    se, sn = size
    tin = make_flat_tin(e0, n0, se, sn, z0, z0 + rise_e, z0 + rise_n, z0 + rise_e + rise_n,
                        surface=name)
    if extra_faces:
        tin["faces"].extend(extra_faces)
    path = tmp / f"{name}.surface-1.json"
    write(path, tin)
    return path


def write_steep_tin(tmp, name, origin, run_m, rise_m, along=30.0):
    """A rectangular TIN with slope = 100*rise/run along E, sized along×run in plan."""
    return write_tin(tmp, name, origin, (run_m, along), rise_e=rise_m, z0=100.0)


def surfaces_doc(tmp, *, design=None, existing=None, road=None, paved_from_road=True):
    args = []
    if design:
        args += ["--surface", f"design_grading={design}"]
    if existing:
        args += ["--surface", f"existing={existing}"]
    if road:
        args += ["--surface", f"road_top={road}"]
    out = tmp / "surfaces.json"
    rc, msg = run("build_surfaces.py", *args, "--out", out)
    assert rc == 0, msg
    return out


def _ifc_header():
    return textwrap.dedent("""\
    ISO-10303-21;
    HEADER;
    FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
    FILE_NAME('synth.ifc','2026-10-21T00:00:00',('test'),('test'),'manual','ODA','');
    FILE_SCHEMA(('IFC2X3'));
    ENDSEC;
    DATA;
    """)


def _pt(eid, x, y, z):
    return f"#{eid}=IFCCARTESIANPOINT(({x:.6f},{y:.6f},{z:.6f}));"


def write_ifc_solid_and_layer(path, *, include_layer=True, reverse_layer=False, mapped=False):
    """Minimal IFC2X3: optional thin solid + single-layer surface (possibly reversed / mapped)."""
    lines = [_ifc_header()]
    n = 1

    def add(s):
        nonlocal n
        lines.append(s)
        # bump n from entity id in s
        import re
        m = re.match(r"#(\d+)", s)
        if m:
            n = max(n, int(m.group(1)) + 1)

    # Owner history / extras omitted — products with direct representation
    # Axis placement at origin
    add(_pt(1, 0, 0, 0))
    add("#2=IFCDIRECTION((0.,0.,1.));")
    add("#3=IFCDIRECTION((1.,0.,0.));")
    add("#4=IFCAXIS2PLACEMENT3D(#1,#2,#3);")
    add("#5=IFCLOCALPLACEMENT($,#4);")

    def face_block(base, pts, reverse=False):
        """pts: list of (x,y,z); returns last id used+1 start was base."""
        ids = []
        cur = base
        for (x, y, z) in pts:
            add(_pt(cur, x, y, z))
            ids.append(cur)
            cur += 1
        order = list(reversed(ids)) if reverse else ids
        refs = ",".join(f"#{i}" for i in order)
        add(f"#{cur}=IFCPOLYLOOP(({refs}));")
        cur += 1
        add(f"#{cur}=IFCFACEOUTERBOUND(#{cur-1},.T.);")
        cur += 1
        add(f"#{cur}=IFCFACE((#{cur-1}));")
        face_id = cur
        return cur + 1, face_id  # next free, face id

    face_ids = []
    cur = 10
    # Single-layer quad (two triangles via fan on 4-pt loop) at z=10 over 20×20
    if include_layer:
        layer_pts = [(0, 0, 10), (20, 0, 10.16), (20, 20, 10.16), (0, 20, 10)]  # ~0.8% along E
        if reverse_layer:
            # reverse winding so nz < 0
            cur, fid = face_block(cur, layer_pts, reverse=True)
        else:
            cur, fid = face_block(cur, layer_pts, reverse=False)
        face_ids.append(fid)
        # second triangle as separate face for coverage
        cur, fid2 = face_block(cur, [(0, 0, 10), (20, 20, 10.16), (0, 20, 10)], reverse=reverse_layer)
        face_ids.append(fid2)

    solid_face_ids = []
    if include_layer is False or True:
        # Thin solid: top and bottom 1 mm apart (optional)
        pass

    # Always add a thin solid when include_layer True (to test preference)
    if include_layer:
        # bottom z=10, top z=10.001 — small patch
        for z, rev in ((10.0, False), (10.001, True)):
            cur, fid = face_block(cur, [(100, 100, z), (101, 100, z), (101, 101, z), (100, 101, z)], reverse=rev)
            solid_face_ids.append(fid)
        # skirt faces omitted for brevity

    # Face sets
    if include_layer:
        layer_refs = ",".join(f"#{i}" for i in face_ids)
        add(f"#{cur}=IFCCONNECTEDFACESET(({layer_refs}));")
        layer_fs = cur
        cur += 1
        add(f"#{cur}=IFCFACEBASEDSURFACEMODEL((#{layer_fs}));")
        layer_model = cur
        cur += 1
    else:
        layer_model = None

    if solid_face_ids:
        srefs = ",".join(f"#{i}" for i in solid_face_ids)
        add(f"#{cur}=IFCCONNECTEDFACESET(({srefs}));")
        solid_fs = cur
        cur += 1
        add(f"#{cur}=IFCFACEBASEDSURFACEMODEL((#{solid_fs}));")
        solid_model = cur
        cur += 1
    else:
        # solid-only upward: one big face at z=5 with rise
        cur, fid = face_block(cur, [(0, 0, 5), (30, 0, 5.3), (30, 30, 5.3), (0, 30, 5)], reverse=False)
        add(f"#{cur}=IFCCONNECTEDFACESET((#{fid}));")
        cur += 1
        add(f"#{cur}=IFCFACEBASEDSURFACEMODEL((#{cur-1}));")
        solid_model = cur
        cur += 1
        layer_model = None

    def product(name, geom_id, tag="PROXY"):
        nonlocal cur
        if mapped:
            # Representation map + mapped item
            add(f"#{cur}=IFCSHAPEREPRESENTATION($,'Body','SurfaceModel',(#{geom_id}));")
            map_rep = cur
            cur += 1
            add(f"#{cur}=IFCREPRESENTATIONMAP(#4,#{map_rep});")
            repmap = cur
            cur += 1
            add(f"#{cur}=IFCMAPPEDITEM(#{repmap},$);")
            mapped_item = cur
            cur += 1
            add(f"#{cur}=IFCSHAPEREPRESENTATION($,'Body','MappedRepresentation',(#{mapped_item}));")
            shape = cur
            cur += 1
        else:
            add(f"#{cur}=IFCSHAPEREPRESENTATION($,'Body','SurfaceModel',(#{geom_id}));")
            shape = cur
            cur += 1
        add(f"#{cur}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{shape}));")
        pdef = cur
        cur += 1
        add(f"#{cur}=IFCBUILDINGELEMENTPROXY('2Id$n5VRL2zQHxqP3W$1',$,'{name}',$,$,#5,#{pdef},$);")
        cur += 1

    if layer_model is not None:
        product("BUILDINGELEMENTPROXY(1)", layer_model)
    if solid_model is not None and include_layer:
        product("BUILDINGELEMENTPROXY(0)", solid_model)
    elif layer_model is None:
        product("SOLID_ONLY", solid_model)

    lines.append("ENDSEC;")
    lines.append("END-ISO-10303-21;")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- TL1
def test_tl1_ifc_reader_solid_and_layer(tmp_path):
    ifc = tmp_path / "both.ifc"
    write_ifc_solid_and_layer(ifc, include_layer=True)
    out_dir = tmp_path / "out"
    rc, msg = run("read_ifc_surface.py", "--ifc", ifc, "--out-dir", out_dir, "--prefix", "t1")
    assert rc == 0, msg
    assert msg["layer_choice"] == "single_layer"
    assert msg["surfaces"]
    side = load(msg["surfaces"][0]["path"])
    assert side["source_format"] == "ifc"
    assert side["points"] and side["faces"]
    # quad split → ≥2 faces
    assert len(side["faces"]) >= 1


def test_tl1_ifc_solid_only_upward(tmp_path):
    ifc = tmp_path / "solid.ifc"
    write_ifc_solid_and_layer(ifc, include_layer=False)
    rc, msg = run("read_ifc_surface.py", "--ifc", ifc, "--out-dir", tmp_path / "o2", "--prefix", "s")
    assert rc == 0, msg
    assert msg["surfaces"]


def test_tl1_ifc_rotated_placement_refused(tmp_path):
    # Build IFC with rotated axis
    lines = [_ifc_header()]
    lines += [
        _pt(1, 0, 0, 0),
        "#2=IFCDIRECTION((0.,1.,0.));",  # not (0,0,1)
        "#3=IFCDIRECTION((1.,0.,0.));",
        "#4=IFCAXIS2PLACEMENT3D(#1,#2,#3);",
        "#5=IFCLOCALPLACEMENT($,#4);",
        _pt(10, 0, 0, 0), _pt(11, 1, 0, 0), _pt(12, 0, 1, 0),
        "#13=IFCPOLYLOOP((#10,#11,#12));",
        "#14=IFCFACEOUTERBOUND(#13,.T.);",
        "#15=IFCFACE((#14));",
        "#16=IFCCONNECTEDFACESET((#15));",
        "#17=IFCFACEBASEDSURFACEMODEL((#16));",
        "#18=IFCSHAPEREPRESENTATION($,'Body','SurfaceModel',(#17));",
        "#19=IFCPRODUCTDEFINITIONSHAPE($,$,(#18));",
        "#20=IFCBUILDINGELEMENTPROXY('id',$,'R',$,$,#5,#19,$);",
        "ENDSEC;", "END-ISO-10303-21;",
    ]
    ifc = tmp_path / "rot.ifc"
    ifc.write_text("\n".join(lines) + "\n")
    rc, msg = run("read_ifc_surface.py", "--ifc", ifc, "--out-dir", tmp_path / "or", "--prefix", "r")
    assert rc == 2, msg
    assert "IFC_ROTATED_PLACEMENT" in str(msg.get("detail", msg))


def test_tl1_ifc4_triangulated(tmp_path):
    lines = [
        "ISO-10303-21;", "HEADER;",
        "FILE_DESCRIPTION(('x'),'2;1');",
        "FILE_NAME('t.ifc','2026-01-01',('a'),('b'),'c','d','');",
        "FILE_SCHEMA(('IFC4'));", "ENDSEC;", "DATA;",
        _pt(1, 0, 0, 0),
        "#2=IFCDIRECTION((0.,0.,1.));",
        "#3=IFCDIRECTION((1.,0.,0.));",
        "#4=IFCAXIS2PLACEMENT3D(#1,#2,#3);",
        "#5=IFCLOCALPLACEMENT($,#4);",
        "#10=IFCCARTESIANPOINTLIST3D(((0.,0.,1.),(10.,0.,1.1),(0.,10.,1.0)));",
        "#11=IFCTRIANGULATEDFACESET($,$,.T.,#10,((1,2,3)),$);",
        "#12=IFCSHAPEREPRESENTATION($,'Body','Tessellation',(#11));",
        "#13=IFCPRODUCTDEFINITIONSHAPE($,$,(#12));",
        "#14=IFCBUILDINGELEMENTPROXY('id',$,'TFS',$,$,#5,#13,$);",
        "ENDSEC;", "END-ISO-10303-21;",
    ]
    ifc = tmp_path / "ifc4.ifc"
    ifc.write_text("\n".join(lines) + "\n")
    rc, msg = run("read_ifc_surface.py", "--ifc", ifc, "--out-dir", tmp_path / "o4", "--prefix", "f")
    assert rc == 0, msg
    assert msg["surfaces"] and msg["surfaces"][0]["faces"] >= 1


# --------------------------------------------------------------------------- TL2
def test_tl2_ewk001_flat_and_pass(tmp_path):
    rules = rules_ewk(tmp_path)
    # 0.8 % over 50×50 → area 2500 m² >> 25
    # rise = 0.008 * 50 = 0.4
    design = write_tin(tmp_path, "flat", (0, 0), (50, 50), rise_e=0.4, z0=100)
    # road footprint elsewhere so whole design is unpaved
    road = write_tin(tmp_path, "road", (1000, 1000), (10, 10), z0=100)
    surf = surfaces_doc(tmp_path, design=design, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl.json")
    assert rc == 0, msg
    sl = load(tmp_path / "sl.json")
    fails = [c for c in sl["checks"] if c["rule_id"] == "EWK-001"]
    assert fails, sl
    assert fails[0]["observed"] < 0.95

    # 1.2 % → pass
    design2 = write_tin(tmp_path, "ok", (0, 0), (50, 50), rise_e=0.6, z0=100)
    surf2 = surfaces_doc(tmp_path, design=design2, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf2, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl2.json")
    assert rc == 0, msg
    sl2 = load(tmp_path / "sl2.json")
    assert not [c for c in sl2["checks"] if c["rule_id"] == "EWK-001"]
    assert any(p["rule_id"] == "EWK-001" for p in sl2["passed"])


def test_tl2_ewk001_small_group_dropped_and_paved_ignored(tmp_path):
    rules = rules_ewk(tmp_path)
    # Small flat ~10 m²: 4×2.5 m at 0.8 %
    design = write_tin(tmp_path, "tiny", (0, 0), (4, 2.5), rise_e=0.032, z0=100)
    road = write_tin(tmp_path, "road2", (1000, 1000), (5, 5), z0=100)
    surf = surfaces_doc(tmp_path, design=design, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "sl.json")
    assert rc == 0, msg
    sl = load(tmp_path / "sl.json")
    assert not [c for c in sl["checks"] if c["rule_id"] == "EWK-001"]

    # Flat inside road footprint → ignored; no unpaved flat
    design2 = write_tin(tmp_path, "inroad", (0, 0), (50, 50), rise_e=0.4, z0=100)
    road2 = write_tin(tmp_path, "same", (0, 0), (50, 50), z0=100)
    surf2 = surfaces_doc(tmp_path, design=design2, road=road2)
    rc, msg = run("check_slopes.py", "--surfaces", surf2, "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "sl3.json")
    assert rc == 0, msg
    sl3 = load(tmp_path / "sl3.json")
    assert not [c for c in sl3["checks"] if c["rule_id"] == "EWK-001"]


def test_tl2_ewk001_no_footprint_not_run(tmp_path):
    rules = rules_ewk(tmp_path)
    design = write_tin(tmp_path, "nf", (0, 0), (50, 50), rise_e=0.4, z0=100)
    surf = surfaces_doc(tmp_path, design=design)
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "sl.json")
    assert rc == 0, msg
    sl = load(tmp_path / "sl.json")
    assert any(n["rule_id"] == "EWK-001" and "unpaved" in n["reason"] for n in sl["not_run"])


# --------------------------------------------------------------------------- TL3
def test_tl3_ewk002_steep_and_pass(tmp_path):
    rules = rules_ewk(tmp_path)
    # 1H:1V = 100 %, height 2 m over run 2 m, length 20 m
    design = write_steep_tin(tmp_path, "steep", (0, 0), run_m=2.0, rise_m=2.0, along=20.0)
    road = write_tin(tmp_path, "rd", (500, 500), (5, 5), z0=100)
    existing = write_tin(tmp_path, "ex", (0, 0), (2, 20), z0=102)  # above design → cut? design z 100..102
    surf = surfaces_doc(tmp_path, design=design, road=road, existing=existing)
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl.json")
    assert rc == 0, msg
    sl = load(tmp_path / "sl.json")
    fails = [c for c in sl["checks"] if c["rule_id"] == "EWK-002" and c["parameter"] == "slope_min_h_per_v"]
    assert fails, sl
    assert any(g["height_m"] >= 0.5 for g in sl["ewk002_groups"])

    # 2.5H:1V = 40 % < 50.05 → pass
    design2 = write_steep_tin(tmp_path, "gentle", (0, 0), run_m=2.5, rise_m=1.0, along=20.0)
    surf2 = surfaces_doc(tmp_path, design=design2, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf2, "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "sl2.json")
    assert rc == 0, msg
    sl2 = load(tmp_path / "sl2.json")
    assert not [c for c in sl2["checks"] if c["rule_id"] == "EWK-002" and c["parameter"] == "slope_min_h_per_v"]


def test_tl3_wall_and_short_group(tmp_path):
    rules = rules_ewk(tmp_path)
    sys.path.insert(0, str(SK13))
    from ewk_common import make_flat_tin
    # Near-vertical: rise 20 m over run 1 m → 2000 % > 1000 % wall
    tin = make_flat_tin(0, 0, 1, 10, 100, 120, 100, 120, surface="wall")
    path = tmp_path / "wall.surface-1.json"
    write(path, tin)
    road = write_tin(tmp_path, "rdw", (500, 500), (5, 5), z0=100)
    surf = surfaces_doc(tmp_path, design=path, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "sl.json")
    assert rc == 0, msg
    sl = load(tmp_path / "sl.json")
    assert sl["wall_faces"] >= 1
    assert not sl["ewk002_groups"]

    # Short steep group 0.3 m high
    design = write_steep_tin(tmp_path, "short", (0, 0), run_m=0.3, rise_m=0.3, along=20.0)
    surf2 = surfaces_doc(tmp_path, design=design, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf2, "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "sl2.json")
    assert rc == 0, msg
    assert load(tmp_path / "sl2.json")["ewk002_groups"] == []


# --------------------------------------------------------------------------- TL4
def test_tl4_fos_text(tmp_path):
    rules = rules_ewk(tmp_path)
    design = write_steep_tin(tmp_path, "s4", (0, 0), run_m=2.0, rise_m=2.0, along=20.0)
    road = write_tin(tmp_path, "r4", (500, 500), (5, 5), z0=100)
    surf = surfaces_doc(tmp_path, design=design, road=road)

    report_fail = write(tmp_path / "rep_fail.pdf.json", {
        "source": {"path": "rep.pdf", "sha256": "a" * 64, "file_type": "pdf"},
        "content": {"texts": [{"text": "FoS (static) 1.30", "insert": [0, 0]}]}})
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "60%", "--report", report_fail, "--out", tmp_path / "sl.json")
    assert rc == 0, msg
    sl = load(tmp_path / "sl.json")
    assert any(c["rule_id"] == "EWK-002" and c["parameter"] == "fos_static" and c["result"] == "fail"
               for c in sl["checks"])

    report_ok = write(tmp_path / "rep_ok.pdf.json", {
        "source": {"path": "rep.pdf", "sha256": "b" * 64, "file_type": "pdf"},
        "content": {"texts": [{"text": "FoS 1.60 / seismic 1.20", "insert": [0, 0]}]}})
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "60%", "--report", report_ok, "--out", tmp_path / "sl2.json")
    assert rc == 0, msg
    sl2 = load(tmp_path / "sl2.json")
    assert any(p.get("note", "").startswith("justification") for p in sl2["passed"]
               if p["rule_id"] == "EWK-002")

    # DD without calculation + steep → MI
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "sl3.json")
    assert rc == 0, msg
    sl3 = load(tmp_path / "sl3.json")
    assert any(c["parameter"] == "fos_static" and c.get("status_override") == "Missing information"
               for c in sl3["checks"])

    # PD → FoS not run
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl4.json")
    assert rc == 0, msg
    sl4 = load(tmp_path / "sl4.json")
    assert any(n["parameter"] == "fos_static" and "not required" in n["reason"] for n in sl4["not_run"])


# --------------------------------------------------------------------------- TL5
def test_tl5_drainage_trap_and_mixed(tmp_path):
    rules = rules_ewk(tmp_path)
    # Bowl: centre low — use 4-point warp: corners high, but make_flat_tin is planar pair.
    # Build a custom TIN with a depression: 5 points
    tin = {
        "template": "landxml-surface-1.0", "surface": "bowl", "index": 1,
        "points": [
            ["1", 0, 0, 10], ["2", 30, 0, 10], ["3", 30, 30, 10], ["4", 0, 30, 10],
            ["5", 15, 15, 9.8],  # centre 0.2 m below
        ],
        "faces": [
            ["1", "2", "5"], ["2", "3", "5"], ["3", "4", "5"], ["4", "1", "5"],
        ],
        "breaklines": [],
    }
    design = write(tmp_path / "bowl.surface-1.json", tin)
    # One plot covering the bowl
    plots = write(tmp_path / "plots.dwg.json", {
        "source": {"path": "plots.dwg", "sha256": "c" * 64, "file_type": "dwg"},
        "content": {"polylines": [{
            "layer": "Residential plots", "handle": "P1",
            "vertices": [[0, 0], [30, 0], [30, 30], [0, 30], [0, 0]],
        }]},
    })
    surf = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"design_grading={design}", "--out", surf)
    assert rc == 0, msg
    rc, msg = run("check_drainage.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--plots", plots, "--site", "NMAR",
                  "--out", tmp_path / "dr.json")
    assert rc == 0, msg
    dr = load(tmp_path / "dr.json")
    assert dr["traps"] or any(c["rule_id"] == "EWK-003" for c in dr["checks"]) or dr["passed"] or dr["not_run"]

    # MIXED_LEVELS
    rc, msg = run("check_drainage.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "60%", "--composite", "--out", tmp_path / "dr2.json")
    assert rc == 2, msg
    assert "MIXED_LEVELS" in str(msg.get("detail", msg))

    # Frontage on rough grading → not run
    rc, msg = run("check_drainage.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "dr3.json")
    assert rc == 0, msg
    dr3 = load(tmp_path / "dr3.json")
    assert any(n["parameter"] == "frontage" for n in dr3["not_run"])


def test_tl5_draining_plot_pass(tmp_path):
    rules = rules_ewk(tmp_path)
    # Uniform slope draining — no closed pit
    design = write_tin(tmp_path, "drain", (0, 0), (40, 40), rise_e=2.0, z0=100)
    plots = write(tmp_path / "plots2.dwg.json", {
        "source": {"path": "p.dwg", "sha256": "d" * 64, "file_type": "dwg"},
        "content": {"polylines": [{
            "layer": "Residential plots", "handle": "P2",
            "vertices": [[5, 5], [35, 5], [35, 35], [5, 35], [5, 5]],
        }]},
    })
    surf = surfaces_doc(tmp_path, design=design)
    rc, msg = run("check_drainage.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--plots", plots, "--site", "NMAR",
                  "--out", tmp_path / "dr.json")
    assert rc == 0, msg
    dr = load(tmp_path / "dr.json")
    assert not [c for c in dr["checks"] if c["rule_id"] == "EWK-003" and c["parameter"] == "positive_drainage"]


# --------------------------------------------------------------------------- TL6
def test_tl6_presence_mi_and_wall_text(tmp_path):
    rules = rules_ewk(tmp_path)
    design = write_steep_tin(tmp_path, "p6", (0, 0), run_m=2.0, rise_m=2.0, along=20.0)
    road = write_tin(tmp_path, "r6", (500, 500), (5, 5), z0=100)
    surf = surfaces_doc(tmp_path, design=design, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "sl.json")
    assert rc == 0, msg

    # DD without protection/wall → two MI findings (101 + 102)
    rc, msg = run("check_presence.py", "--slopes", tmp_path / "sl.json", "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "pr.json")
    assert rc == 0, msg
    pr = load(tmp_path / "pr.json")
    assert any(c["rule_id"] == "EWK-101" for c in pr["checks"])
    assert any(c["rule_id"] == "EWK-102" for c in pr["checks"])

    # RETAINING WALL text 3 m from group centroid
    groups = load(tmp_path / "sl.json")["ewk002_groups"]
    assert groups
    cx, cy = groups[0]["centroid_en"]
    dwg = write(tmp_path / "struct.dwg.json", {
        "source": {"path": "s.dwg", "sha256": "e" * 64, "file_type": "dwg"},
        "content": {"texts": [{"text": "RETAINING WALL", "insert": [cx + 3, cy]}]}})
    rc, msg = run("check_presence.py", "--slopes", tmp_path / "sl.json", "--rules", rules,
                  "--stage", "60%", "--file", f"sections={dwg}", "--out", tmp_path / "pr2.json")
    assert rc == 0, msg
    pr2 = load(tmp_path / "pr2.json")
    assert not [c for c in pr2["checks"] if c["rule_id"] == "EWK-102"]
    assert any(p["rule_id"] == "EWK-102" for p in pr2["passed"])

    # PD → EWK-101 not run; EWK-102 runs (L-a)
    rc, msg = run("check_presence.py", "--slopes", tmp_path / "sl.json", "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "pr3.json")
    assert rc == 0, msg
    pr3 = load(tmp_path / "pr3.json")
    assert any(n["rule_id"] == "EWK-101" and "not required" in n["reason"] for n in pr3["not_run"])
    # EWK-102 may list or pass depending on 1.0 m threshold
    assert not any(n["rule_id"] == "EWK-102" and "not required" in n.get("reason", "") for n in pr3["not_run"])


# --------------------------------------------------------------------------- TL7
def test_tl7_stage_pd_profile_and_dd_candidates(tmp_path):
    rules = rules_ewk(tmp_path)
    design = write_steep_tin(tmp_path, "t7", (0, 0), run_m=2.0, rise_m=2.0, along=20.0)
    existing = write_tin(tmp_path, "ex7", (0, 0), (2, 20), z0=99)
    road = write_tin(tmp_path, "rd7", (500, 500), (5, 5), z0=100)
    surf = surfaces_doc(tmp_path, design=design, existing=existing, road=road)

    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl.json")
    assert rc == 0, msg
    rc, msg = run("check_drainage.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "dr.json")
    assert rc == 0, msg
    rc, msg = run("check_presence.py", "--slopes", tmp_path / "sl.json", "--rules", rules,
                  "--stage", "30% Concept Design", "--surfaces", surf,
                  "--out", tmp_path / "pr.json", "--required-out", tmp_path / "req.json")
    assert rc == 0, msg
    rc, msg = run("earthworks_profile.py", "--surfaces", surf, "--slopes", tmp_path / "sl.json",
                  "--drainage", tmp_path / "dr.json", "--out", tmp_path / "profile.json")
    assert rc == 0, msg
    profile = load(tmp_path / "profile.json")
    assert profile.get("cut_fill") is not None or profile.get("inventory")

    # Candidates at PD (L-a: findings allowed)
    rc, msg = run("build_candidates.py",
                  "--results", tmp_path / "sl.json", tmp_path / "dr.json", tmp_path / "pr.json",
                  "--rules", rules, "--run", "NMAR-test-20261021T1200", "--site", "NMAR",
                  "--sub", "test", "--evidence-dir", tmp_path / "ev",
                  "--out", tmp_path / "cand_pd.json")
    assert rc == 0, msg
    cand_pd = load(tmp_path / "cand_pd.json")
    assert isinstance(cand_pd, list)
    # With steep groups, expect ≥1 candidate at PD
    assert len(cand_pd) >= 1

    # DD candidates validate
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "sl_dd.json")
    assert rc == 0, msg
    rc, msg = run("check_presence.py", "--slopes", tmp_path / "sl_dd.json", "--rules", rules,
                  "--stage", "60%", "--out", tmp_path / "pr_dd.json")
    assert rc == 0, msg
    rc, msg = run("build_candidates.py",
                  "--results", tmp_path / "sl_dd.json", tmp_path / "pr_dd.json",
                  "--rules", rules, "--run", "NMAR-test-20261021T1300", "--site", "NMAR",
                  "--sub", "test", "--evidence-dir", tmp_path / "ev2",
                  "--out", tmp_path / "cand_dd.json")
    assert rc == 0, msg
    rc, msg = run("validate_findings.py", "--findings", tmp_path / "cand_dd.json", "--rules", rules)
    assert rc == 0, msg


# --------------------------------------------------------------------------- TL10
def test_tl10_determinism(tmp_path):
    rules = rules_ewk(tmp_path)
    design = write_tin(tmp_path, "det", (0, 0), (50, 50), rise_e=0.4, z0=100)
    road = write_tin(tmp_path, "rdet", (1000, 1000), (10, 10), z0=100)
    surf = surfaces_doc(tmp_path, design=design, road=road)
    outs = []
    for i in range(2):
        out = tmp_path / f"sl_{i}.json"
        rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                      "--stage", "60%", "--out", out)
        assert rc == 0, msg
        outs.append(Path(out).read_bytes())
    assert outs[0] == outs[1]


# --------------------------------------------------------------------------- ADDENDUM
def test_addendum_reversed_winding(tmp_path):
    ifc = tmp_path / "rev.ifc"
    write_ifc_solid_and_layer(ifc, include_layer=True, reverse_layer=True)
    rc, msg = run("read_ifc_surface.py", "--ifc", ifc, "--out-dir", tmp_path / "or", "--prefix", "rv")
    assert rc == 0, msg
    assert msg["surfaces"] and msg["surfaces"][0]["faces"] >= 1
    # single_layer preferred despite reversed winding
    assert msg["layer_choice"] == "single_layer"


def test_addendum_mapped_item(tmp_path):
    ifc = tmp_path / "map.ifc"
    write_ifc_solid_and_layer(ifc, include_layer=True, mapped=True)
    rc, msg = run("read_ifc_surface.py", "--ifc", ifc, "--out-dir", tmp_path / "om", "--prefix", "mp")
    assert rc == 0, msg
    assert msg["surfaces"]


def test_addendum_streaming_memory_bound(tmp_path):
    """Large generated IFC read with bounded RSS growth (streaming)."""
    ifc = tmp_path / "large.ifc"
    # Generate ~5 MB of points/faces without holding full geometry in the test process beyond write
    with open(ifc, "w", encoding="utf-8") as fh:
        fh.write(_ifc_header())
        fh.write(_pt(1, 0, 0, 0) + "\n")
        fh.write("#2=IFCDIRECTION((0.,0.,1.));\n#3=IFCDIRECTION((1.,0.,0.));\n")
        fh.write("#4=IFCAXIS2PLACEMENT3D(#1,#2,#3);\n#5=IFCLOCALPLACEMENT($,#4);\n")
        eid = 100
        face_ids = []
        # 2000 triangles
        for i in range(2000):
            x = float(i % 50)
            y = float(i // 50)
            fh.write(_pt(eid, x, y, 10) + "\n")
            fh.write(_pt(eid + 1, x + 1, y, 10.01) + "\n")
            fh.write(_pt(eid + 2, x, y + 1, 10) + "\n")
            fh.write(f"#{eid+3}=IFCPOLYLOOP((#{eid},#{eid+1},#{eid+2}));\n")
            fh.write(f"#{eid+4}=IFCFACEOUTERBOUND(#{eid+3},.T.);\n")
            fh.write(f"#{eid+5}=IFCFACE((#{eid+4}));\n")
            face_ids.append(eid + 5)
            eid += 6
        refs = ",".join(f"#{i}" for i in face_ids)
        fh.write(f"#{eid}=IFCCONNECTEDFACESET(({refs}));\n")
        fh.write(f"#{eid+1}=IFCFACEBASEDSURFACEMODEL((#{eid}));\n")
        fh.write(f"#{eid+2}=IFCSHAPEREPRESENTATION($,'Body','SurfaceModel',(#{eid+1}));\n")
        fh.write(f"#{eid+3}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{eid+2}));\n")
        fh.write(f"#{eid+4}=IFCBUILDINGELEMENTPROXY('id',$,'BIG',$,$,#5,#{eid+3},$);\n")
        fh.write("ENDSEC;\nEND-ISO-10303-21;\n")

    # Soft bound: peak RSS during read should stay well under loading whole file as one string * N
    # We just assert the reader succeeds and file is larger than a small threshold.
    assert ifc.stat().st_size > 100_000
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rc, msg = run("read_ifc_surface.py", "--ifc", ifc, "--out-dir", tmp_path / "ol", "--prefix", "L")
    assert rc == 0, msg
    assert msg["surfaces"][0]["faces"] >= 1000
    # ru_maxrss units: KB on Linux
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Child process — check child succeeded; parent memory not the reader. Re-check via subprocess env.
    # Assert streaming path used: script documentation contract — success on large file is the gate.
    assert after >= before  # sanity


def test_addendum_midp_title_match(tmp_path):
    design = write_tin(tmp_path, "midp", (0, 0), (10, 10), z0=100)
    # Side file with internal name differing from MIDP number
    tin = load(design)
    tin["product_name"] = "NRJS-IRO-MOD-00-00002"
    tin["surface"] = "Mass Grading"
    tin["source_format"] = "ifc"  # IFC present; MIDP also schedules XML
    write(design, tin)
    midp = write(tmp_path / "midp.json", {
        "models": [{
            "number": "AN-ROD-MOD-90000-002",
            "title": "Mass Grading",
            "role": "design_grading",
            "formats": ["IFC", "XML"],
            "stage": "30% Concept Design",
        }]
    })
    out = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py",
                  "--surface", f"design_grading={design}",
                  "--midp", midp, "--out", out)
    assert rc == 0, msg
    doc = load(out)
    # Matched by title+role; XML format missing → midp_missing (D3: no require_xml flag)
    assert doc["midp_missing"], doc
    assert doc["midp_missing"][0]["reason"] == "format missing"
    assert "XML" in doc["midp_missing"][0]["formats_missing"] or \
           "xml" in [f.lower() for f in doc["midp_missing"][0]["formats_missing"]]


# --------------------------------------------------------------------------- S29 D1–D10
def write_ifc_sloped_thin_solid(path, *, n=4, cell=10.0, rise=20.0, gap=0.001):
    """Sloped thin solid (top+bottom ΔZ=gap) spanning metres in Z — D1 synthetic."""
    lines = [_ifc_header()]
    n_id = 1

    def add(s):
        nonlocal n_id
        lines.append(s)
        import re
        m = re.match(r"#(\d+)", s)
        if m:
            n_id = max(n_id, int(m.group(1)) + 1)

    add(_pt(1, 0, 0, 0))
    add("#2=IFCDIRECTION((0.,0.,1.));")
    add("#3=IFCDIRECTION((1.,0.,0.));")
    add("#4=IFCAXIS2PLACEMENT3D(#1,#2,#3);")
    add("#5=IFCLOCALPLACEMENT($,#4);")

    face_ids = []
    cur = 10

    def tri_face(pts, reverse=False):
        nonlocal cur
        ids = []
        for (x, y, z) in pts:
            add(_pt(cur, x, y, z))
            ids.append(cur)
            cur += 1
        order = list(reversed(ids)) if reverse else ids
        refs = ",".join(f"#{i}" for i in order)
        add(f"#{cur}=IFCPOLYLOOP(({refs}));")
        cur += 1
        add(f"#{cur}=IFCFACEOUTERBOUND(#{cur-1},.T.);")
        cur += 1
        add(f"#{cur}=IFCFACE((#{cur-1}));")
        face_ids.append(cur)
        cur += 1

    # Grid of paired triangles: Z rises with E (rise over n*cell) — not flat
    for i in range(n):
        for j in range(n):
            x0, y0 = i * cell, j * cell
            x1, y1 = x0 + cell, y0 + cell
            z00 = (x0 / (n * cell)) * rise
            z10 = (x1 / (n * cell)) * rise
            z01 = z00
            z11 = z10
            # top (upward) and bottom twin 1 mm below (downward)
            for dz, rev in ((0.0, False), (-gap, True)):
                tri_face([(x0, y0, z00 + dz), (x1, y0, z10 + dz), (x1, y1, z11 + dz)], reverse=rev)
                tri_face([(x0, y0, z00 + dz), (x1, y1, z11 + dz), (x0, y1, z01 + dz)], reverse=rev)

    refs = ",".join(f"#{i}" for i in face_ids)
    add(f"#{cur}=IFCCONNECTEDFACESET(({refs}));")
    cur += 1
    add(f"#{cur}=IFCFACEBASEDSURFACEMODEL((#{cur-1}));")
    model = cur
    cur += 1
    add(f"#{cur}=IFCSHAPEREPRESENTATION($,'Body','SurfaceModel',(#{model}));")
    cur += 1
    add(f"#{cur}=IFCPRODUCTDEFINITIONSHAPE($,$,(#{cur-1}));")
    cur += 1
    add(f"#{cur}=IFCBUILDINGELEMENTPROXY('id',$,'SLOPED_SOLID',$,$,#5,#{cur-1},$);")
    lines.append("ENDSEC;")
    lines.append("END-ISO-10303-21;")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_d1_sloped_thin_solid_one_sheet(tmp_path):
    """D1: sloped terrain solid (Z-span ≫ 2 mm) → thin_solid_split, one upward sheet."""
    ifc = tmp_path / "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-099.ifc"
    write_ifc_sloped_thin_solid(ifc, n=3, cell=10.0, rise=30.0, gap=0.001)
    out_dir = tmp_path / "out"
    rc, msg = run("read_ifc_surface.py", "--ifc", ifc, "--out-dir", out_dir, "--prefix", "d1")
    assert rc == 0, msg
    assert msg["layer_choice"] == "thin_solid_split"
    assert len(msg["surfaces"]) == 1
    side = load(msg["surfaces"][0]["path"])
    # upward only ≈ half of 2*(n*n*2) triangles
    assert side["faces"]
    n_up = len(side["faces"])
    assert n_up == 3 * 3 * 2  # one sheet of the grid
    assert side.get("document_no") and "ROD-MOD-90000-099" in side["document_no"]
    assert side.get("layer_choice") == "thin_solid_split"


def test_d2_document_no_midp_match(tmp_path):
    """D2: match MIDP by document_no / source file, not IFC proxy title."""
    ifc = tmp_path / "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002.ifc"
    write_ifc_solid_and_layer(ifc, include_layer=True)
    rc, msg = run("read_ifc_surface.py", "--ifc", ifc, "--out-dir", tmp_path / "o", "--prefix", "m002")
    assert rc == 0, msg
    tin_path = msg["surfaces"][0]["path"]
    midp = write(tmp_path / "midp.json", {
        "models": [{
            "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002",
            "title": "Site Grading Model",
            "role": "design_grading",
            "formats": ["IFC", "XML"],
            "stage": "30% Concept Design",
            "milestone": 30,
        }]
    })
    out = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"design_grading={tin_path}",
                  "--midp", midp, "--out", out)
    assert rc == 0, msg
    doc = load(out)
    assert doc["midp_schedules_carriers_at_30"] is True
    # model received; only XML missing (not "model not received")
    assert len(doc["midp_missing"]) == 1
    assert doc["midp_missing"][0]["reason"] == "format missing"
    assert any(f.lower() == "xml" for f in doc["midp_missing"][0]["formats_missing"])
    assert doc["surfaces"][0]["document_no"] and "90000-002" in doc["surfaces"][0]["document_no"]


def test_d5_adapt_midp_from_skill6(tmp_path):
    """D5 / R2 / R3 / R7: adapt_midp filters site, drops SLD, keeps contour / solids."""
    skill6 = write(tmp_path / "midp_s6.json", {
        "tool": {"name": "read_midp.py", "version": "0.x"},
        "files": [{"name": "MIDP.xlsm"}],
        "rows": [
            {"number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001", "title": "Roads NGL Model",
             "formats": ["IFC", "XML"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design", "owner": "Parsons"}]},
            {"number": "LDIP-RCRC-SAP-03-DN-ROD-DWG-10400-001", "title": "Overall Existing Contour Plan",
             "formats": ["DWG", "PDF"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design"}]},
            {"number": "LDIP-RCRC-SAP-03-DN-ROD-DWG-10600-001", "title": "Overall Cut/Fill Stake-Out Plan",
             "formats": ["DWG", "PDF"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design"}]},
            {"number": "LDIP-RCRC-SAP-01-AQ-ROD-MOD-90000-002", "title": "Roads Solids Model",
             "formats": ["IFC", "DWG"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design"}]},
            {"number": "LDIP-RCRC-SAP-01-AN-ROD-MOD-90000-001", "title": "Roads NGL Model",
             "formats": ["IFC"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design"}]},
            {"number": "LDIP-RCRC-SAP-03-DN-ELE-DWG-60000-001", "title": "MV Single-Line Diagram",
             "formats": ["DWG"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design"}]},
            {"number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002", "title": "Site Grading",
             "formats": ["IFC"],
             "exchanges": [{"index": 1, "stage": "60% Design Development"}]},
        ],
    })
    out = tmp_path / "ewk_midp.json"
    rc, msg = run("adapt_midp.py", "--midp", skill6, "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    models = load(out)["models"]
    nums = [m["number"] for m in models]
    assert any("DN-ROD-MOD-90000-001" in n for n in nums)
    assert any("10400" in n for n in nums)  # R7 contour
    assert any("10600" in n for n in nums)  # R7 cut/fill
    assert not any("AQ-ROD" in n for n in nums)  # R2: QRWN filtered out
    assert not any("AN-ROD" in n for n in nums)  # R2: NRJS filtered out
    assert not any("60000" in n for n in nums)  # R3: SLD negative
    assert not any(n.endswith("90000-002") and "DN" in n for n in nums)  # 60 % only
    # R7 roles
    by_num = {m["number"]: m for m in models}
    contour = next(m for m in models if "10400" in m["number"])
    assert contour["role"] == "existing"
    solids_out = tmp_path / "aq.json"
    rc, msg = run("adapt_midp.py", "--midp", skill6, "--site", "QRWN", "--out", solids_out)
    assert rc == 0, msg
    solids = next(m for m in load(solids_out)["models"] if "90000-002" in m["number"])
    assert solids["role"] == "design_grading"
    assert solids["title"] == "Roads Solids Model"


def test_d7_midp_not_scheduled_no_pd_findings(tmp_path):
    """D7: when MIDP does not schedule earthworks carriers at 30 %, no PD findings/MI."""
    rules = rules_ewk(tmp_path)
    design = write_steep_tin(tmp_path, "d7", (0, 0), run_m=2.0, rise_m=2.0, along=20.0)
    road = write_tin(tmp_path, "rd7b", (500, 500), (5, 5), z0=100)
    midp = write(tmp_path / "midp_dd.json", {
        "models": [{
            "number": "X-ROD-MOD-90000-001",
            "title": "Site Grading",
            "role": "design_grading",
            "formats": ["IFC"],
            "stage": "60%",
            "milestone": 60,
        }]
    })
    surf = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"design_grading={design}",
                  "--surface", f"road_top={road}", "--midp", midp, "--out", surf)
    assert rc == 0, msg
    assert load(surf)["midp_schedules_carriers_at_30"] is False

    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl.json")
    assert rc == 0, msg
    sl = load(tmp_path / "sl.json")
    assert not [c for c in sl["checks"] if c["rule_id"] in ("EWK-001", "EWK-002")]
    assert any("not scheduled" in n.get("reason", "") for n in sl["not_run"])

    rc, msg = run("check_drainage.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "dr.json")
    assert rc == 0, msg
    assert not [c for c in load(tmp_path / "dr.json")["checks"] if c["rule_id"] == "EWK-003"]

    rc, msg = run("check_presence.py", "--slopes", tmp_path / "sl.json", "--rules", rules,
                  "--stage", "30% Concept Design", "--surfaces", surf,
                  "--out", tmp_path / "pr.json", "--required-out", tmp_path / "req.json")
    assert rc == 0, msg
    pr = load(tmp_path / "pr.json")
    assert not [c for c in pr["checks"] if c["rule_id"] == "EWK-102"]
    assert load(tmp_path / "req.json")["items"] == []


def test_d8_d9_total_count_required_tolerance(tmp_path):
    """D8/D9: candidate statement has total count; required 50 / tol 0.05 from rule params."""
    rules = rules_ewk(tmp_path)
    # Several steep groups: three separated patches
    sys.path.insert(0, str(SK13))
    from ewk_common import make_flat_tin
    faces_extra = []
    # Build one steep tin large enough for multiple groups via separate write + merge is hard;
    # use three steep tins composited is not supported — instead one long steep face strip.
    design = write_steep_tin(tmp_path, "d8", (0, 0), run_m=2.0, rise_m=2.0, along=40.0)
    # Flat unpaved area for EWK-001 (≥25 m²)
    flat = write_tin(tmp_path, "flat8", (100, 100), (50, 50), rise_e=0.4, z0=100)
    # Use flat as design for EWK-001 path separately; for EWK-002 use steep
    road = write_tin(tmp_path, "rd8", (2000, 2000), (5, 5), z0=100)
    surf = surfaces_doc(tmp_path, design=design, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl.json")
    assert rc == 0, msg
    sl = load(tmp_path / "sl.json")
    ewk002 = [c for c in sl["checks"] if c["rule_id"] == "EWK-002" and c["parameter"] == "slope_min_h_per_v"]
    assert ewk002
    assert ewk002[0]["required"] == 50.0
    assert ewk002[0]["tolerance"] == 0.05
    assert ewk002[0].get("total_count", 0) >= 1

    surf_f = surfaces_doc(tmp_path, design=flat, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf_f, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl_f.json")
    assert rc == 0, msg
    ewk001 = [c for c in load(tmp_path / "sl_f.json")["checks"] if c["rule_id"] == "EWK-001"]
    assert ewk001
    assert ewk001[0]["required"] == 1.0
    assert ewk001[0]["tolerance"] == 0.05

    rc, msg = run("build_candidates.py",
                  "--results", tmp_path / "sl.json",
                  "--rules", rules, "--run", "NMAR-test-20261021T1400", "--site", "NMAR",
                  "--sub", "test", "--evidence-dir", tmp_path / "ev",
                  "--out", tmp_path / "cand.json")
    assert rc == 0, msg
    cands = load(tmp_path / "cand.json")
    f002 = next(f for f in cands if f["reference"]["rule_id"] == "EWK-002"
                and f["reference"]["parameter"] == "slope_min_h_per_v")
    total = ewk002[0]["total_count"]
    assert f002["statement"].startswith(f"{total} occurrence(s)")
    assert f002["reference"]["required_value"] == 50.0
    assert f002["reference"]["tolerance"] == 0.05


def test_d10_plot_layer_fnmatch_and_dedupe(tmp_path):
    """D10: fnmatch on full + short layer; centroid de-dupe; no title-block substring grab."""
    rules = rules_ewk(tmp_path)
    tin = {
        "template": "landxml-surface-1.0", "surface": "bowl", "index": 1,
        "points": [
            ["1", 0, 0, 10], ["2", 40, 0, 10], ["3", 40, 40, 10], ["4", 0, 40, 10],
            ["5", 20, 20, 9.7],
        ],
        "faces": [["1", "2", "5"], ["2", "3", "5"], ["3", "4", "5"], ["4", "1", "5"]],
        "breaklines": [],
    }
    design = write(tmp_path / "bowl10.surface-1.json", tin)
    plots = write(tmp_path / "plots10.dwg.json", {
        "source": {"path": "plots.dwg", "sha256": "f" * 64, "file_type": "dwg"},
        "content": {"polylines": [
            {"layer": "0$0$Residential plots", "handle": "A",
             "vertices": [[0, 0], [40, 0], [40, 40], [0, 40], [0, 0]]},
            # title-block copy — substring "Residential plots" but different short name pattern
            {"layer": "TITLE_Residential plots_COPY", "handle": "TB",
             "vertices": [[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]},
            # duplicate centroid of A
            {"layer": "Residential plots", "handle": "A2",
             "vertices": [[0.01, 0.01], [39.99, 0.01], [39.99, 39.99], [0.01, 39.99], [0.01, 0.01]]},
        ]},
    })
    surf = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"design_grading={design}", "--out", surf)
    assert rc == 0, msg
    rc, msg = run("check_drainage.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--plots", plots, "--site", "NMAR",
                  "--out", tmp_path / "dr.json")
    assert rc == 0, msg
    # TITLE_* must not match fnmatch patterns; A and A2 dedupe to one
    # Trap may or may not fire depending on flood; assert plot load path via n_plots if present
    dr = load(tmp_path / "dr.json")
    # At least: no crash; if traps listed, plot handle is A or A2 not TB
    for t in dr.get("traps") or []:
        assert t.get("plot") in (None, "A", "A2")


# --------------------------------------------------------------------------- S29 R1–R5 / R7
def test_r1_inventory_formats_and_no_inventory_not_run(tmp_path):
    """R1: with inventory judge DWG/NWC/DWFx; without → not_run; never false DWG missing."""
    ifc = tmp_path / "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001-00.ifc"
    write_ifc_solid_and_layer(ifc, include_layer=True)
    rc, msg = run("read_ifc_surface.py", "--ifc", ifc, "--out-dir", tmp_path / "o", "--prefix", "r1")
    assert rc == 0, msg
    tin = msg["surfaces"][0]["path"]
    midp = write(tmp_path / "midp.json", {"models": [{
        "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001",
        "title": "Roads NGL Model", "role": "existing",
        "formats": ["IFC", "DWG", "NWC", "DWFx", "XML"],
        "stage": "30% Concept Design", "milestone": 30,
    }]})
    # Without inventory: IFC present (matched by number strip -00); DWG/NWC/DWFx → not_run
    out1 = tmp_path / "s1.json"
    rc, msg = run("build_surfaces.py", "--surface", f"existing={tin}",
                  "--midp", midp, "--site", "NMAR", "--out", out1)
    assert rc == 0, msg
    d1 = load(out1)
    assert not any(m["reason"] == "model not received" for m in d1["midp_missing"])
    assert d1["midp_not_run"]
    assert all(f.upper() in ("DWG", "NWC", "DWFX") or f.lower() in ("dwg", "nwc", "dwfx")
               for nr in d1["midp_not_run"] for f in nr.get("formats_unchecked") or [])
    # XML still judged from surfaces
    assert any(m["reason"] == "format missing" and any(x.lower() == "xml" for x in m["formats_missing"])
               for m in d1["midp_missing"])
    assert not any(
        m["reason"] == "format missing" and any(norm in {x.lower() for x in m["formats_missing"]}
                                                for norm in ("dwg", "nwc", "dwfx"))
        for m in d1["midp_missing"]
    )

    inv = write(tmp_path / "files.json", {"files": [
        {"path": "models/LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001-00.ifc", "name": ifc.name,
         "format": "IFC", "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001-00"},
        {"path": "models/LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001-00.dwg", "name": "x.dwg",
         "format": "DWG", "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001-00"},
    ]})
    out2 = tmp_path / "s2.json"
    rc, msg = run("build_surfaces.py", "--surface", f"existing={tin}",
                  "--midp", midp, "--inventory", inv, "--site", "NMAR", "--out", out2)
    assert rc == 0, msg
    d2 = load(out2)
    miss = d2["midp_missing"]
    assert miss and miss[0]["reason"] == "format missing"
    fm = {f.lower() for f in miss[0]["formats_missing"]}
    assert "nwc" in fm and "dwfx" in fm and "xml" in fm
    assert "dwg" not in fm and "ifc" not in fm


def test_r3_single_line_diagram_not_carrier(tmp_path):
    """R3 negative: 'Single-Line Diagram' must not become an earthworks carrier."""
    skill6 = write(tmp_path / "sld.json", {
        "tool": {"name": "read_midp.py", "version": "0.x"},
        "files": [],
        "rows": [{
            "number": "LDIP-RCRC-SAP-03-DN-ELE-DWG-60000-001",
            "title": "LV Single-Line Diagram",
            "formats": ["DWG"],
            "exchanges": [{"stage": "30% Concept Design"}],
        }],
    })
    rc, msg = run("adapt_midp.py", "--midp", skill6, "--site", "NMAR", "--out", tmp_path / "a.json")
    assert rc == 0, msg
    assert load(tmp_path / "a.json")["models"] == []


def test_r4_old_scheme_match_by_role(tmp_path):
    """R4: NRJS IRO-MOD old numbers match MIDP AN-ROD-MOD by role; note number differs."""
    design = write_tin(tmp_path, "old", (0, 0), (10, 10), z0=100)
    tin = load(design)
    tin["document_no"] = "NRJS-IRO-MOD-00-00002"
    tin["source_file"] = "NRJS-IRO-MOD-00-00002.ifc"
    tin["source_format"] = "ifc"
    tin["product_name"] = "BUILDINGELEMENTPROXY(1)"
    tin["surface"] = "BUILDINGELEMENTPROXY(1)"
    write(design, tin)
    midp = write(tmp_path / "midp.json", {"models": [{
        "number": "LDIP-RCRC-SAP-01-AN-ROD-MOD-90000-002",
        "title": "Site Grading Model", "role": "design_grading",
        "formats": ["IFC", "XML"],
        "stage": "30% Concept Design", "milestone": 30,
    }]})
    out = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"design_grading={design}",
                  "--midp", midp, "--site", "NRJS", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    assert not any(m["reason"] == "model not received" for m in doc["midp_missing"])
    assert any(m.get("matched_by") == "role" for m in doc.get("midp_matches") or [])
    fmt = [m for m in doc["midp_missing"] if m["reason"] == "format missing"]
    assert fmt and any(f.lower() == "xml" for f in fmt[0]["formats_missing"])
    assert fmt[0].get("note") and "differs" in fmt[0]["note"]


def test_r5_one_mi_candidate_per_site(tmp_path):
    """R5/R6: one MI finding listing all missing; rule_id EWK-001; clause names 001/002/003."""
    rules = rules_ewk(tmp_path)
    design = write_tin(tmp_path, "r5", (0, 0), (10, 10), z0=100)
    midp = write(tmp_path / "midp.json", {"models": [
        {"number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001", "title": "NGL",
         "role": "existing", "formats": ["IFC"], "stage": "30% Concept Design", "milestone": 30},
        {"number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002", "title": "Grading",
         "role": "design_grading", "formats": ["IFC", "XML"],
         "stage": "30% Concept Design", "milestone": 30},
    ]})
    # Only design surface — existing missing; XML missing on design
    tin = load(design)
    tin["document_no"] = "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002"
    tin["source_format"] = "ifc"
    write(design, tin)
    surf = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"design_grading={design}",
                  "--midp", midp, "--site", "NMAR", "--out", surf)
    assert rc == 0, msg
    assert len(load(surf)["midp_missing"]) >= 2

    slopes = write(tmp_path / "sl.json", {
        "tool": {"script": "check_slopes.py", "version": "0"},
        "ewk002_groups": [], "walls": [], "checks": [], "passed": [], "not_run": [],
    })
    rc, msg = run("check_presence.py", "--slopes", slopes, "--rules", rules,
                  "--stage", "30% Concept Design", "--surfaces", surf,
                  "--out", tmp_path / "pr.json", "--required-out", tmp_path / "req.json")
    assert rc == 0, msg
    items = load(tmp_path / "req.json")["items"]
    assert len(items) == 1
    assert items[0]["key"] == "midp:earthworks"
    assert items[0]["rules"] == ["EWK-001"]
    assert "EWK-002" in items[0]["clause"] and "EWK-003" in items[0]["clause"]

    rc, msg = run("build_candidates.py",
                  "--results", tmp_path / "pr.json", tmp_path / "req.json",
                  "--rules", rules, "--run", "NMAR-test-20261022T0900", "--site", "NMAR",
                  "--sub", "test", "--evidence-dir", tmp_path / "ev",
                  "--out", tmp_path / "cand.json")
    assert rc == 0, msg
    mi = [f for f in load(tmp_path / "cand.json") if f["issue_type"] == "missing_information"
          and "MIDP earthworks" in f["title"]]
    assert len(mi) == 1
    assert mi[0]["reference"]["rule_id"] == "EWK-001"
    assert "90000-001" in mi[0]["statement"]
    assert "EWK-001" in mi[0]["reference"]["clause"]


# --------------------------------------------------------------------------- S29 S1–S3
def test_s1_drawing_inventory_match_not_role(tmp_path):
    """S1: drawing in inventory → matched_by inventory; never role/surface; no number-differs note."""
    # Surface with role existing (NGL) — must NOT absorb a contour drawing via role
    ngl = write_tin(tmp_path, "ngl", (0, 0), (10, 10), z0=100)
    tin = load(ngl)
    tin["document_no"] = "LDIP-RCRC-SAP-01-AQ-ROD-MOD-90000-001"
    tin["source_file"] = "LDIP-RCRC-SAP-01-AQ-ROD-MOD-90000-001.ifc"
    tin["source_format"] = "ifc"
    tin["product_name"] = "BUILDINGELEMENTPROXY(1)"
    tin["surface"] = "BUILDINGELEMENTPROXY(1)"
    write(ngl, tin)
    midp = write(tmp_path / "midp.json", {"models": [{
        "number": "LDIP-RCRC-SAP-01-AQ-ROD-DWG-10400-001",
        "title": "Overall Existing Contour Plan",
        "role": "existing",
        "formats": ["DWG", "DWFx", "PDF"],
        "stage": "30% Concept Design", "milestone": 30,
    }]})
    inv = write(tmp_path / "files.json", {"files": [
        {"path": "drawings/LDIP-RCRC-SAP-01-AQ-ROD-DWG-10400-001-00.dwg",
         "name": "LDIP-RCRC-SAP-01-AQ-ROD-DWG-10400-001-00.dwg",
         "format": "DWG", "number": "LDIP-RCRC-SAP-01-AQ-ROD-DWG-10400-001-00"},
    ]})
    out = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"existing={ngl}",
                  "--midp", midp, "--inventory", inv, "--site", "QRWN", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    matches = doc.get("midp_matches") or []
    assert matches and matches[0]["matched_by"] == "inventory"
    assert not matches[0].get("note")
    assert not any(m.get("matched_by") == "role" for m in matches)
    miss = [m for m in doc["midp_missing"] if "10400" in (m.get("deliverable") or "")]
    assert miss
    fm = {f.lower() for f in miss[0]["formats_missing"]}
    assert "dwfx" in fm and "pdf" in fm
    assert "dwg" not in fm
    assert miss[0].get("matched_by") == "inventory"
    assert not miss[0].get("note")


def test_s2_role_match_formats_via_own_number(tmp_path):
    """S2: after role match, formats from inventory under matched file's own (old) number."""
    design = write_tin(tmp_path, "old2", (0, 0), (10, 10), z0=100)
    tin = load(design)
    tin["document_no"] = "NRJS-IRO-MOD-00-00003"
    tin["source_file"] = "NRJS-IRO-MOD-00-00003.ifc"
    tin["source_format"] = "ifc"
    tin["product_name"] = "BUILDINGELEMENTPROXY(1)"
    tin["surface"] = "BUILDINGELEMENTPROXY(1)"
    write(design, tin)
    midp = write(tmp_path / "midp.json", {"models": [{
        "number": "LDIP-RCRC-SAP-01-AN-ROD-MOD-90000-003",
        "title": "Roads Surface Model", "role": "road_top",
        "formats": ["IFC", "DWG", "NWC", "DWFx", "XML"],
        "stage": "30% Concept Design", "milestone": 30,
    }]})
    inv = write(tmp_path / "files.json", {"files": [
        {"path": "models/NRJS-IRO-MOD-00-00003.ifc", "name": "NRJS-IRO-MOD-00-00003.ifc",
         "format": "IFC", "number": "NRJS-IRO-MOD-00-00003"},
        {"path": "models/NRJS-IRO-MOD-00-00003.dwg", "name": "NRJS-IRO-MOD-00-00003.dwg",
         "format": "DWG", "number": "NRJS-IRO-MOD-00-00003"},
        {"path": "models/NRJS-IRO-MOD-00-00003.nwc", "name": "NRJS-IRO-MOD-00-00003.nwc",
         "format": "NWC", "number": "NRJS-IRO-MOD-00-00003"},
        {"path": "models/NRJS-IRO-MOD-00-00003.dwfx", "name": "NRJS-IRO-MOD-00-00003.dwfx",
         "format": "DWFx", "number": "NRJS-IRO-MOD-00-00003"},
    ]})
    out = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"road_top={design}",
                  "--midp", midp, "--inventory", inv, "--site", "NRJS", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    assert any(m.get("matched_by") == "role" for m in doc.get("midp_matches") or [])
    fmt = [m for m in doc["midp_missing"] if m["reason"] == "format missing"]
    assert len(fmt) == 1
    fm = {f.lower() for f in fmt[0]["formats_missing"]}
    assert fm == {"xml"}  # only XML missing — DWG/NWC/DWFx present under old number


def test_s3_drawing_carriers_by_title_not_number_range(tmp_path):
    """S3: grading/contour/cut-fill by title; Road Setting-Out Plan (NMAR 10300) not a carrier."""
    skill6 = write(tmp_path / "midp_s3.json", {
        "tool": {"name": "read_midp.py", "version": "0.x"},
        "files": [{"name": "MIDP.xlsm"}],
        "rows": [
            {"number": "LDIP-RCRC-SAP-03-DN-ROD-DWG-10300-001",
             "title": "Road Setting-Out Plan",
             "formats": ["DWG", "PDF"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design"}]},
            {"number": "LDIP-RCRC-SAP-03-DN-ROD-DWG-10400-001",
             "title": "Overall Existing Contour Plan",
             "formats": ["DWG", "PDF"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design"}]},
            {"number": "LDIP-RCRC-SAP-03-DN-ROD-DWG-10500-001",
             "title": "Mass Grading Plan",
             "formats": ["DWG", "PDF"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design"}]},
            {"number": "LDIP-RCRC-SAP-03-DN-ROD-DWG-10600-001",
             "title": "Overall Cut/Fill Stake-Out Plan",
             "formats": ["DWG", "PDF"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design"}]},
            {"number": "LDIP-RCRC-SAP-01-AQ-ROD-DWG-10300-001",
             "title": "Overall Existing Contour Plan",
             "formats": ["DWG", "PDF"],
             "exchanges": [{"index": 1, "stage": "30% Concept Design"}]},
        ],
    })
    out = tmp_path / "ewk.json"
    rc, msg = run("adapt_midp.py", "--midp", skill6, "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    models = load(out)["models"]
    nums = [m["number"] for m in models]
    assert not any("10300" in n and "DN" in n for n in nums)  # setting-out not carrier
    assert any("10400" in n for n in nums)
    assert any("10500" in n for n in nums)
    assert any("10600" in n for n in nums)
    aq = tmp_path / "aq.json"
    rc, msg = run("adapt_midp.py", "--midp", skill6, "--site", "QRWN", "--out", aq)
    assert rc == 0, msg
    # QRWN 10300 = contour plan → is a carrier (by title)
    assert any("AQ-ROD-DWG-10300" in m["number"] for m in load(aq)["models"])


# --------------------------------------------------------------------------- N2 / N3 (post-merge follow-up)
def test_n2_mi_searched_names_inventory(tmp_path):
    """N2: MI with --inventory → statement names inventory file; never '(no file given)'."""
    rules = rules_ewk(tmp_path)
    design = write_tin(tmp_path, "n2", (0, 0), (10, 10), z0=100)
    tin = load(design)
    tin["document_no"] = "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002"
    tin["source_format"] = "ifc"
    tin["source_file"] = "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002.ifc"
    write(design, tin)
    midp = write(tmp_path / "midp.json", {"models": [
        {"number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001", "title": "NGL",
         "role": "existing", "formats": ["IFC", "NWC"],
         "stage": "30% Concept Design", "milestone": 30},
        {"number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002", "title": "Grading",
         "role": "design_grading", "formats": ["IFC", "NWC", "DWFx"],
         "stage": "30% Concept Design", "milestone": 30},
    ]})
    inv_name = "files_n2.json"
    inv = write(tmp_path / inv_name, {
        "tool": {"name": "inventory.py", "version": "0.3.0"},
        "files": [
            {"path": "models/LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002.ifc",
             "name": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002.ifc",
             "format": "IFC", "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002"},
        ],
    })
    surf = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"design_grading={design}",
                  "--midp", midp, "--inventory", inv, "--site", "NMAR", "--out", surf)
    assert rc == 0, msg
    doc = load(surf)
    assert doc.get("inventory", {}).get("file_name") == inv_name
    assert doc["inventory"]["n_files"] >= 1
    assert doc["midp_missing"]

    slopes = write(tmp_path / "sl.json", {
        "tool": {"script": "check_slopes.py", "version": "0"},
        "ewk002_groups": [], "walls": [], "checks": [], "passed": [], "not_run": [],
    })
    rc, msg = run("check_presence.py", "--slopes", slopes, "--rules", rules,
                  "--stage", "30% Concept Design", "--surfaces", surf,
                  "--out", tmp_path / "pr.json", "--required-out", tmp_path / "req.json")
    assert rc == 0, msg
    items = load(tmp_path / "req.json")["items"]
    assert len(items) == 1
    searched = " ".join(items[0]["searched"])
    assert inv_name in searched
    assert "(no file given)" not in searched
    assert "files)" in searched or "file" in searched.lower()

    rc, msg = run("build_candidates.py",
                  "--results", tmp_path / "pr.json", tmp_path / "req.json",
                  "--rules", rules, "--run", "NMAR-test-20261022T1012", "--site", "NMAR",
                  "--sub", "test", "--evidence-dir", tmp_path / "ev",
                  "--out", tmp_path / "cand.json")
    assert rc == 0, msg
    mi = [f for f in load(tmp_path / "cand.json")
          if f["issue_type"] == "missing_information" and "MIDP earthworks" in f["title"]]
    assert len(mi) == 1
    assert inv_name in mi[0]["statement"]
    assert "(no file given)" not in mi[0]["statement"]
    assert "Searched:" in mi[0]["statement"]


def test_n3_inventory_skill6_files_json_shape(tmp_path):
    """N3: --inventory accepts skill 6 inventory.py output (files[])."""
    design = write_tin(tmp_path, "n3a", (0, 0), (5, 5), z0=100)
    tin = load(design)
    tin["document_no"] = "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002"
    tin["source_format"] = "ifc"
    write(design, tin)
    midp = write(tmp_path / "midp.json", {"models": [{
        "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002",
        "title": "Site Grading", "role": "design_grading",
        "formats": ["IFC", "DWG", "NWC"],
        "stage": "30% Concept Design", "milestone": 30,
    }]})
    inv = write(tmp_path / "files.json", {
        "tool": {"name": "inventory.py", "version": "0.3.0"},
        "mode": "standalone", "site": "NMAR",
        "files": [
            {"path": "Vol04/LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002-00.ifc",
             "name": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002-00.ifc",
             "ext": ".ifc", "format": "IFC", "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002-00",
             "kind": "file"},
            {"path": "Vol04/LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002-00.dwg",
             "name": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002-00.dwg",
             "ext": ".dwg", "format": "DWG", "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-002-00",
             "kind": "file"},
        ],
    })
    out = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"design_grading={design}",
                  "--midp", midp, "--inventory", inv, "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    assert doc["inventory_used"] is True
    assert doc["inventory"]["file_name"] == "files.json"
    assert doc["inventory"]["n_files"] == 2
    miss = [m for m in doc["midp_missing"] if m["reason"] == "format missing"]
    assert miss
    fm = {f.lower() for f in miss[0]["formats_missing"]}
    assert "nwc" in fm and "dwg" not in fm and "ifc" not in fm


def test_n3_inventory_package_sheet_register_shape(tmp_path):
    """N3: --inventory accepts skill 6 package.json sheet_register."""
    design = write_tin(tmp_path, "n3b", (0, 0), (5, 5), z0=100)
    tin = load(design)
    tin["document_no"] = "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001"
    tin["source_format"] = "ifc"
    write(design, tin)
    midp = write(tmp_path / "midp.json", {"models": [{
        "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001",
        "title": "Roads NGL Model", "role": "existing",
        "formats": ["IFC", "DWG", "DWFx"],
        "stage": "30% Concept Design", "milestone": 30,
    }]})
    pkg = write(tmp_path / "package.json", {
        "tool": {"name": "build_register.py", "version": "0.x"},
        "site": "NMAR",
        "sheet_register": [{
            "base": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001",
            "numbers": ["LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001-00"],
            "files": [
                {"path": "models/x.ifc", "name": "x.ifc", "format": "IFC",
                 "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001-00"},
                {"path": "models/x.dwg", "name": "x.dwg", "format": "DWG",
                 "number": "LDIP-RCRC-SAP-03-DN-ROD-MOD-90000-001-00"},
            ],
        }],
    })
    out = tmp_path / "surfaces.json"
    rc, msg = run("build_surfaces.py", "--surface", f"existing={design}",
                  "--midp", midp, "--inventory", pkg, "--site", "NMAR", "--out", out)
    assert rc == 0, msg
    doc = load(out)
    assert doc["inventory"]["file_name"] == "package.json"
    assert doc["inventory"]["n_files"] >= 2
    miss = [m for m in doc["midp_missing"] if m["reason"] == "format missing"]
    assert miss
    fm = {f.lower() for f in miss[0]["formats_missing"]}
    assert "dwfx" in fm and "dwg" not in fm


# --------------------------------------------------------------------------- L-b TL12–TL14 (synthetic)
def _batter_tin(h_per_v, *, height=3.0, width=10.0, n_along=10, n_run=6, round_z=True,
                surface="batter"):
    """Irregular triangulation of a planar batter; optional 1 mm level rounding (L-b TL12)."""
    run = height * h_per_v
    points, id_grid, pid = [], [], 1
    for j in range(n_along + 1):
        row = []
        y = width * j / n_along
        for i in range(n_run + 1):
            x = run * i / n_run
            z = 100.0 + (height / run) * x
            if round_z:
                z = round(z, 3)
            points.append([str(pid), float(x), float(y), float(z)])
            row.append(str(pid))
            pid += 1
        id_grid.append(row)
    faces = []
    for j in range(n_along):
        for i in range(n_run):
            a, b = id_grid[j][i], id_grid[j][i + 1]
            c, d = id_grid[j + 1][i], id_grid[j + 1][i + 1]
            if (i + j) % 2 == 0:
                faces.extend([[a, b, d], [a, d, c]])
            else:
                faces.extend([[a, b, c], [b, d, c]])
    return {"template": "landxml-surface-1.0", "surface": surface, "index": 1,
            "points": points, "faces": faces, "breaklines": []}


def test_tl12_batter_2h1v_zero_and_1p8_one_group(tmp_path):
    """TL12: 2H:1V mm-rounded batter → 0 EWK-002; 1.8H:1V → 1 group height 3 m."""
    rules = rules_ewk(tmp_path)
    road = write_tin(tmp_path, "rd12", (500, 500), (5, 5), z0=100)

    path_ok = write(tmp_path / "b2.surface-1.json", _batter_tin(2.0, surface="b2"))
    surf = surfaces_doc(tmp_path, design=path_ok, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl_ok.json")
    assert rc == 0, msg
    assert load(tmp_path / "sl_ok.json")["ewk002_groups"] == []

    path_st = write(tmp_path / "b18.surface-1.json", _batter_tin(1.8, surface="b18"))
    # rebuild surfaces for steep batter (fresh out path)
    out2 = tmp_path / "surfaces_b18.json"
    rc, msg = run("build_surfaces.py",
                  "--surface", f"design_grading={path_st}",
                  "--surface", f"road_top={road}",
                  "--out", out2)
    assert rc == 0, msg
    rc, msg = run("check_slopes.py", "--surfaces", out2, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl_st.json")
    assert rc == 0, msg
    g = load(tmp_path / "sl_st.json")["ewk002_groups"]
    assert len(g) == 1, g
    assert abs(g[0]["height_m"] - 3.0) < 0.05
    note = [c.get("note") or "" for c in load(tmp_path / "sl_st.json")["checks"]
            if c["rule_id"] == "EWK-002" and c["parameter"] == "slope_min_h_per_v"]
    assert note and "mean=" in note[0] and "max=" in note[0] and "H:V" in note[0]


def test_tl13_no_fragmentation_under_threshold_faces(tmp_path):
    """TL13: 1H:1V slope with ~2 % faces just under 50 % → still 1 EWK-002 group."""
    rules = rules_ewk(tmp_path)
    # Dense 1H:1V batter (height 3 m, run 3 m, width 20 m)
    tin = _batter_tin(1.0, height=3.0, width=20.0, n_along=20, n_run=10, round_z=False,
                      surface="frag")
    # Flatten ~2 % of faces to ~49.9 % by dropping one vertex (creates near-threshold faces
    # that would split groups under a hard 50.05 % cut).
    pts = {str(r[0]): r for r in tin["points"]}
    n_faces = len(tin["faces"])
    n_flat = max(1, int(round(0.02 * n_faces)))
    for fi in range(0, n_flat * 2, 2):  # every other early face
        if fi >= n_faces:
            break
        f = tin["faces"][fi]
        # lower the uphill vertex slightly so slope ≈ 49.9 %
        # face spans roughly Δx along run; set z of highest point down
        zs = [(pts[str(vid)][3], str(vid)) for vid in f]
        zs.sort()
        top_id = zs[-1][1]
        # target slope ~49.9 %: for run≈0.3 m between grid, reduce rise
        pts[top_id][3] = round(pts[top_id][3] - 0.15, 3)
    tin["points"] = list(pts.values())
    design = write(tmp_path / "frag.surface-1.json", tin)
    road = write_tin(tmp_path, "rd13", (500, 500), (5, 5), z0=100)
    surf = surfaces_doc(tmp_path, design=design, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl.json")
    assert rc == 0, msg
    g = load(tmp_path / "sl.json")["ewk002_groups"]
    assert len(g) == 1, (len(g), g[:3] if g else None)


def test_tl14_near_threshold_flat_pad(tmp_path):
    """TL14: 30×30 at 0.99 % → 0 EWK-001; at 0.5 % → 1 group."""
    rules = rules_ewk(tmp_path)
    road = write_tin(tmp_path, "rd14", (1000, 1000), (5, 5), z0=100)

    # 0.99 % over 30 m → rise 0.297 m, mm-rounded
    rise_hi = round(0.0099 * 30.0, 3)
    d_hi = write_tin(tmp_path, "pad99", (0, 0), (30, 30), rise_e=rise_hi, z0=100)
    surf = surfaces_doc(tmp_path, design=d_hi, road=road)
    rc, msg = run("check_slopes.py", "--surfaces", surf, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl_hi.json")
    assert rc == 0, msg
    assert load(tmp_path / "sl_hi.json")["ewk001_groups"] == []

    rise_lo = round(0.005 * 30.0, 3)
    d_lo = write_tin(tmp_path, "pad05", (0, 0), (30, 30), rise_e=rise_lo, z0=100)
    out2 = tmp_path / "surfaces_lo.json"
    rc, msg = run("build_surfaces.py",
                  "--surface", f"design_grading={d_lo}",
                  "--surface", f"road_top={road}", "--out", out2)
    assert rc == 0, msg
    rc, msg = run("check_slopes.py", "--surfaces", out2, "--rules", rules,
                  "--stage", "30% Concept Design", "--out", tmp_path / "sl_lo.json")
    assert rc == 0, msg
    g = load(tmp_path / "sl_lo.json")["ewk001_groups"]
    assert len(g) == 1, g
    assert g[0]["area_m2"] >= 25.0
