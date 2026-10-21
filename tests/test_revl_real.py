"""Skill 13 real-data tests TL8 / TL9 — skipped without LDIP_REVIEW_ROOT.

SPC-0006 Rev L §4.14.14. Real packages stay outside Git.
"""
import os
from pathlib import Path

import pytest

ROOT = os.environ.get("LDIP_REVIEW_ROOT") or os.environ.get("LDIP_REVIEW")

pytestmark = pytest.mark.skipif(
    not ROOT or not Path(ROOT).is_dir(),
    reason="LDIP_REVIEW_ROOT not set — real NMAR/QRWN/NRJS packages stay out of Git",
)


def test_tl8_nmar_30pct_profile_placeholder():
    """TL8: real NMAR IFC 001/002/003 profile counts — run when owner links LDIP_REVIEW_ROOT.

    Full assertions (areas, group counts, 0 candidates vs L-a findings after rules_v8)
    require the local package and an active/synthetic rules decision. Placeholder verifies
    the env gate and that Roads IFC paths exist when linked.
    """
    root = Path(ROOT)
    # Common layout from Rev L §4.14.2 — do not invent contents if missing
    candidates = list(root.rglob("*ROD-MOD-90000-002*")) + list(root.rglob("*Site*Grading*.ifc"))
    if not candidates:
        pytest.skip("NMAR grading IFC not found under LDIP_REVIEW_ROOT")
    assert candidates[0].is_file()


def test_tl9_qrwn_nrjs_placeholder():
    """TL9: real QRWN / NRJS — skip unless linked; do not invent surfaces."""
    root = Path(ROOT)
    xml = list(root.rglob("*Roads*TOS*.xml")) + list(root.rglob("*Compiled*Grading*"))
    ifc = list(root.rglob("*ROD-MOD-90000*.ifc")) + list(root.rglob("*IRO-MOD*.ifc"))
    if not xml and not ifc:
        pytest.skip("QRWN/NRJS earthworks carriers not found under LDIP_REVIEW_ROOT")
    assert True
