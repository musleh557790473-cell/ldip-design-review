"""PDF raster pairing for skill 7 M1(c) (S31 PR #18 review R1; Rev M §4.15 DM-2 / DM-9).

Render each PDF page (pypdfium2), convert to grey, resize to fixed size, mask the title-block
strip on the right, cost = mean absolute difference, one-to-one assignment (Hungarian).
Margin = 2nd-best / best; weak when margin < weak_margin. All results proposed / Probable.

No network, no shell calls.
"""
__version__ = "0.1.0"

from pathlib import Path

import numpy as np

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PARAMS = SKILL_DIR / "references" / "params.json"


def load_raster_params(path=None):
    import json
    p = Path(path) if path else DEFAULT_PARAMS
    raw = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    r = raw.get("raster") or {}
    return {
        "dpi": int(r.get("dpi") or 25),
        "width": int(r.get("width") or 400),
        "height": int(r.get("height") or 283),
        "mask_right_pct": float(r.get("mask_right_pct") if r.get("mask_right_pct") is not None else 0.18),
        "weak_margin": float(r.get("weak_margin") if r.get("weak_margin") is not None else 1.5),
        "tag": r.get("tag") or "[PROPOSED — DM-9, from T7-R1 probe]",
        "verification_status_cap": r.get("verification_status_cap") or "Probable",
    }


def render_page_grey(pdf_path, dpi, width, height, page_index=0):
    """Return uint8 HxW greyscale array for one PDF page, resized to (height, width)."""
    import pypdfium2 as pdfium
    from PIL import Image

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        if page_index < 0 or page_index >= len(doc):
            raise ValueError(f"page {page_index} outside 0–{len(doc) - 1} for {pdf_path}")
        page = doc[page_index]
        bmp = page.render(scale=dpi / 72.0)
        img = bmp.to_pil().convert("L")
        img = img.resize((width, height), Image.Resampling.BILINEAR)
        return np.asarray(img, dtype=np.uint8)
    finally:
        doc.close()


def mask_title_block(arr, mask_right_pct):
    """Zero the right-hand title-block strip (fraction of width)."""
    out = arr.copy()
    if mask_right_pct <= 0:
        return out
    w = out.shape[1]
    cut = max(0, min(w, int(round(w * (1.0 - mask_right_pct)))))
    out[:, cut:] = 0
    return out


def prepare_raster(pdf_path, params, page_index=0):
    arr = render_page_grey(pdf_path, params["dpi"], params["width"], params["height"], page_index)
    return mask_title_block(arr, params["mask_right_pct"])


def mad_cost(a, b):
    """Mean absolute difference in [0, 255]."""
    return float(np.mean(np.abs(a.astype(np.float32) - b.astype(np.float32))))


def assign_one_to_one(cost):
    """Hungarian assignment. cost: (n_prev, n_curr). Returns list of (i, j, cost_ij)."""
    from scipy.optimize import linear_sum_assignment

    if cost.size == 0:
        return []
    # Pad to square with large costs so extras stay unmatched
    n, m = cost.shape
    size = max(n, m)
    pad = np.full((size, size), 1e6, dtype=np.float64)
    pad[:n, :m] = cost
    ri, cj = linear_sum_assignment(pad)
    out = []
    for i, j in zip(ri, cj):
        if i < n and j < m and pad[i, j] < 1e5:
            out.append((int(i), int(j), float(cost[i, j])))
    return out


def margins_for_assignment(cost, assignments):
    """For each assigned (i,j), margin = 2nd_best_in_row_or_col / best. Prefer row view."""
    n, m = cost.shape
    out = {}
    for i, j, c in assignments:
        row = cost[i, :]
        order = np.argsort(row)
        best = float(row[order[0]])
        second = float(row[order[1]]) if m > 1 else float("inf")
        # Also consider column competitors
        col = cost[:, j]
        corder = np.argsort(col)
        cbest = float(col[corder[0]])
        csecond = float(col[corder[1]]) if n > 1 else float("inf")
        # Use the tighter (smaller) margin
        marg_r = (second / best) if best > 1e-9 else float("inf")
        marg_c = (csecond / cbest) if cbest > 1e-9 else float("inf")
        margin = min(marg_r, marg_c)
        out[(i, j)] = margin
    return out


def pair_unmatched_by_raster(prev_items, curr_items, params, get_pdf):
    """One-to-one raster pairing of unmatched drawings.

    prev_items / curr_items: list of drawing dicts (must still be unmatched).
    get_pdf(side) -> Path or None.
    Returns list of dicts: {prev, curr, mad, margin, weak, match}.
    """
    prev_ok, curr_ok = [], []
    prev_arrs, curr_arrs = [], []
    for p in prev_items:
        pdf = get_pdf(p)
        if not pdf or not Path(pdf).is_file():
            continue
        try:
            prev_arrs.append(prepare_raster(pdf, params))
            prev_ok.append(p)
        except Exception:
            continue
    for c in curr_items:
        pdf = get_pdf(c)
        if not pdf or not Path(pdf).is_file():
            continue
        try:
            curr_arrs.append(prepare_raster(pdf, params))
            curr_ok.append(c)
        except Exception:
            continue
    if not prev_ok or not curr_ok:
        return []
    cost = np.zeros((len(prev_ok), len(curr_ok)), dtype=np.float64)
    for i, a in enumerate(prev_arrs):
        for j, b in enumerate(curr_arrs):
            cost[i, j] = mad_cost(a, b)
    assignments = assign_one_to_one(cost)
    margins = margins_for_assignment(cost, assignments)
    weak_lim = params["weak_margin"]
    results = []
    for i, j, c in assignments:
        margin = margins[(i, j)]
        weak = bool(margin < weak_lim)
        results.append({
            "previous": prev_ok[i],
            "current": curr_ok[j],
            "mad": c,
            "margin": None if margin == float("inf") else round(margin, 4),
            "weak": weak,
            "match": "raster_mad",
        })
    return results
