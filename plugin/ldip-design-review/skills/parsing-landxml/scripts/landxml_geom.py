"""Geometry library for skill 3 parsing-landxml (SPC-0006 Rev C §4.12.6, §4.12.7).

- LandXML coordinate text is Northing, Easting [, Elevation]. `ne_text_to_en` is the ONE place where
  the order is turned into Easting, Northing [, Elevation] (test T2).
- Directions are converted to decimal degrees from the file's angular / direction unit.
- Arc and clothoid lengths and end points; profile grades, algebraic difference A and K = L / |A|;
  triangle areas (2D, 3D).
Pure functions; no I/O, no network, no shell calls.
"""
__version__ = "0.1.0"

import math

# EPSG:32638 sanity ranges (SPC-0006 Rev C §4.12.6)
E_RANGE = (166000.0, 834000.0)
N_RANGE = (0.0, 9330000.0)


class GeomError(ValueError):
    pass


# --------------------------------------------------------------------------- coordinate order
def numbers(text):
    """Whitespace-separated numbers of an element's text; GeomError on any non-number."""
    if text is None:
        return []
    out = []
    for tok in text.split():
        try:
            out.append(float(tok))
        except ValueError as exc:
            raise GeomError(f"not a number: {tok[:40]}") from exc
    return out


def ne_to_en(values):
    """One LandXML point [N, E] or [N, E, Z] -> (E, N) or (E, N, Z)."""
    if len(values) == 2:
        return (values[1], values[0])
    if len(values) == 3:
        return (values[1], values[0], values[2])
    raise GeomError(f"a point needs 2 or 3 values, got {len(values)}")


def ne_text_to_en(text):
    """Text of P / Start / End / Center / PI (one point) -> (E, N[, Z])."""
    return ne_to_en(numbers(text))


def ne_list_to_en(text, dim):
    """PntList2D (dim 2) / PntList3D (dim 3) text -> list of (E, N[, Z])."""
    v = numbers(text)
    if len(v) % dim:
        raise GeomError(f"point list length {len(v)} is not a multiple of {dim}")
    return [ne_to_en(v[i:i + dim]) for i in range(0, len(v), dim)]


def axis_check(e, n):
    """'ok' | 'swapped' (fits EPSG:32638 only with E and N exchanged) | 'outside' (fits neither way)."""
    def fits(x, y):
        return E_RANGE[0] <= x <= E_RANGE[1] and N_RANGE[0] <= y <= N_RANGE[1]
    if fits(e, n):
        return "ok"
    if fits(n, e):
        return "swapped"
    return "outside"


# --------------------------------------------------------------------------- angles
def to_degrees(value, unit):
    """A direction / angle in the file's unit -> decimal degrees. None stays None.
    Units per LandXML 1.2 angularType: radians, grads, decimal degrees, decimal dd.mm.ss."""
    if value is None:
        return None
    u = (unit or "decimal degrees").strip().lower()
    if u == "radians":
        return math.degrees(float(value))
    if u == "grads":
        return float(value) * 0.9
    if u == "decimal degrees":
        return float(value)
    if u == "decimal dd.mm.ss":
        s = str(value).strip()
        neg = s.startswith("-")
        s = s.lstrip("+-")
        d, _, rest = s.partition(".")
        rest = (rest + "0000")[:4] + ("." + rest[4:] if len(rest) > 4 else "")
        deg = int(d or 0) + int(rest[:2]) / 60.0 + float(rest[2:]) / 3600.0
        return -deg if neg else deg
    raise GeomError(f"unknown angular unit: {unit}")


# --------------------------------------------------------------------------- plan geometry
def dist2d(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def azimuth(a, b):
    """Grid azimuth a->b in degrees, clockwise from grid north (0..360)."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360.0


def arc_sweep(start, center, end, rot):
    """Swept angle (radians, 0..2pi) from start to end around center in direction rot ('cw' / 'ccw')."""
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    a1 = math.atan2(end[1] - center[1], end[0] - center[0])
    d = (a1 - a0) % (2 * math.pi)          # counter-clockwise sweep
    if rot == "cw":
        d = (2 * math.pi - d) % (2 * math.pi)
    return d


def arc_length(start, center, end, rot, radius=None):
    r = radius if radius else dist2d(center, start)
    return r * arc_sweep(start, center, end, rot)


def clothoid_point(start, azimuth_deg, length, r_start, r_end, rot, s):
    """Point at distance s along a clothoid that starts at `start` with grid azimuth `azimuth_deg`;
    curvature changes linearly from 1/r_start to 1/r_end (None or inf = 0). Numerical integration
    (Simpson, 200 steps); accurate to well below 1 mm for road spirals."""
    k0 = 0.0 if not r_start or math.isinf(r_start) else 1.0 / r_start
    k1 = 0.0 if not r_end or math.isinf(r_end) else 1.0 / r_end
    sign = 1.0 if rot == "cw" else -1.0       # cw = curving right = azimuth increases
    th0 = math.radians(azimuth_deg)
    steps = 200

    def theta(t):
        return th0 + sign * (k0 * t + (k1 - k0) * t * t / (2.0 * length))

    h = s / steps
    se = sn = 0.0
    for i in range(steps + 1):
        w = 1 if i in (0, steps) else (4 if i % 2 else 2)
        th = theta(i * h)
        se += w * math.sin(th)
        sn += w * math.cos(th)
    return (start[0] + se * h / 3.0, start[1] + sn * h / 3.0)


def clothoid_end_azimuth(azimuth_deg, length, r_start, r_end, rot):
    k0 = 0.0 if not r_start or math.isinf(r_start) else 1.0 / r_start
    k1 = 0.0 if not r_end or math.isinf(r_end) else 1.0 / r_end
    sign = 1.0 if rot == "cw" else -1.0
    return (azimuth_deg + sign * math.degrees((k0 + k1) * length / 2.0)) % 360.0


def parse_radius(text):
    """LandXML radius attribute; 'INF' / 'inf' / 'Infinity' -> math.inf; None stays None."""
    if text is None:
        return None
    t = str(text).strip().lower()
    if t in ("inf", "infinity", "+inf"):
        return math.inf
    return float(t)


# --------------------------------------------------------------------------- profiles
def grade_pct(p0, p1):
    """Grade in % between two (station, elevation) points."""
    ds = p1[0] - p0[0]
    if ds == 0:
        raise GeomError(f"two PVIs at the same station {p0[0]}")
    return (p1[1] - p0[1]) / ds * 100.0


def curve_k(length, g_in, g_out):
    """Algebraic difference A = |g_out - g_in| (%), K = L / A, crest (g_out < g_in) or sag."""
    a = abs(g_out - g_in)
    k = None if a == 0 else length / a
    kind = "crest" if g_out < g_in else ("sag" if g_out > g_in else "none")
    return a, k, kind


# --------------------------------------------------------------------------- triangles
def tri_area_2d(a, b, c):
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2.0


def tri_area_3d(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return math.sqrt(cx * cx + cy * cy + cz * cz) / 2.0
