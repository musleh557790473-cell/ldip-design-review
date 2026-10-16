# ldip-design-review (AICC JV — LDIP Design Review Agents)

Code repository for the LDIP design review multi-agent system.
Single source of truth for requirements: AICC-BIM-SPC-0001 Rev F and SPC-0002…0006 (Claude Project
"LDIP Design Review Agents – Build").

**This repository holds code only** (brief §10.1–10.2): skills, scripts, agent definitions, hooks,
JSON schemas and the rulebook *structure*. It shall never contain Parsons files, contract documents,
DLUS, rule values (`rules_v<N>.json`), or any Golden Set content. `.gitignore` and
`tools/check_no_data.py` (run before every commit) enforce this.

| Folder | Content |
|---|---|
| `plugin/ldip-design-review/` | The plugin (SPC-0005 §9.4) |
| `tools/a1-probe/` | Stage 3 test A1 probe plugin (test only) |
| `tools/k1-env-check/` | Stage 3 test K1 environment check |
| `tools/check_no_data.py` | Pre-commit guard for rule 5 |
| `specs-machine/` | Machine copies of spec tables (e.g. SPC-0002 §5 access matrix source) |
| `tests/` | Pytest suites and synthetic fixtures (no project data) |

## Run environment (cloud run session)

Install before an install check or a review run (D13: ezdwg is pinned so that test runs stay
comparable; `convert.py` warns `ENGINE_VERSION_UNPINNED` on any other version):

    pip install ezdwg==0.16.0 ezdxf pypdfium2 pdfplumber pytesseract shapely numpy scipy --break-system-packages
    apt-get install -y tesseract-ocr tesseract-ocr-ara

LibreDWG `dwg2dxf` (reader-chain step 2, SPC-0006 Rev F §4.1A.2) is not installed and `LDIP_LIBREDWG_EXE` is not
set until the licence review L-1 closes (DF-1).

Python 3.11 or later (suite last run on 3.13.16, session 10).
