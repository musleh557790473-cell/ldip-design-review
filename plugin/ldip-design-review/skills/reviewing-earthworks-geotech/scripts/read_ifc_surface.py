#!/usr/bin/env python3
"""Dependency-free IFC STEP surface reader for skill 13 (SPC-0006 Rev L §4.14.5 + L-a ADDENDUM).

Streams the STEP file line by line (no whole-file read, no regex over the full text).
Reads IfcFaceBasedSurfaceModel / ShellBased / FacetedBrep / IFC4 TriangulatedFaceSet on
IfcBuildingElementProxy, IfcGeographicElement, IfcSite. Follows IfcMappedItem /
IfcRepresentationMap. Applies translation-only IfcLocalPlacement (rotated → refused).

Single-layer preference: when a file holds a thin solid and a single-layer surface of the
same extent, the single-layer is used. When only a solid exists, non-vertical faces
(|n_z| > 1e-9) are kept; the up/down test (n_z > 0) is used only to split a thin solid.

Usage: python read_ifc_surface.py --ifc <file.ifc> --out-dir <dir> [--prefix <stem>]
Exit: 0 written · 2 input error. No network, no new packages.
"""
__version__ = "0.1.1"

import argparse
import hashlib
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ewk_common import EXIT_INPUT, EXIT_OK, EwkError, emit, main_guard  # noqa: E402

# Entity types we care about (upper-case STEP names)
POINT = "IFCCARTESIANPOINT"
POINTLIST3D = "IFCCARTESIANPOINTLIST3D"
POLYLOOP = "IFCPOLYLOOP"
FACEBOUND = "IFCFACEBOUND"
FACEOUTERBOUND = "IFCFACEOUTERBOUND"
FACE = "IFCFACE"
CONNECTED = "IFCCONNECTEDFACESET"
OPENSHELL = "IFCOPENSHELL"
CLOSEDSHELL = "IFCCLOSEDSHELL"
FBS = "IFCFACEBASEDSURFACEMODEL"
SBS = "IFCSHELLBASEDSURFACEMODEL"
BREP = "IFCFACETEDBREP"
TFS = "IFCTRIANGULATEDFACESET"
LOCALPL = "IFCLOCALPLACEMENT"
AXIS2 = "IFCAXIS2PLACEMENT3D"
DIR = "IFCDIRECTION"
CART_TRANS = "IFCCARTESIANTRANSFORMATIONOPERATOR3D"
MAPPED = "IFCMAPPEDITEM"
REPMAP = "IFCREPRESENTATIONMAP"
SHAPEREP = "IFCSHAPEREPRESENTATION"
PRODEF = "IFCPRODUCTDEFINITIONSHAPE"
PROXY = "IFCBUILDINGELEMENTPROXY"
GEO = "IFCGEOGRAPHICELEMENT"
SITE = "IFCSITE"
PRODUCTS = {PROXY, GEO, SITE}

REF = re.compile(r"#(\d+)")
HEADER_SCHEMA = re.compile(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", re.I)
HEADER_APP = re.compile(r"FILE_NAME\s*\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,", re.I)
HEADER_NAME = re.compile(r"FILE_NAME\s*\(\s*'([^']*)'", re.I)


def _parse_refs(blob):
    return [int(x) for x in REF.findall(blob)]


def _parse_floats(blob):
    # Numbers inside the outermost parentheses of the entity
    return [float(x) for x in re.findall(r"[-+]?(?:\d+\.\d*|\d*\.\d+|\d+)(?:[Ee][-+]?\d+)?", blob)]


def _split_args(body):
    """Split top-level comma-separated STEP args (respect parentheses and quotes)."""
    args, cur, depth, in_str = [], [], 0, False
    i = 0
    while i < len(body):
        ch = body[i]
        if in_str:
            cur.append(ch)
            if ch == "'":
                if i + 1 < len(body) and body[i + 1] == "'":
                    cur.append("'")
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            cur.append(ch)
        elif ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    if cur:
        args.append("".join(cur).strip())
    return args


def _stream_entities(path):
    """Yield (id, type_upper, arg_body) streaming line by line. Bounded memory."""
    buf = ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not buf and line.lstrip().startswith("/*"):
                # skip comment blocks spanning lines cheaply
                if "*/" not in line:
                    # read until end of comment
                    for more in fh:
                        if "*/" in more:
                            break
                    continue
            s = line.strip()
            if not s or s.startswith("ISO-10303") or s.startswith("HEADER") or s.startswith("DATA") or s == "ENDSEC;" or s == "END-ISO-10303-21;":
                if s.startswith("FILE_SCHEMA") or s.startswith("FILE_NAME"):
                    yield None, "_HEADER_", s
                continue
            buf += s if not buf else " " + s
            if not buf.endswith(";"):
                continue
            ent = buf
            buf = ""
            if not ent.startswith("#"):
                if ent.startswith("FILE_SCHEMA") or ent.startswith("FILE_NAME"):
                    yield None, "_HEADER_", ent
                continue
            m = re.match(r"#(\d+)\s*=\s*([A-Za-z0-9_]+)\s*\((.*)\)\s*;\s*$", ent, re.S)
            if not m:
                continue
            yield int(m.group(1)), m.group(2).upper(), m.group(3)


def _face_normal_z(pts3):
    if len(pts3) < 3:
        return 0.0
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = pts3[0], pts3[1], pts3[2]
    ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
    vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
    return ux * vy - uy * vx  # nz of cross product (XY-projected area * 2 related)


def _fan_tris(loop_pts):
    """Fan-split polygon (≥3 verts) into triangles. Returns list of 3-point triples."""
    if len(loop_pts) < 3:
        return []
    if len(loop_pts) == 3:
        return [loop_pts]
    tris = []
    a = loop_pts[0]
    for i in range(1, len(loop_pts) - 1):
        tris.append([a, loop_pts[i], loop_pts[i + 1]])
    return tris


def read_ifc(path):
    """Parse IFC STEP into products with triangle lists. Raises EwkError on rotated placement."""
    points = {}          # id -> (x,y,z) local
    point_lists = {}     # id -> [(x,y,z), ...]
    loops = {}           # id -> [point_ids]
    bounds = {}          # id -> loop_id
    faces = {}           # id -> [bound_ids]
    face_sets = {}       # id -> [face_ids]
    shells = {}          # id -> [face_ids] (open/closed)
    fbs = {}             # id -> [face_set_ids]
    sbs = {}             # id -> [shell_ids]
    breps = {}           # id -> shell_id
    tfs = {}             # id -> (coord_list_id, face_index_rows)
    dirs = {}            # id -> (dx,dy,dz)
    axis2 = {}           # id -> (location_id, axis_id|None, ref_id|None)
    localpl = {}         # id -> (placement_rel_to|None, relative_id)
    mapped = {}          # id -> (mapping_source_id, mapping_target_id)
    repmaps = {}         # id -> mapped_rep_id
    shapereps = {}       # id -> [item_ids]
    proddefs = {}        # id -> [shaperep_ids]
    products = {}        # id -> {type, name, object_placement, representation}
    header = {"schema": None, "application": None, "date": None, "name": None}

    for eid, etype, body in _stream_entities(path):
        if etype == "_HEADER_":
            m = HEADER_SCHEMA.search(body)
            if m:
                header["schema"] = m.group(1)
            m = HEADER_APP.search(body)
            if m:
                header["name"] = header["name"] or m.group(1)
                header["date"] = m.group(2)
                # application often in FILE_NAME later args — leave None if absent
            m = HEADER_NAME.search(body)
            if m and not header["name"]:
                header["name"] = m.group(1)
            continue
        if etype == POINT:
            nums = _parse_floats(body)
            if len(nums) >= 3:
                points[eid] = (nums[0], nums[1], nums[2])
            elif len(nums) == 2:
                points[eid] = (nums[0], nums[1], 0.0)
        elif etype == POINTLIST3D:
            # IFC4: IfcCartesianPointList3D(((\ldots),(\ldots),…))
            nums = _parse_floats(body)
            pts = [(nums[i], nums[i + 1], nums[i + 2]) for i in range(0, len(nums) - 2, 3)]
            point_lists[eid] = pts
        elif etype == POLYLOOP:
            loops[eid] = _parse_refs(body)
        elif etype in (FACEBOUND, FACEOUTERBOUND):
            refs = _parse_refs(body)
            if refs:
                bounds[eid] = refs[0]
        elif etype == FACE:
            faces[eid] = _parse_refs(body)
        elif etype in (CONNECTED, OPENSHELL, CLOSEDSHELL):
            face_sets[eid] = _parse_refs(body)
            if etype in (OPENSHELL, CLOSEDSHELL):
                shells[eid] = _parse_refs(body)
        elif etype == FBS:
            fbs[eid] = _parse_refs(body)
        elif etype == SBS:
            sbs[eid] = _parse_refs(body)
        elif etype == BREP:
            refs = _parse_refs(body)
            if refs:
                breps[eid] = refs[0]
        elif etype == TFS:
            refs = _parse_refs(body)
            # CoordList is first ref typically; face indexes are integer lists
            rows = []
            for m in re.finditer(r"\((\d+(?:\s*,\s*\d+)*)\)", body):
                row = [int(x) for x in m.group(1).split(",")]
                if len(row) >= 3 and all(v > 0 for v in row[:3]):
                    # skip CoordIndex that might be confused — keep 3+ int rows
                    if refs and row[0] == refs[0]:
                        continue
                    rows.append(row)
            tfs[eid] = (refs[0] if refs else None, rows)
        elif etype == DIR:
            nums = _parse_floats(body)
            if len(nums) >= 3:
                dirs[eid] = (nums[0], nums[1], nums[2])
            elif len(nums) == 2:
                dirs[eid] = (nums[0], nums[1], 0.0)
        elif etype == AXIS2:
            refs = _parse_refs(body)
            axis2[eid] = (refs[0] if refs else None,
                          refs[1] if len(refs) > 1 else None,
                          refs[2] if len(refs) > 2 else None)
        elif etype == LOCALPL:
            refs = _parse_refs(body)
            # IFCLOCALPLACEMENT($, #rel) or (#parent, #rel)
            args = _split_args(body)
            parent = None
            if args and args[0] not in ("$", "*") and args[0].startswith("#"):
                parent = int(args[0][1:])
            rel = refs[-1] if refs else None
            if parent is not None and rel == parent and len(refs) == 1:
                rel = parent
                parent = None
            if len(refs) >= 2:
                parent, rel = refs[0], refs[1]
            elif len(refs) == 1 and parent is None:
                rel = refs[0]
            localpl[eid] = (parent, rel)
        elif etype == MAPPED:
            refs = _parse_refs(body)
            if len(refs) >= 2:
                mapped[eid] = (refs[0], refs[1])
            elif len(refs) == 1:
                mapped[eid] = (refs[0], None)
        elif etype == REPMAP:
            refs = _parse_refs(body)
            # MappingOrigin, MappedRepresentation
            if len(refs) >= 2:
                repmaps[eid] = refs[1]
            elif refs:
                repmaps[eid] = refs[0]
        elif etype == SHAPEREP:
            refs = _parse_refs(body)
            shapereps[eid] = refs
        elif etype == PRODEF:
            refs = _parse_refs(body)
            proddefs[eid] = refs
        elif etype in PRODUCTS:
            args = _split_args(body)
            # GlobalId, OwnerHistory, Name, Description, ObjectType, ObjectPlacement, Representation, Tag
            name = None
            if len(args) > 2:
                raw = args[2].strip()
                if raw.startswith("'") and raw.endswith("'"):
                    name = raw[1:-1].replace("''", "'")
            refs = _parse_refs(body)
            # Heuristic: last two refs often placement + representation
            placement = refs[-2] if len(refs) >= 2 else (refs[0] if refs else None)
            representation = refs[-1] if refs else None
            # Better: walk args positions 5 and 6 (0-based)
            if len(args) > 6:
                if args[5].startswith("#"):
                    placement = int(args[5][1:])
                elif args[5] in ("$", "*"):
                    placement = None
                if args[6].startswith("#"):
                    representation = int(args[6][1:])
                elif args[6] in ("$", "*"):
                    representation = None
            products[eid] = {"type": etype, "name": name, "placement": placement,
                             "representation": representation}

    def placement_offset(pid, _seen=None):
        """Accumulate translation; refuse non-identity rotation."""
        if pid is None:
            return (0.0, 0.0, 0.0)
        _seen = _seen or set()
        if pid in _seen:
            return (0.0, 0.0, 0.0)
        _seen.add(pid)
        if pid not in localpl:
            return (0.0, 0.0, 0.0)
        parent, rel = localpl[pid]
        ox, oy, oz = 0.0, 0.0, 0.0
        if parent:
            ox, oy, oz = placement_offset(parent, _seen)
        if rel and rel in axis2:
            loc_id, axis_id, ref_id = axis2[rel]
            if loc_id and loc_id in points:
                lx, ly, lz = points[loc_id]
                ox, oy, oz = ox + lx, oy + ly, oz + lz
            # Rotation check: default axis (0,0,1) and ref (1,0,0) are OK; anything else refused
            if axis_id and axis_id in dirs:
                dx, dy, dz = dirs[axis_id]
                if abs(dx) > 1e-9 or abs(dy) > 1e-9 or abs(dz - 1.0) > 1e-6:
                    raise EwkError(EXIT_INPUT, "IFC_ROTATED_PLACEMENT")
            if ref_id and ref_id in dirs:
                dx, dy, dz = dirs[ref_id]
                if abs(dx - 1.0) > 1e-6 or abs(dy) > 1e-9 or abs(dz) > 1e-9:
                    raise EwkError(EXIT_INPUT, "IFC_ROTATED_PLACEMENT")
        return (ox, oy, oz)

    def resolve_geom_items(item_ids, out_tris, offset):
        for iid in item_ids:
            if iid in fbs:
                for fsid in fbs[iid]:
                    for fid in face_sets.get(fsid, []):
                        _face_to_tris(fid, out_tris, offset)
            elif iid in sbs:
                for shid in sbs[iid]:
                    for fid in shells.get(shid, face_sets.get(shid, [])):
                        _face_to_tris(fid, out_tris, offset)
            elif iid in breps:
                shid = breps[iid]
                for fid in shells.get(shid, face_sets.get(shid, [])):
                    _face_to_tris(fid, out_tris, offset)
            elif iid in face_sets:
                for fid in face_sets[iid]:
                    _face_to_tris(fid, out_tris, offset)
            elif iid in tfs:
                clid, rows = tfs[iid]
                plist = point_lists.get(clid, [])
                for row in rows:
                    # IFC4 CoordIndex is 1-based into PointList
                    try:
                        pts = [plist[i - 1] for i in row[:3]]
                    except (IndexError, TypeError):
                        continue
                    world = [(p[0] + offset[0], p[1] + offset[1], p[2] + offset[2]) for p in pts]
                    out_tris.append(world)
            elif iid in mapped:
                src, tgt = mapped[iid]
                # mapping target may adjust transform — we only allow translation via axis2 if present
                mapped_rep = repmaps.get(src)
                extra = (0.0, 0.0, 0.0)
                if tgt and tgt in axis2:
                    loc_id, axis_id, ref_id = axis2[tgt]
                    if axis_id and axis_id in dirs:
                        dx, dy, dz = dirs[axis_id]
                        if abs(dx) > 1e-9 or abs(dy) > 1e-9 or abs(dz - 1.0) > 1e-6:
                            raise EwkError(EXIT_INPUT, "IFC_ROTATED_PLACEMENT")
                    if loc_id and loc_id in points:
                        extra = points[loc_id]
                if mapped_rep and mapped_rep in shapereps:
                    off2 = (offset[0] + extra[0], offset[1] + extra[1], offset[2] + extra[2])
                    resolve_geom_items(shapereps[mapped_rep], out_tris, off2)
            elif iid in shapereps:
                resolve_geom_items(shapereps[iid], out_tris, offset)

    def _face_to_tris(fid, out_tris, offset):
        bound_ids = faces.get(fid, [])
        if not bound_ids:
            return
        # Prefer outer bound: first bound
        loop_id = bounds.get(bound_ids[0], bound_ids[0] if bound_ids[0] in loops else None)
        if loop_id is None:
            return
        pids = loops.get(loop_id, [])
        loop_pts = []
        for pid in pids:
            if pid not in points:
                return
            x, y, z = points[pid]
            loop_pts.append((x + offset[0], y + offset[1], z + offset[2]))
        for tri in _fan_tris(loop_pts):
            out_tris.append(tri)

    product_geoms = []
    for pid, prod in products.items():
        offset = placement_offset(prod.get("placement"))
        tris = []
        rep = prod.get("representation")
        if rep and rep in proddefs:
            for srid in proddefs[rep]:
                if srid in shapereps:
                    resolve_geom_items(shapereps[srid], tris, offset)
                else:
                    resolve_geom_items([srid], tris, offset)
        elif rep:
            resolve_geom_items([rep], tris, offset)
        if tris:
            product_geoms.append({
                "product_id": pid,
                "type": prod["type"],
                "name": prod.get("name"),
                "triangles": tris,
                "offset": offset,
            })
    return header, product_geoms


def _extent(tris):
    if not tris:
        return None
    xs = [p[0] for t in tris for p in t]
    ys = [p[1] for t in tris for p in t]
    zs = [p[2] for t in tris for p in t]
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def _tri_nz(tri):
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
    ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
    vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
    return ux * vy - uy * vx


def _xy_footprint_key(tri, nd=3):
    """Sorted rounded XY corners — identical for a top/bottom twin of a thin solid."""
    corners = [(round(p[0], nd), round(p[1], nd)) for p in tri]
    return tuple(sorted(corners))


def is_thin_solid_paired(tris, z_tol=0.002, pair_frac=0.35):
    """D1: thin solid by paired faces (same XY footprint, ΔZ ≤ 2 mm, opposite normal).

    Also true when up≈down counts and a meaningful pair fraction share XY.
    Whole-product Z-span is NOT used (terrain solids span tens of metres).
    """
    metas = []
    for tri in tris:
        nz = _tri_nz(tri)
        if abs(nz) <= 1e-9:
            continue
        cz = (tri[0][2] + tri[1][2] + tri[2][2]) / 3.0
        metas.append((_xy_footprint_key(tri), cz, 1 if nz > 0 else -1))
    n = len(metas)
    if n < 2:
        return False
    by_xy = {}
    for key, cz, sign in metas:
        by_xy.setdefault(key, []).append((cz, sign))
    paired = 0
    for items in by_xy.values():
        ups = [z for z, s in items if s > 0]
        dns = [z for z, s in items if s < 0]
        if ups and dns and any(abs(u - d) <= z_tol + 1e-9 for u in ups for d in dns):
            paired += 1
    n_up = sum(1 for _k, _z, s in metas if s > 0)
    n_dn = sum(1 for _k, _z, s in metas if s < 0)
    balanced = n_up > 0 and n_dn > 0 and abs(n_up - n_dn) / max(n_up, n_dn) <= 0.20
    frac = paired / max(1, min(n_up, n_dn) if balanced else n)
    if paired / n >= pair_frac:
        return True
    if balanced and frac >= 0.20:
        return True
    return False


def _nonvertical_faces(tris, upward_only=False):
    """Keep faces with |n_z| > 1e-9; if upward_only, also n_z > 0 (thin-solid split only)."""
    out = []
    for tri in tris:
        nz3 = _tri_nz(tri)
        if abs(nz3) <= 1e-9:
            continue
        if upward_only and nz3 <= 0:
            continue
        out.append(tri)
    return out


def choose_products(product_geoms):
    """One sheet per model: prefer unpaired single-layer; else thin_solid_split (D1)."""
    if not product_geoms:
        return [], "no_products"
    classified = []
    for g in product_geoms:
        tris = g["triangles"]
        thin = is_thin_solid_paired(tris)
        classified.append({**g, "thin_solid": thin, "n_tris": len(tris)})

    layers = [c for c in classified if not c["thin_solid"]]
    solids = [c for c in classified if c["thin_solid"]]
    if layers:
        # Prefer the largest single-layer product; one sheet per model
        best = max(layers, key=lambda c: c["n_tris"])
        best = dict(best)
        best["triangles"] = _nonvertical_faces(best["triangles"], upward_only=False)
        return [best], "single_layer"
    if solids:
        best = max(solids, key=lambda c: c["n_tris"])
        best = dict(best)
        best["triangles"] = _nonvertical_faces(best["triangles"], upward_only=True)
        return [best], "thin_solid_split"
    best = max(classified, key=lambda c: c["n_tris"])
    best = dict(best)
    best["triangles"] = _nonvertical_faces(best["triangles"], upward_only=False)
    return [best], "nonvertical"


_DOCNO = re.compile(
    r"([A-Za-z0-9]+(?:-[A-Za-z0-9]+){5,}(?:-\d{3})?)",
    re.I,
)


def document_no_from_path(path):
    """Deliverable number from IFC file name (D2), e.g. …-ROD-MOD-90000-002."""
    stem = Path(path).stem
    m = _DOCNO.search(stem)
    return m.group(1).upper() if m else stem


def tris_to_sidefile(tris, *, surface_name, index, header, product, file_sha, choice,
                     source_file=None, document_no=None):
    """Build landxml_surface.schema.json-shaped side file + IFC provenance fields."""
    key_to_id = {}
    points = []
    faces = []
    next_id = 1
    for tri in tris:
        ids = []
        for p in tri:
            key = (round(p[0], 6), round(p[1], 6), round(p[2], 6))
            if key not in key_to_id:
                key_to_id[key] = str(next_id)
                points.append([str(next_id), float(p[0]), float(p[1]), float(p[2])])
                next_id += 1
            ids.append(key_to_id[key])
        (x1, y1, _), (x2, y2, _), (x3, y3, _) = tri
        area = abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) / 2.0
        if area < 1e-12:
            continue
        faces.append(ids)
    return {
        "template": "landxml-surface-1.0",
        "surface": surface_name,
        "index": index,
        "points": points,
        "faces": faces,
        "breaklines": [],
        "source_format": "ifc",
        "source_file": source_file,
        "document_no": document_no,
        "product_id": product.get("product_id"),
        "product_name": product.get("name"),
        "product_type": product.get("type"),
        "file_sha256": file_sha,
        "ifc_header": header,
        "layer_choice": choice,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ifc", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--prefix", default=None)
    a = ap.parse_args(argv)

    def run():
        path = Path(a.ifc)
        if not path.is_file():
            raise EwkError(EXIT_INPUT, f"file not found: {a.ifc}")
        # Stream hash (do not require a second full read into memory for hashing alone —
        # sha256_file-style chunked read).
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        file_sha = h.hexdigest()
        header, geoms = read_ifc(path)
        selected, choice = choose_products(geoms)
        out_dir = Path(a.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = a.prefix or path.stem
        doc_no = document_no_from_path(path)
        written = []
        # D1: one chosen sheet per model file
        for i, prod in enumerate(selected[:1], 1):
            name = prod.get("name") or f"IFC-{prod['product_id']}"
            side = tris_to_sidefile(
                prod["triangles"], surface_name=name, index=i,
                header=header, product=prod, file_sha=file_sha, choice=choice,
                source_file=path.name, document_no=doc_no,
            )
            out = out_dir / f"{prefix}.surface-{i}.json"
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_text(__import__("json").dumps(side, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, out)
            written.append({
                "path": str(out),
                "index": i,
                "points": len(side["points"]),
                "faces": len(side["faces"]),
                "product_id": prod["product_id"],
                "name": name,
                "layer_choice": choice,
                "document_no": doc_no,
                "source_file": path.name,
            })
        emit({"status": "ok", "ifc": str(path), "sha256": file_sha, "header": header,
              "document_no": doc_no, "source_file": path.name,
              "products_read": len(geoms), "surfaces": written, "layer_choice": choice,
              "tool": {"script": "read_ifc_surface.py", "version": __version__}})
        return EXIT_OK

    return main_guard(run)


if __name__ == "__main__":
    sys.exit(main())
