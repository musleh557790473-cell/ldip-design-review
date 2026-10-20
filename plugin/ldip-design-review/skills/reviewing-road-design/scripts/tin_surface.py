"""LandXML TIN surface helpers for skill 10 phase D (SPC-0006 §4.13H / DK-4).

Load a design-surface side file (`landxml_surface.schema.json`), select faces whose 2D centroid
lies inside an element polygon, and report face slope % from the fitted plane
z = ax + by + c → 100 * sqrt(a² + b²). Reported element value = maximum face slope %.

No network, no shell calls. Multi-surface choice when several design surfaces exist is [GAP]
unless the caller passes an explicit index.
"""
__version__ = "0.1.0"

import hashlib
import math
from pathlib import Path

from road_common import EXIT_INPUT, RoadError, read_json


def load_tin(path):
    """Return side-file dict; raise RoadError on empty geometry."""
    doc = read_json(path)
    if not doc.get("points") or not doc.get("faces"):
        raise RoadError(EXIT_INPUT, f"surface has no points/faces: {path}")
    return doc


def tin_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_tin_from_landxml(landxml_path, surface_index=None):
    """Pick one TIN side file next to a LandXML ingestion JSON.

    Returns (tin_path, surface_meta, reason_or_none).
    When several surfaces exist and surface_index is None → reason documents [GAP].
    """
    ing = read_json(landxml_path)
    surfaces = (ing.get("content") or {}).get("surfaces") or []
    if not surfaces:
        return None, None, "no design surface in the package"
    parent = Path(landxml_path).resolve().parent
    if surface_index is not None:
        match = next((s for s in surfaces if int(s.get("index") or 0) == int(surface_index)), None)
        if match is None:
            return None, None, f"surface index {surface_index} not in content.surfaces[]"
        tin_name = match.get("tin_file")
        if not tin_name:
            return None, None, f"surface index {surface_index} has no tin_file"
        path = parent / tin_name
        if not path.is_file():
            return None, None, f"tin_file not found: {tin_name}"
        return str(path), match, None
    if len(surfaces) > 1:
        return None, None, ("multiple design surfaces present; which surface to prefer is "
                            "[GAP] (SPC-0006 §4.13H.3 / DK-4) — pass --surface-index")
    s0 = surfaces[0]
    tin_name = s0.get("tin_file")
    if not tin_name:
        return None, None, "surface entry has no tin_file"
    path = parent / tin_name
    if not path.is_file():
        return None, None, f"tin_file not found: {tin_name}"
    return str(path), s0, None


def _point_map(tin):
    """id → (E, N, Z). Point rows are [id, E, N, Z]."""
    out = {}
    for row in tin.get("points") or []:
        if len(row) < 4:
            continue
        pid, e, n, z = row[0], float(row[1]), float(row[2]), float(row[3])
        if pid is None:
            continue
        out[str(pid)] = (e, n, z)
    return out


def face_plane_slope_pct(p1, p2, p3):
    """Slope % of the plane through three XYZ points. Degenerate → None."""
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = p1, p2, p3
    # Solve z = ax + by + c via normal of two edge vectors
    ux, uy, uz = x2 - x1, y2 - y1, z2 - z1
    vx, vy, vz = x3 - x1, y3 - y1, z3 - z1
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    if abs(nz) < 1e-12:
        return None  # vertical / degenerate in XY
    a = -nx / nz
    b = -ny / nz
    return 100.0 * math.sqrt(a * a + b * b)


def face_centroid_2d(p1, p2, p3):
    return ((p1[0] + p2[0] + p3[0]) / 3.0, (p1[1] + p2[1] + p3[1]) / 3.0)


def faces_in_polygon(tin, polygon, g):
    """Yield (face_ids, slope_pct, centroid_xy) for faces whose 2D centroid is inside polygon.

    `polygon` is a shapely Polygon (or anything with .covers / .contains).
    """
    pts = _point_map(tin)
    for face in tin.get("faces") or []:
        if len(face) < 3:
            continue
        ids = [str(face[0]), str(face[1]), str(face[2])]
        try:
            p1, p2, p3 = pts[ids[0]], pts[ids[1]], pts[ids[2]]
        except KeyError:
            continue
        cx, cy = face_centroid_2d(p1, p2, p3)
        if not polygon.covers(g.Point(cx, cy)):
            continue
        slope = face_plane_slope_pct(p1, p2, p3)
        if slope is None:
            continue
        yield ids, slope, (cx, cy)


def max_face_slope_in_polygon(tin, polygon, g):
    """Return (max_slope_pct, face_count) or (None, 0) when no face centroid is inside."""
    max_s, n = None, 0
    for _ids, slope, _c in faces_in_polygon(tin, polygon, g):
        n += 1
        if max_s is None or slope > max_s:
            max_s = slope
    return max_s, n


def min_face_slope_in_polygon(tin, polygon, g):
    """Return (min_slope_pct, face_count) or (None, 0) when no face centroid is inside.

    Used for drainage minimum grade (e.g. ROD-043 drainage_g_min): conservative vs "≥ N %".
    """
    min_s, n = None, 0
    for _ids, slope, _c in faces_in_polygon(tin, polygon, g):
        n += 1
        if min_s is None or slope < min_s:
            min_s = slope
    return min_s, n


def make_flat_tin(origin_e, origin_n, size_e, size_n, z00, z10, z01, z11=None, *, surface="SYN", index=1):
    """Build a compact 2-triangle TIN rectangle for synthetic tests (not a full schema doc).

    z corners: SW=z00, SE=z10, NW=z01, NE=z11 (default = average of others for a warped plane pair).
    """
    e0, n0 = origin_e, origin_n
    e1, n1 = origin_e + size_e, origin_n + size_n
    if z11 is None:
        z11 = z10 + z01 - z00  # planar bilinear completion
    points = [
        ["1", e0, n0, float(z00)],
        ["2", e1, n0, float(z10)],
        ["3", e0, n1, float(z01)],
        ["4", e1, n1, float(z11)],
    ]
    faces = [["1", "2", "4"], ["1", "4", "3"]]
    return {"template": "landxml-surface-1.0", "surface": surface, "index": index,
            "points": points, "faces": faces, "breaklines": []}


def slope_pct_for_rise_run(rise, run):
    """Helper for tests: 100 * rise/run."""
    return 100.0 * float(rise) / float(run)
