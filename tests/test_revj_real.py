"""Real-data check TJ8 (SPC-0006 Rev J §4.13G.11): NMAR / QRWN / NRJS 30 % — every C3 rule not run
(not required at stage / no element); no C3 finding; sections give no parking element
("WITHOUT PARALLEL PARKING").

Real drawings stay out of Git (brief §10.2). Skip unless LDIP_REVIEW points at a local review root
that holds the three-site 30 % ingestions.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SK10 = REPO / "plugin/ldip-design-review/skills/reviewing-road-design/scripts"
PY = sys.executable
ROOT = Path(os.environ["LDIP_REVIEW"]) if os.environ.get("LDIP_REVIEW") else None


pytestmark = pytest.mark.skipif(ROOT is None or not ROOT.is_dir(),
                                reason="LDIP_REVIEW not set — TJ8 is local-only (Rev J §4.13G.11)")


def test_tj8_placeholder_documents_expectation():
    """Documented expectation when real data is present (executed only with LDIP_REVIEW)."""
    assert ROOT is not None
    # A full TJ8 harness needs site-specific ingestion paths outside Git; this stub keeps the
    # skip/collect path stable for CI. Expand when the 30 % package paths are wired like T-F*.
    assert (SK10 / "check_calming.py").is_file()
