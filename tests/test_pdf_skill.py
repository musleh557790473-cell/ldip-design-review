"""Tests for skill 2 extracting-pdf-drawings (SPC-0006 §4.2) and its links: write_manifest.py PDF
entries, the ingestion schema PDF branch and the Verifier's pdf_vector_text re-read.
Synthetic PDFs made with reportlab; the real PDFs (§4.2.6 T1, T3) run only with LDIP_PDF_SAMPLES
(never in Git, brief §10.2)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pdfplumber")
pytest.importorskip("pypdfium2")
reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A3, landscape  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "plugin/ldip-design-review"
SK = PLUGIN / "skills/extracting-pdf-drawings/scripts"
SK1 = PLUGIN / "skills/extracting-dwg-data/scripts"
SV = PLUGIN / "skills/verifying-review-findings/scripts"
PY = sys.executable
DOC = "LDIP-RCRC-SAP-01-QRWN-MPL-DWG-00-11000"
HAS_TESS = shutil.which("tesseract") is not None


def run(script, *args, base=SK):
    p = subprocess.run([PY, str(base / script), *map(str, args)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def make_pdf(path, scanned_page=True, rotate=0):
    from PIL import Image, ImageDraw, ImageFont
    w, h = landscape(A3)
    c = canvas.Canvas(str(path), pagesize=(w, h))
    c.setFont("Helvetica", 12)
    c.drawString(100, h - 140, DOC)
    c.setFillColorRGB(1, 0, 0)
    c.drawString(100, h - 190, "Plot 5911 area 489.00 m2")
    c.showPage()
    if scanned_page:
        img = Image.new("RGB", (2480, 1754), "white")
        d = ImageDraw.Draw(img)
        try:
            f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 60)
        except OSError:
            f = ImageFont.load_default()
        d.text((200, 400), "BENCHMARK 130455", fill="black", font=f)
        png = path.with_suffix(".png")
        img.save(png)
        c.drawImage(str(png), 0, 0, w, h)
        c.showPage()
    c.save()
    return path


@pytest.fixture
def pdf(tmp_path):
    return make_pdf(tmp_path / f"{DOC}.pdf")


def extract(tmp_path, pdf_path, batch="b01"):
    out = tmp_path / batch / (pdf_path.name + ".json")
    rc, res = run("extract_pdf.py", "--pdf", pdf_path, "--out", out, "--source-path", pdf_path.name)
    return rc, res, out


# ------------------------------------------------------------------ extract
def test_extract_vector_and_scanned(tmp_path, pdf):
    rc, res, out = extract(tmp_path, pdf)
    assert rc == 0 and res["pages"] == 2 and res["scanned_pages"] == [2] and res["unreadable_pages"] == []
    doc = json.loads(out.read_text())
    p1, p2 = doc["content"]["pages"]
    assert [s["text"] for s in p1["spans"]][:2] == [DOC, "Plot"]
    assert p1["spans"][0]["bbox"][1] < p1["spans"][1]["bbox"][1]           # top-left origin, sorted
    assert not p1["scanned"] and p2["scanned"] and p2["raster_ratio"] >= 0.9 and p2["char_count"] == 0
    assert p1["colours_complete"] and p1["objects"] >= 1 and doc["extractor"]["converter"]["name"] == "pypdfium2"
    assert any(c["colour"].startswith("(1.000,0.000,0.000") for c in p1["colours_used"])
    assert doc["source"]["document_no"] == DOC and doc["title_block"] is None
    codes = {w["code"] for w in doc["warnings"]}
    assert {"TITLE_BLOCK_NOT_FOUND", "CALLOUT_STYLE_UNKNOWN", "SCANNED_PAGES"} <= codes
    rc, v = run("validate_ingestion.py", "--files", out, "--root", tmp_path, base=SK1)
    assert rc == 0, v


def test_extract_deterministic_except_timestamp(tmp_path, pdf):
    _, _, a = extract(tmp_path, pdf, "b01")
    _, _, b = extract(tmp_path, pdf, "b02")
    da, db = json.loads(a.read_text()), json.loads(b.read_text())
    da["meta"] = db["meta"] = None
    assert da == db


def test_detect_scanned_and_other_values(tmp_path, pdf):
    _, _, out = extract(tmp_path, pdf)
    rc, res = run("detect_scanned.py", "--ingestion", out)
    assert rc == 0 and res["scanned"] == [2] and res["differs_from_recorded"] == []
    rc, res = run("detect_scanned.py", "--ingestion", out, "--max-chars", "100", "--min-cover", "0.0")
    assert res["scanned"] == [1, 2] and res["differs_from_recorded"] == [1]


def test_extract_input_errors(tmp_path, pdf):
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"not a pdf")
    assert run("extract_pdf.py", "--pdf", bad, "--out", tmp_path / "x.pdf.json")[0] == 2
    assert run("extract_pdf.py", "--pdf", tmp_path / "missing.pdf", "--out", tmp_path / "missing.pdf.json")[0] == 2
    assert run("extract_pdf.py", "--pdf", pdf, "--out", tmp_path / "wrong-name.json")[0] == 2
    assert run("extract_pdf.py", "--pdf", pdf, "--out", tmp_path / ("x" * 260) / (pdf.name + ".json"))[0] == 2


def test_unreadable_page_is_recorded_not_fatal(tmp_path, pdf, monkeypatch):
    sys.path.insert(0, str(SK))
    import extract_pdf as ep
    real = ep.page_record

    def flaky(page, n, raw):
        if n == 2:
            raise ValueError("damaged content stream")
        return real(page, n, raw)

    monkeypatch.setattr(ep, "page_record", flaky)
    doc = ep.extract(str(pdf), pdf.name)
    p2 = doc["content"]["pages"][1]
    assert p2["error"].startswith("ValueError") and p2["spans"] == []
    assert any(w["code"] == "PAGE_UNREADABLE" and "page 2" in w["detail"] for w in doc["warnings"])


# ------------------------------------------------------------------ OCR
@pytest.mark.skipif(not HAS_TESS, reason="tesseract not installed")
def test_ocr_scanned_page(tmp_path, pdf):
    _, _, out = extract(tmp_path, pdf)
    rc, res = run("ocr_tiles.py", "--ingestion", out, "--pdf", pdf)
    if rc == 3:
        pytest.skip(res["detail"])                     # Arabic data missing on this machine
    assert rc == 0 and res["pages_ocr"] == [2]
    doc = json.loads(out.read_text())
    p1, p2 = doc["content"]["pages"]
    words = [s["text"] for s in p2["ocr_spans"]]
    assert "BENCHMARK" in words and "130455" in words
    assert all(s["source_type"] == "pdf_ocr" for s in p2["ocr_spans"]) and p1["ocr_spans"] == []
    assert len(words) == len(set(words))                             # no duplicates from tile overlaps
    assert doc["content"]["ocr"]["engine"] == "tesseract" and any(w["code"] == "OCR_TEXT" for w in doc["warnings"])
    assert run("validate_ingestion.py", "--files", out, base=SK1)[0] == 0


def test_ocr_refuses_other_pdf(tmp_path, pdf):
    _, _, out = extract(tmp_path, pdf)
    other = make_pdf(tmp_path / "other.pdf", scanned_page=False)
    rc, res = run("ocr_tiles.py", "--ingestion", out, "--pdf", other)
    assert rc == 2 and "differs" in res["detail"]


def test_ocr_tiles_cover_page_without_gaps():
    sys.path.insert(0, str(SK))
    import ocr_tiles as ot
    for w, h in ((1500, 900), (4961, 3508), (14043, 9933)):
        cov = [[False] * 4 for _ in range(4)]
        tl = list(ot.tiles(w, h))
        xs = sorted({t[0][0] for t in tl}) + [w]
        assert max(t[0][2] for t in tl) == w and max(t[0][3] for t in tl) == h
        for (x0, y0, x1, y1), _ in tl:
            assert x1 - x0 <= ot.OCR_TILE_PX and y1 - y0 <= ot.OCR_TILE_PX
        assert all(b - a <= ot.OCR_TILE_PX for a, b in zip(xs, xs[1:])), cov


# ------------------------------------------------------------------ crop
def test_crop_stable_hash_and_errors(tmp_path, pdf):
    a, b = tmp_path / "e" / "f1_1.png", tmp_path / "e" / "f1_2.png"
    rc1, r1 = run("crop.py", "--pdf", pdf, "--page", 1, "--bbox", 95, 175, 260, 195, "--out", a)
    rc2, r2 = run("crop.py", "--pdf", pdf, "--page", 1, "--bbox", 95, 175, 260, 195, "--out", b)
    assert rc1 == rc2 == 0 and r1["sha256"] == r2["sha256"] == hashlib.sha256(a.read_bytes()).hexdigest()
    assert Path(str(a) + ".sha256").read_text().strip() == r1["sha256"]
    assert abs(r1["pixels"][0] - (260 - 95) * 200 / 72) <= 1            # pdfium rounds the page edges
    assert run("crop.py", "--pdf", pdf, "--page", 9, "--bbox", 0, 0, 10, 10, "--out", a)[0] == 2
    assert run("crop.py", "--pdf", pdf, "--page", 1, "--bbox", 0, 0, 5000, 10, "--out", a)[0] == 2
    assert run("crop.py", "--pdf", pdf, "--page", 1, "--bbox", 10, 10, 5, 20, "--out", a)[0] == 2
    assert run("crop.py", "--pdf", pdf, "--page", 1, "--bbox", 0, 0, 10, 10, "--out", tmp_path / "x.jpg")[0] == 2
    assert run("crop.py", "--pdf", pdf, "--page", 1, "--bbox", 0, 0, 10, 10, "--dpi", 2000, "--out", a)[0] == 2


# ------------------------------------------------------------------ manifest (skill 1 write_manifest.py)
def test_write_manifest_pdf_entries(tmp_path, pdf):
    root = tmp_path / "root"
    sub = root / "submittals/QRWN/T0001"
    sub.mkdir(parents=True)
    src = sub / pdf.name
    shutil.copy(pdf, src)
    bdir = root / "0.ai_context/QRWN/T0001/b01"
    bdir.mkdir(parents=True)
    rel = f"submittals/QRWN/T0001/{pdf.name}"
    assert run("extract_pdf.py", "--pdf", src, "--out", bdir / (pdf.name + ".json"), "--source-path", rel)[0] == 0
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    missing = sub / "missing.pdf"
    shutil.copy(pdf, missing)
    rc, res = run("write_manifest.py", "--batch-dir", bdir, "--run", "QRWN-T0001-20261005T1200", "--site", "QRWN",
                  "--sub", "T0001", "--file", rel, sha, "--file", "submittals/QRWN/T0001/missing.pdf", sha,
                  "--root", root, base=SK1)
    assert rc == 0, res
    m = json.loads((bdir / "batch_manifest.json").read_text())
    by = {Path(f["path"]).name: f for f in m["files"]}
    assert by[pdf.name]["status"] == "extracted" and by[pdf.name]["output"] == pdf.name + ".json"
    assert by["missing.pdf"]["status"] == "failed" and by["missing.pdf"]["error"]["code"] == 2
    assert m["converter"] == {"name": None, "version": None} and m["counts"]["extracted"] == 1
    # hash in the file list differs from the ingestion file → failed, file kept as intermediate (C3)
    rc, res = run("write_manifest.py", "--batch-dir", bdir, "--run", "QRWN-T0001-20261005T1200", "--site", "QRWN",
                  "--sub", "T0001", "--file", rel, "0" * 64, base=SK1)
    m = json.loads((bdir / "batch_manifest.json").read_text())
    assert m["files"][0]["status"] == "failed" and m["files"][0]["intermediates"] == [pdf.name + ".json"]


def test_schema_rejects_bad_pdf_content(tmp_path, pdf):
    _, _, out = extract(tmp_path, pdf)
    doc = json.loads(out.read_text())
    doc["content"]["pages"][0]["ocr_spans"] = [{"text": "x", "bbox": [0, 0, 1, 1], "conf": 90, "source_type": "pdf_vector_text"}]
    bad = tmp_path / "b9" / out.name
    bad.parent.mkdir()
    bad.write_text(json.dumps(doc))
    assert run("validate_ingestion.py", "--files", bad, base=SK1)[0] == 4
    doc = json.loads(out.read_text())
    doc["extractor"]["skill"] = "extracting-dwg-data"
    bad.write_text(json.dumps(doc))
    assert run("validate_ingestion.py", "--files", bad, base=SK1)[0] == 4


# ------------------------------------------------------------------ Verifier: pdf_vector_text second reader
def pdf_candidate(rel, sha, quote, bbox=(95, 175, 260, 195), kind="pdf_vector_text"):
    return {"finding_id": "QRWN-T0001-20261005T1200/INT-001", "reference": {"rule_id": "PKG-005"},
            "location": {"document_no": DOC, "file_path": rel, "file_sha256": sha, "pdf": {"page": 1, "bbox_pt": list(bbox)}},
            "evidence": [{"evidence_id": "E01", "source_type": kind, "extract": f'p1 "{quote}"'}]}


@pytest.mark.parametrize("quote,kind,expected", [
    ("Plot 5911 area 489.00 m2", "pdf_vector_text", "agrees"),
    ("plot  5911", "pdf_vector_text", "agrees"),                 # spaces / case normalised
    ("Plot 5912", "pdf_vector_text", "disagrees"),
    (DOC, "pdf_vector_text", "disagrees"),                        # outside the quoted box
    ("Plot 5911", "pdf_ocr", "not_rechecked"),
])
def test_recheck_pdf_vector_text(tmp_path, pdf, quote, kind, expected):
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    cand = tmp_path / "c.json"
    cand.write_text(json.dumps([pdf_candidate(pdf.name, sha, quote, kind=kind)]))
    rc, res = run("recheck.py", "--candidates", cand, "--root", tmp_path, "--out", tmp_path / "rc.json", base=SV)
    assert rc == 0
    r = list(json.loads((tmp_path / "rc.json").read_text())["results"].values())[0]
    assert r["recheck_result"] == expected, r["method"]


def test_recheck_pdf_changed_file(tmp_path, pdf):
    cand = tmp_path / "c.json"
    cand.write_text(json.dumps([pdf_candidate(pdf.name, "0" * 64, "Plot 5911")]))
    run("recheck.py", "--candidates", cand, "--root", tmp_path, "--out", tmp_path / "rc.json", base=SV)
    r = list(json.loads((tmp_path / "rc.json").read_text())["results"].values())[0]
    assert r["recheck_result"] == "disagrees" and "hash" in r["method"]


# ------------------------------------------------------------------ guard
def test_guard_allows_pdf_commands(tmp_path):
    sys.path.insert(0, str(REPO / "tests"))
    from test_plugin_scripts import RUN, guard  # noqa: E402
    root = tmp_path / "LDIP & Review"
    for d in ("runs", "rulebook", "0.ai_context/QRWN/T0001/b01", "submittals/QRWN/T0001", "findings"):
        (root / d).mkdir(parents=True, exist_ok=True)
    S, F = f"{PLUGIN}/skills/extracting-pdf-drawings/scripts", "submittals/QRWN/T0001/A-001.pdf"
    B, E = "0.ai_context/QRWN/T0001/b01", f"findings/{RUN}/evidence/verifier"
    intake = [f'python "{S}/extract_pdf.py" --pdf {F} --out {B}/A-001.pdf.json --source-path {F}',
              f'python "{S}/ocr_tiles.py" --ingestion {B}/A-001.pdf.json --pdf {F}']
    for c in intake:
        assert guard(root, "Bash", {"command": c}, agent_type="ldip-design-review:intake-batch") == "allow", c
    crop = f'python "{S}/crop.py" --pdf {F} --page 1 --bbox 0 0 100 50 --dpi 200 --out {E}/INT-001_1.png'
    assert guard(root, "Bash", {"command": crop}, agent_type="ldip-design-review:verifier") == "allow"
    assert guard(root, "Bash", {"command": intake[0]}, agent_type="ldip-design-review:gis-cadastral") == "deny"


# ------------------------------------------------------------------ real PDFs (local only)
REAL = os.environ.get("LDIP_PDF_SAMPLES")


@pytest.mark.skipif(not REAL, reason="LDIP_PDF_SAMPLES not set (real PDFs stay out of Git)")
def test_real_pdfs(tmp_path):
    pdfs = sorted(Path(REAL).glob("*.pdf"))
    assert pdfs
    for p in pdfs:
        rc, res, out = extract(tmp_path, p)
        assert rc == 0, res
        assert run("validate_ingestion.py", "--files", out, base=SK1)[0] == 0


# ------------------------------------------------------------------ session 8: readers swapped (owner, 5 Oct 2026)
def test_strip_scan_detected(tmp_path):
    """T3 case: a page made of seven image strips (~14 % each) is scanned (all images together)."""
    from PIL import Image
    w, h = landscape(A3)
    strip = tmp_path / "strip.png"
    Image.new("RGB", (800, 100), "white").save(strip)
    c = canvas.Canvas(str(tmp_path / "strips.pdf"), pagesize=(w, h))
    for k in range(7):
        c.drawImage(str(strip), 0, h - (k + 1) * h / 7, w, h / 7)
    c.showPage()
    c.save()
    rc, res, out = extract(tmp_path, tmp_path / "strips.pdf")
    p = json.loads(out.read_text())["content"]["pages"][0]
    assert rc == 0 and res["scanned_pages"] == [1] and p["image_cover"] < 0.2 and p["raster_ratio"] >= 0.99


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_rotated_page_boxes_match_the_render(tmp_path, rot):
    """Word boxes are in displayed coordinates: a crop at the box shows ink, a crop beside it none."""
    from PIL import Image
    w, h = A3
    c = canvas.Canvas(str(tmp_path / f"r{rot}.pdf"), pagesize=(w, h))
    c.setPageRotation(rot)
    c.setFont("Helvetica", 40)
    c.drawString(100, 300, "ROTATED")          # inside the media box whatever the rotation
    c.showPage()
    c.save()
    pdf_path = tmp_path / f"r{rot}.pdf"
    rc, _, out = extract(tmp_path, pdf_path)
    p = json.loads(out.read_text())["content"]["pages"][0]
    assert rc == 0 and p["rotation"] == rot and [s_["text"] for s_ in p["spans"]] == ["ROTATED"]
    x0, top, x1, bottom = p["spans"][0]["bbox"]
    assert 0 <= x0 < x1 <= p["width_pt"] and 0 <= top < bottom <= p["height_pt"]

    def ink(box, name):
        rc_, r_ = run("crop.py", "--pdf", pdf_path, "--page", 1, "--bbox", *box, "--dpi", 100, "--out", tmp_path / name)
        assert rc_ == 0, r_
        im = Image.open(tmp_path / name).convert("L")
        px = im.tobytes()
        return sum(1 for v in px if v < 128) / len(px)

    assert ink((x0, top, x1, bottom), "in.png") > 0.05
    far = (5, 5, 25, 25) if x0 > 60 or top > 60 else (p["width_pt"] - 30, p["height_pt"] - 30, p["width_pt"] - 5, p["height_pt"] - 5)
    assert ink(far, "out.png") == 0.0


def test_large_page_colours_and_recheck(tmp_path, pdf, monkeypatch):
    sys.path.insert(0, str(SK))
    import extract_pdf as ep
    monkeypatch.setattr(ep, "LARGE_PAGE_OBJECTS", 0)
    doc = ep.extract(str(pdf), pdf.name)
    p1 = doc["content"]["pages"][0]
    assert not p1["colours_complete"] and any(w_["code"] == "LARGE_PAGE" for w_ in doc["warnings"])
    assert p1["colours_used"]                                    # text colours are still read
    sys.path.insert(0, str(SV))
    import recheck as rk
    monkeypatch.setattr(rk, "LARGE_PAGE_OBJECTS", 0)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    res, method, _ = rk.recheck_pdf_text(pdf_candidate(pdf.name, sha, "Plot 5911"), str(tmp_path))
    assert res == "not_rechecked" and "objects" in method


# ------------------------------------------------------------------ picture regions inside vector pages (owner, 5 Oct 2026)
def test_merge_regions_joins_strips():
    sys.path.insert(0, str(SK))
    import pdf_common as pc
    strips = [[0, k * 10.0, 100, (k + 1) * 10.0] for k in range(7)] + [[500, 500, 510, 510]]
    regs = pc.merge_regions(strips)
    assert regs[0] == [[0, 0.0, 100, 70.0], 7] and regs[1][1] == 1


@pytest.mark.skipif(not HAS_TESS, reason="tesseract not installed")
def test_ocr_picture_region_in_vector_page(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    w, h = landscape(A3)
    img = Image.new("RGB", (1600, 1000), "white")
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 90)
    except OSError:
        f = ImageFont.load_default()
    ImageDraw.Draw(img).text((100, 400), "PARCEL 5911", fill="black", font=f)
    png = tmp_path / "pic.png"
    img.save(png)
    logo = tmp_path / "logo.png"
    Image.new("RGB", (40, 40), "black").save(logo)
    c = canvas.Canvas(str(tmp_path / "mixed.pdf"), pagesize=(w, h))
    c.setFont("Helvetica", 12)
    c.drawString(100, h - 140, "VECTOR TITLE")
    c.drawImage(str(png), 600, h - 500, 400, 250)                 # picture region: OCR'd
    c.drawImage(str(logo), 50, 50, 30, 30)                        # small logo: not OCR'd
    c.drawString(620, h - 300, "OVERLAY")                         # vector text on the picture: not repeated
    c.showPage()
    c.save()
    rc, res, out = extract(tmp_path, tmp_path / "mixed.pdf")
    assert rc == 0 and res["scanned_pages"] == [] and res["image_region_pages"] == [1]
    regs = json.loads(out.read_text())["content"]["pages"][0]["image_regions"]
    assert [r["ocr_candidate"] for r in regs] == [True, False]
    rc, res = run("ocr_tiles.py", "--ingestion", out, "--pdf", tmp_path / "mixed.pdf")
    if rc == 3:
        pytest.skip(res["detail"])
    assert rc == 0 and res["image_regions"] == 1
    sp = json.loads(out.read_text())["content"]["pages"][0]["ocr_spans"]
    words = [s_["text"] for s_ in sp]
    assert "PARCEL" in words and "5911" in words and "OVERLAY" not in words
    assert all(s_["region"] == "image" and 600 <= s_["bbox"][0] <= 1000 for s_ in sp)
    assert run("validate_ingestion.py", "--files", out, base=SK1)[0] == 0


# ------------------------------------------------------------------ title block (K5-1)
TB_NO = "LDIP-RCRC-SAP-03-DN-ROD-DWG-10200-001"


def make_tb_pdf(path, scale=1.0, rotate=0, number=TB_NO, rev="00"):
    """Parsons-like title block: labels and values at the bottom right of an A1 sheet (scaled for A0)."""
    w, h = 2384 * scale, 1684 * scale
    c = canvas.Canvas(str(path), pagesize=(w, h))
    if rotate:
        c.setPageRotation(rotate)

    def put(x, top, text, size=5):
        c.setFont("Helvetica", size * scale)
        c.drawString(x * scale, h - (top + size) * scale, text)
    put(400, 400, "ROAD PLAN BODY TEXT Drawing No.: should not be read")
    put(1966, 1240, "Purpose of Issue:")
    put(2081, 1248, "ISSUED FOR APPROVAL", 10)
    put(1966, 1269, "Client:")
    put(1966, 1537, "Drawing Title:")
    put(2111, 1548, "ROAD LAYOUT", 10)
    for x, lab in ((1966, "Scale at A1:"), (2065, "Date:"), (2164, "Revision:"), (2263, "Sheet:")):
        put(x, 1609, lab)
    for x, val in ((2010, "1:1000M"), (2090, "SEPT. 2026"), (2218, rev), (2296, "01 OF 05")):
        put(x, 1614, val, 8)
    put(1966, 1632, "Drawing No.:")
    put(2054, 1638, number, 10)
    c.showPage()
    c.save()
    return path


@pytest.mark.parametrize("scale,rotate", [(1.0, 0), (1.4135, 0), (1.0, 90)])
def test_title_block_label_anchored(tmp_path, scale, rotate):
    pdf_path = make_tb_pdf(tmp_path / f"{TB_NO}-00.pdf", scale=scale, rotate=rotate)
    rc, res, out = extract(tmp_path, pdf_path)
    assert rc == 0
    doc = json.loads(out.read_text())
    tb = doc["title_block"]
    codes = {w["code"] for w in doc["warnings"]}
    if rotate:
        # a page rotated after drawing puts the block elsewhere on the displayed page: not read
        assert tb is None and "TITLE_BLOCK_NOT_FOUND" in codes
        return
    assert tb["document_no"] == TB_NO and tb["revision"] == "00" and tb["title"] == "ROAD LAYOUT"
    assert tb["sheet"] == "01 OF 05" and tb["date"] == "SEPT. 2026" and tb["status"] == "ISSUED FOR APPROVAL"
    assert tb["scale"] == "1:1000M" and tb["fields"]["document_no"]["bbox"] is not None
    assert doc["content"]["pages"][0]["title_block"]["document_no"] == TB_NO
    assert "TITLE_BLOCK_NOT_FOUND" not in codes and "TITLE_BLOCK_ZONE_UNCONFIRMED" not in codes
    rc, v = run("validate_ingestion.py", "--files", out, "--root", tmp_path, base=SK1)
    assert rc == 0, v


def test_title_block_empty_value_warns(tmp_path):
    pdf_path = make_tb_pdf(tmp_path / f"{TB_NO}-00.pdf", rev="")
    _, _, out = extract(tmp_path, pdf_path)
    doc = json.loads(out.read_text())
    assert doc["title_block"]["revision"] is None
    assert any(w["code"] == "TITLE_BLOCK_FIELD_EMPTY" and "revision" in w["detail"] for w in doc["warnings"])
