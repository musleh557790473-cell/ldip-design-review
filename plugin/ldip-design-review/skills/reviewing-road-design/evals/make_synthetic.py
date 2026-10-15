#!/usr/bin/env python3
"""Generate the synthetic LandXML of skill 10 test T4 (SPC-0006 Rev D §4.13.13). No project data.

Alignment RD-90 (desc "Collector road, synthetic"): line 100 m east - arc R = 120 m, 20 deg right - line
100 m. Profile RD-90-FG: PVI 0+000 @ 680.000, crest ParaCurve L = 40 m at 0+120 @ 687.800 (+6.5 % in,
-1.5 % out: A = 8.0 %, K = 5.0), PVI at the end. Coordinates near the LDIP sites (EPSG:32638), written N E.
Expected with the rulebook values of rules_v2 and a collector design speed of 40 km/h (ER minimum 50):
ROD-017 (120 < 150), ROD-024 (6.5 > 6.0), ROD-028 at 50 km/h (5.0 < 7.4; passes 4 at 40 km/h),
ROD-030 (40 < 50 at 40 and 50 km/h) fail; ROD-026 passes; no sag curve. All class-dependent findings
Probable (class from the alignment desc, D10-3).

Usage: python make_synthetic.py --out synthetic_t4.xml
"""
import argparse
import math
from pathlib import Path


def ne(p):
    return f"{p[1]:.6f} {p[0]:.6f}"


def build():
    p0 = (657000.0, 2747500.0)
    p1 = (p0[0] + 100.0, p0[1])
    R, sweep = 120.0, math.radians(20.0)
    c = (p1[0], p1[1] - R)                                   # right turn from heading east: centre to the south
    a0 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    p2 = (c[0] + R * math.cos(a0 - sweep), c[1] + R * math.sin(a0 - sweep))
    arc_len = R * sweep
    t = R * math.tan(sweep / 2)
    pi = (p1[0] + t, p1[1])
    az = math.radians(90.0 + 20.0)
    p3 = (p2[0] + 100.0 * math.sin(az), p2[1] + 100.0 * math.cos(az))
    total = 200.0 + arc_len
    z_pvi = 680.0 + 0.065 * 120.0
    z_end = z_pvi - 0.015 * (total - 120.0)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2" date="2026-10-15" time="12:00:00">
 <Units><Metric areaUnit="squareMeter" linearUnit="meter" volumeUnit="cubicMeter" temperatureUnit="celsius" pressureUnit="milliBars" angularUnit="decimal degrees" directionUnit="decimal degrees"/></Units>
 <CoordinateSystem epsgCode="32638" desc="UTM-WGS 1984 datum, Zone 38 North (synthetic test)"/>
 <Project name="SYNTHETIC-SKILL10-T4"/>
 <Application name="make_synthetic.py" version="0.1.0" manufacturer="AICC BIM Office (test data)"/>
 <Alignments>
  <Alignment name="RD-90" desc="Collector road, synthetic" staStart="0" length="{total:.6f}">
   <CoordGeom>
    <Line length="100.000000"><Start>{ne(p0)}</Start><End>{ne(p1)}</End></Line>
    <Curve rot="cw" crvType="arc" radius="{R:.6f}" length="{arc_len:.6f}"><Start>{ne(p1)}</Start><Center>{ne(c)}</Center><End>{ne(p2)}</End><PI>{ne(pi)}</PI></Curve>
    <Line length="100.000000"><Start>{ne(p2)}</Start><End>{ne(p3)}</End></Line>
   </CoordGeom>
   <Profile name="RD-90">
    <ProfAlign name="RD-90-FG" desc="design">
     <PVI>0.000000 680.000000</PVI>
     <ParaCurve length="40.000000">120.000000 {z_pvi:.6f}</ParaCurve>
     <PVI>{total:.6f} {z_end:.6f}</PVI>
    </ProfAlign>
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
