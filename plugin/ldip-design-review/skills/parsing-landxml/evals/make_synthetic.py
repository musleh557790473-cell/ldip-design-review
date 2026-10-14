#!/usr/bin/env python3
"""Generate the synthetic LandXML of test T5 (SPC-0006 Rev C §4.12.12). No project data.

Alignment SYN-A: line 100 m (east) - arc R = 150 m, 30 deg right (cw) - clothoid 50 m, R 150 -> INF (cw)
- line 100 m. Profile SYN-A-FG: PVI 0+000 @ 680.000, crest ParaCurve L = 60 m at 0+150 @ 683.000
(+2.0 % in, -1.5 % out), PVI at the alignment end. Existing-ground ProfSurf with 4 points.
Coordinates near the LDIP sites (EPSG:32638); written N E as LandXML requires.
Expected (T5): min_radius 150.000; A = 3.5 %; K = 17.14; crest; no continuity gaps; no warnings.

Usage: python make_synthetic.py --out synthetic_t5.xml
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import landxml_geom as g  # noqa: E402


def ne(p):
    return f"{p[1]:.6f} {p[0]:.6f}"


def build():
    p0 = (657000.0, 2747500.0)
    az = 90.0
    p1 = (p0[0] + 100.0, p0[1])
    R, sweep = 150.0, math.radians(30.0)
    rn = (math.cos(math.radians(az)), -math.sin(math.radians(az)))           # right normal (cw turn)
    c = (p1[0] + R * rn[0], p1[1] + R * rn[1])
    a0 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    p2 = (c[0] + R * math.cos(a0 - sweep), c[1] + R * math.sin(a0 - sweep))  # clockwise
    arc_len = R * sweep
    az2 = az + 30.0
    t = R * math.tan(sweep / 2)
    arc_pi = (p1[0] + t * math.sin(math.radians(az)), p1[1] + t * math.cos(math.radians(az)))
    Ls = 50.0
    p3 = g.clothoid_point(p2, az2, Ls, R, math.inf, "cw", Ls)
    az3 = g.clothoid_end_azimuth(az2, Ls, R, math.inf, "cw")
    # spiral PI = intersection of the start and end tangents
    d1 = (math.sin(math.radians(az2)), math.cos(math.radians(az2)))
    d2 = (math.sin(math.radians(az3)), math.cos(math.radians(az3)))
    den = d1[0] * d2[1] - d1[1] * d2[0]
    s = ((p3[0] - p2[0]) * d2[1] - (p3[1] - p2[1]) * d2[0]) / den
    spi = (p2[0] + s * d1[0], p2[1] + s * d1[1])
    p4 = (p3[0] + 100.0 * d2[0], p3[1] + 100.0 * d2[1])
    total = 100.0 + arc_len + Ls + 100.0
    z_end = 683.0 - 0.015 * (total - 150.0)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2" date="2026-10-14" time="12:00:00">
 <Units><Metric areaUnit="squareMeter" linearUnit="meter" volumeUnit="cubicMeter" temperatureUnit="celsius" pressureUnit="milliBars" angularUnit="decimal degrees" directionUnit="decimal degrees"/></Units>
 <CoordinateSystem epsgCode="32638" desc="UTM-WGS 1984 datum, Zone 38 North (synthetic test)"/>
 <Project name="SYNTHETIC-T5"/>
 <Application name="make_synthetic.py" version="0.1.0" manufacturer="AICC BIM Office (test data)"/>
 <Alignments>
  <Alignment name="SYN-A" desc="synthetic line-arc-spiral-line" staStart="0" length="{total:.6f}">
   <CoordGeom>
    <Line length="100.000000"><Start>{ne(p0)}</Start><End>{ne(p1)}</End></Line>
    <Curve rot="cw" crvType="arc" radius="{R:.6f}" length="{arc_len:.6f}"><Start>{ne(p1)}</Start><Center>{ne(c)}</Center><End>{ne(p2)}</End><PI>{ne(arc_pi)}</PI></Curve>
    <Spiral rot="cw" spiType="clothoid" length="{Ls:.6f}" radiusStart="{R:.6f}" radiusEnd="INF"><Start>{ne(p2)}</Start><PI>{ne(spi)}</PI><End>{ne(p3)}</End></Spiral>
    <Line length="100.000000"><Start>{ne(p3)}</Start><End>{ne(p4)}</End></Line>
   </CoordGeom>
   <Profile name="SYN-A">
    <ProfAlign name="SYN-A-FG" desc="design">
     <PVI>0.000000 680.000000</PVI>
     <ParaCurve length="60.000000">150.000000 683.000000</ParaCurve>
     <PVI>{total:.6f} {z_end:.6f}</PVI>
    </ProfAlign>
    <ProfSurf name="SYN-A-EG" desc="existing ground"><PntList2D>0 679.5 100 680.4 200 681.1 {total:.3f} 680.0</PntList2D></ProfSurf>
   </Profile>
  </Alignment>
 </Alignments>
</LandXML>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    Path(a.out).write_text(build(), encoding="utf-8")
    print(f'{{"status": "ok", "out": "{Path(a.out).as_posix()}"}}')


if __name__ == "__main__":
    main()
