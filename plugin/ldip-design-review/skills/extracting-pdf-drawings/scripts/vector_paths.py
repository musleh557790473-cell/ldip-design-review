"""Vector paths of a PDF drawing with their colours — the optional side file `<file>.paths.json` of skill 2
(SPC-0006 Rev F §4.13C.3, DF-5 [AGREED] 15 Oct 2026 23:58).

Read with pdfplumber (the second reader of skill 2; it applies the content-stream transforms). Each line,
curve and rectangle of a page is kept with its stroke or fill colour and its points in PDF points from the
displayed page's top-left corner (the `.pdf.json` bbox convention). Only chromatic colours (largest RGB
channel difference >= CHROMA_MIN) keep their points: greys and black (base plan, frames, text) are counted
per colour only, so the file stays small. Used by skill 10 `read_class.py` method M4 (legend colour of the
road hierarchy plan); colour is never sole evidence (brief §5.5).
No network, no shell calls.
"""
__version__ = "0.1.0"

import collections

CHROMA_MIN = 0.10
ROUND = 2


def rgb(c):
    if isinstance(c, (list, tuple)) and len(c) == 3 and all(isinstance(v, (int, float)) for v in c):
        return tuple(round(float(v), 3) for v in c)
    return None


def chromatic(c):
    return c is not None and max(c) - min(c) >= CHROMA_MIN


def key(c):
    return "(" + ",".join(f"{v:.3f}" for v in c) + ")"


def read(pdf_path, need):
    pdfplumber, = need("pdfplumber")
    pages = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pg in pdf.pages:
            groups = collections.OrderedDict()
            counts = collections.Counter()
            for o in sorted(pg.lines + pg.curves + pg.rects, key=lambda o: (o["top"], o["x0"], o["x1"], o["bottom"])):
                c, kind = None, None
                if o.get("stroke"):
                    c, kind = rgb(o.get("stroking_color")), "stroke"
                if c is None and o.get("fill"):
                    c, kind = rgb(o.get("non_stroking_color")), "fill"
                if c is None:
                    counts["(other)"] += 1
                    continue
                k = key(c)
                counts[k] += 1
                if not chromatic(c):
                    continue
                pts = o.get("pts") or [(o["x0"], o["top"]), (o["x1"], o["bottom"])]
                g = groups.setdefault(k, {"rgb": list(c), "paths": []})
                g["paths"].append({"kind": kind, "width": round(float(o.get("linewidth") or 0.0), 3),
                                   "points": [[round(float(x), ROUND), round(float(y), ROUND)] for x, y in pts]})
            pages.append({"page": pg.page_number, "width_pt": round(float(pg.width), 2),
                          "height_pt": round(float(pg.height), 2),
                          "colour_counts": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
                          "colours": dict(sorted(groups.items()))})
    return pages


def build(pdf_path, source_path, sha, need):
    from importlib.metadata import version
    return {"schema_version": "1.0", "source": {"path": source_path, "sha256": sha},
            "reader": {"script": "vector_paths.py", "version": __version__, "pdfplumber": version("pdfplumber"),
                       "chroma_min": CHROMA_MIN},
            "pages": read(pdf_path, need), "warnings": []}
