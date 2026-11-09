"""Pure PDF operations used by the UI. No Qt imports here, so everything can be
tested and scripted on its own.

All functions work on PyMuPDF (pymupdf) Document / Page objects and PDF
coordinates (points, origin top-left, 72 pt = 1 inch).
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from datetime import datetime

import pymupdf

# ---------------------------------------------------------------- helpers

def parse_ranges(text: str, page_count: int) -> list[int]:
    """'1-3, 5, 8-' -> zero-based page indexes (keeps order, no duplicates)."""
    out: list[int] = []
    text = (text or "").replace(" ", "")
    if not text:
        return out
    for part in text.split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a else 1
            end = int(b) if b else page_count
        else:
            start = end = int(part)
        if start > end:
            rng = range(start, end - 1, -1)
        else:
            rng = range(start, end + 1)
        for p in rng:
            if 1 <= p <= page_count and (p - 1) not in out:
                out.append(p - 1)
    return out


def hex_to_rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore


def open_any(path: str, password: str | None = None) -> pymupdf.Document:
    """Open a PDF (or an image / XPS / EPUB, converted to PDF) fully in memory."""
    with open(path, "rb") as fh:
        data = fh.read()
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "pdf"
    src = pymupdf.open(stream=data, filetype=ext)
    encrypted = bool(src.needs_pass)
    if encrypted:
        if not password or not src.authenticate(password):
            raise PermissionError("Password required")
        # decrypted copy so we can edit freely. (Do not query is_encrypted /
        # needs_pass after authenticate – MuPDF then writes broken streams.)
        data = src.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)
        src.close()
        return pymupdf.open("pdf", data)
    if src.is_pdf:
        return src
    pdf_bytes = src.convert_to_pdf()
    src.close()
    return pymupdf.open("pdf", pdf_bytes)


def save_bytes(doc: pymupdf.Document, **kw) -> bytes:
    opts = dict(garbage=3, deflate=True)
    opts.update(kw)
    return doc.tobytes(**opts)


# ---------------------------------------------------------------- pages

def rotate_pages(doc, pages, degrees):
    for p in pages:
        page = doc[p]
        page.set_rotation((page.rotation + degrees) % 360)


def delete_pages(doc, pages):
    if len(set(pages)) >= doc.page_count:
        raise ValueError("A PDF must keep at least one page.")
    doc.delete_pages(sorted(set(pages)))


def reorder(doc, order: list[int]):
    doc.select(order)


def move_pages(doc, pages: list[int], target: int):
    """Move the given pages so they start at index `target` (in the result)."""
    pages = sorted(set(pages))
    rest = [i for i in range(doc.page_count) if i not in pages]
    target = max(0, min(target, len(rest)))
    doc.select(rest[:target] + pages + rest[target:])


def duplicate_page(doc, pno):
    doc.fullcopy_page(pno, pno + 1)


def insert_blank(doc, at, width=None, height=None):
    if width is None:
        ref = doc[min(max(at - 1, 0), doc.page_count - 1)].rect if doc.page_count else pymupdf.paper_rect("a4")
        width, height = ref.width, ref.height
    doc.new_page(pno=at, width=width, height=height)


def insert_file(doc, path, at, password=None):
    src = open_any(path, password)
    doc.insert_pdf(src, start_at=at)
    n = src.page_count
    src.close()
    return n


def merge_files(paths: list[str], out_path: str):
    out = pymupdf.open()
    for p in paths:
        src = open_any(p)
        out.insert_pdf(src)
        src.close()
    out.save(out_path, garbage=3, deflate=True)
    out.close()


def extract_pages(doc, pages, out_path):
    out = pymupdf.open()
    for p in pages:
        out.insert_pdf(doc, from_page=p, to_page=p)
    out.save(out_path, garbage=3, deflate=True)
    out.close()


def split_document(doc, out_dir, base, mode="every", value="1"):
    """mode: 'every' -> chunks of N pages; 'ranges' -> 'a-b; c-d'; 'size' -> max MB."""
    os.makedirs(out_dir, exist_ok=True)
    groups: list[list[int]] = []
    n = doc.page_count
    if mode == "every":
        k = max(1, int(value))
        groups = [list(range(i, min(i + k, n))) for i in range(0, n, k)]
    elif mode == "ranges":
        for part in str(value).split(";"):
            pg = parse_ranges(part, n)
            if pg:
                groups.append(pg)
    elif mode == "size":
        limit = float(value) * 1024 * 1024
        cur: list[int] = []
        for i in range(n):
            trial = cur + [i]
            tmp = pymupdf.open()
            for p in trial:
                tmp.insert_pdf(doc, from_page=p, to_page=p)
            size = len(tmp.tobytes(garbage=3, deflate=True))
            tmp.close()
            if size > limit and cur:
                groups.append(cur)
                cur = [i]
            else:
                cur = trial
        if cur:
            groups.append(cur)
    files = []
    width = len(str(len(groups)))
    for idx, g in enumerate(groups, 1):
        path = os.path.join(out_dir, f"{base}_part{str(idx).zfill(width)}.pdf")
        extract_pages(doc, g, path)
        files.append(path)
    return files


def crop_pages(doc, pages, margins):
    """margins = (left, top, right, bottom) in points, relative to current page."""
    l, t, r, b = margins
    for p in pages:
        page = doc[p]
        mb = page.mediabox
        cb = page.cropbox
        new = pymupdf.Rect(cb.x0 + l, cb.y0 + t, cb.x1 - r, cb.y1 - b) & mb
        if new.is_empty:
            raise ValueError("Margins are larger than the page.")
        page.set_cropbox(new)


# ---------------------------------------------------------------- annotations

def _color_annot(annot, color, fill=None, width=None, opacity=None):
    rgb = hex_to_rgb(color) if isinstance(color, str) else color
    colors = {"stroke": rgb}
    if fill:
        colors["fill"] = hex_to_rgb(fill) if isinstance(fill, str) else fill
    try:
        annot.set_colors(**colors)
    except Exception:
        pass
    if width is not None:
        try:
            annot.set_border(width=width)
        except Exception:
            pass
    if opacity is not None:
        annot.set_opacity(opacity)
    annot.set_info(title=os.environ.get("USERNAME") or os.environ.get("USER") or "PDF Studio")
    annot.update()
    return annot


def text_markup(page, rect, kind="highlight", color="#ffe600"):
    """Highlight / underline / strikeout the words inside rect. If no words are
    found the rectangle itself is highlighted."""
    rect = pymupdf.Rect(rect)
    words = [w for w in page.get_text("words") if pymupdf.Rect(w[:4]).intersects(rect)]
    quads = []
    if words:
        # merge words on the same line into one quad
        lines: dict[tuple, pymupdf.Rect] = {}
        for w in words:
            key = (w[5], w[6])  # block, line
            r = pymupdf.Rect(w[:4])
            lines[key] = lines[key] | r if key in lines else r
        quads = [r.quad for r in lines.values()]
    else:
        quads = [rect.quad]
    fn = {
        "highlight": page.add_highlight_annot,
        "underline": page.add_underline_annot,
        "strikeout": page.add_strikeout_annot,
        "squiggly": page.add_squiggly_annot,
    }[kind]
    annot = fn(quads)
    return _color_annot(annot, color)


def add_rect(page, rect, color="#e53935", width=2, fill=None, opacity=1.0):
    return _color_annot(page.add_rect_annot(rect), color, fill, width, opacity)


def add_ellipse(page, rect, color="#e53935", width=2, fill=None, opacity=1.0):
    return _color_annot(page.add_circle_annot(rect), color, fill, width, opacity)


def add_line(page, p1, p2, color="#e53935", width=2, arrow=True):
    annot = page.add_line_annot(p1, p2)
    if arrow:
        annot.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
    return _color_annot(annot, color, None, width)


def add_ink(page, strokes, color="#1e40af", width=2):
    annot = page.add_ink_annot(strokes)
    return _color_annot(annot, color, None, width)


def add_note(page, point, text, color="#ffd54f"):
    annot = page.add_text_annot(point, text, icon="Comment")
    return _color_annot(annot, color)


def add_textbox_annot(page, rect, text, fontsize=12, color="#000000", border=None):
    annot = page.add_freetext_annot(
        rect, text, fontsize=fontsize, fontname="helv",
        text_color=hex_to_rgb(color),
        border_color=hex_to_rgb(border) if border else None,
        fill_color=None,
    )
    annot.set_info(title=os.environ.get("USERNAME") or "PDF Studio")
    annot.update()
    return annot


STAMPS = {
    "Approved": 0, "As Is": 1, "Confidential": 2, "Departmental": 3,
    "Draft": 4, "Experimental": 5, "Expired": 6, "Final": 7,
    "For Comment": 8, "For Public Release": 9, "Not Approved": 10,
    "Not For Public Release": 11, "Sold": 12, "Top Secret": 13,
}


def add_stamp(page, rect, name="Approved"):
    annot = page.add_stamp_annot(rect, stamp=STAMPS.get(name, 0))
    annot.set_info(title=os.environ.get("USERNAME") or "PDF Studio")
    annot.update()
    return annot


def add_custom_stamp(page, rect, text, color="#c62828", with_date=True):
    """Draw a bordered text stamp permanently onto the page."""
    rect = pymupdf.Rect(rect)
    rgb = hex_to_rgb(color)
    label = text.upper()
    if with_date:
        label += "\n" + datetime.now().strftime("%d %b %Y")
    shape = page.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=rgb, width=2.5)
    inner = rect + (4, 4, -4, -4)
    lines = label.count("\n") + 1
    size = max(6, min(inner.height / (lines * 1.25), inner.width / max(len(s) for s in label.split("\n")) * 1.7))
    while size > 4:
        rc = shape.insert_textbox(inner, label, fontsize=size, fontname="hebo", color=rgb, align=1)
        if rc >= 0:
            break
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(color=rgb, width=2.5)
        size -= 1
    shape.commit()


def annot_at(page, point):
    pt = pymupdf.Point(point)
    hits = [a for a in page.annots() if a.rect.contains(pt)]
    if not hits:
        return None
    hits.sort(key=lambda a: a.rect.width * a.rect.height)
    return hits[0]


def delete_annot_at(page, point):
    a = annot_at(page, point)
    if a is None:
        return False
    page.delete_annot(a)
    return True


def move_annot(page, annot_xref, dx, dy):
    for a in page.annots():
        if a.xref == annot_xref:
            r = a.rect
            try:
                if a.type[0] in (pymupdf.PDF_ANNOT_INK,):
                    strokes = [[(x + dx, y + dy) for x, y in s] for s in a.vertices]
                    info, colors, border, op = a.info, a.colors, a.border, a.opacity
                    page.delete_annot(a)
                    na = page.add_ink_annot(strokes)
                    na.set_info(info)
                    na.set_colors(stroke=colors.get("stroke"))
                    na.set_border(width=border.get("width", 1))
                    na.set_opacity(op if op >= 0 else 1)
                    na.update()
                elif a.type[0] == pymupdf.PDF_ANNOT_LINE:
                    p1, p2 = a.vertices
                    info, colors, border, ends = a.info, a.colors, a.border, a.line_ends
                    page.delete_annot(a)
                    na = page.add_line_annot((p1[0] + dx, p1[1] + dy), (p2[0] + dx, p2[1] + dy))
                    na.set_line_ends(*ends)
                    na.set_info(info)
                    na.set_colors(stroke=colors.get("stroke"))
                    na.set_border(width=border.get("width", 1))
                    na.update()
                elif a.type[0] in (pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_UNDERLINE,
                                   pymupdf.PDF_ANNOT_STRIKE_OUT, pymupdf.PDF_ANNOT_SQUIGGLY):
                    return False  # tied to text; moving makes no sense
                else:
                    a.set_rect(r + (dx, dy, dx, dy))
                    a.update()
                return True
            except Exception:
                return False
    return False


def add_image(page, rect, image_path=None, image_bytes=None, keep_aspect=True, overlay=True):
    rect = pymupdf.Rect(rect)
    if image_bytes is None:
        with open(image_path, "rb") as fh:
            image_bytes = fh.read()
    page.insert_image(rect, stream=image_bytes, keep_proportion=keep_aspect, overlay=overlay)


def add_signature(page, rect, png_bytes):
    """Signatures are placed as an image inside a stamp-free, permanent overlay."""
    add_image(page, rect, image_bytes=png_bytes, keep_aspect=True)


def redact(page, rect, fill="#000000", text=""):
    page.add_redact_annot(rect, text=text or None, fill=hex_to_rgb(fill))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)


# ---------------------------------------------------------------- text editing

FONTS = {
    "Helvetica": "helv", "Helvetica Bold": "hebo", "Times": "tiro",
    "Times Bold": "tibo", "Courier": "cour", "Courier Bold": "cobo",
}


def add_text(page, point, text, fontsize=12, color="#000000", font="Helvetica"):
    page.insert_text(pymupdf.Point(point), text, fontsize=fontsize,
                     fontname=FONTS.get(font, "helv"), color=hex_to_rgb(color),
                     rotate=page.rotation)


def text_in_rect(page, rect):
    return page.get_text("text", clip=pymupdf.Rect(rect)).strip()


def text_style_in_rect(page, rect):
    """Return (fontsize, color_hex, is_bold, serif) of the first span in rect."""
    rect = pymupdf.Rect(rect)
    for b in page.get_text("dict", clip=rect)["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if s["text"].strip():
                    c = s["color"]
                    col = "#%02x%02x%02x" % ((c >> 16) & 255, (c >> 8) & 255, c & 255)
                    name = s["font"].lower()
                    bold = "bold" in name or bool(s["flags"] & 16)
                    serif = "times" in name or "serif" in name and "sans" not in name or bool(s["flags"] & 4)
                    mono = "cour" in name or "mono" in name or bool(s["flags"] & 8)
                    return s["size"], col, bold, serif, mono
    return 11, "#000000", False, False, False


def baseline_in_rect(page, rect):
    """y of the text baseline of the first span intersecting rect (or None)."""
    rect = pymupdf.Rect(rect)
    for b in page.get_text("dict", clip=rect + (-1, -1, 1, 1))["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if s["text"].strip():
                    return s["origin"][1]
    return None


def font_for(bold, serif, mono):
    if mono:
        return "Courier Bold" if bold else "Courier"
    if serif:
        return "Times Bold" if bold else "Times"
    return "Helvetica Bold" if bold else "Helvetica"


def replace_text_in_rect(page, rect, new_text, fontsize=None, color=None, font=None, bg="#ffffff"):
    """Erase the text inside rect and write new_text in its place."""
    rect = pymupdf.Rect(rect)
    size, col, bold, serif, mono = text_style_in_rect(page, rect)
    fontsize = fontsize or size
    color = color or col
    font = font or font_for(bold, serif, mono)
    # tight box around the existing words so we keep the original position
    words = [pymupdf.Rect(w[:4]) for w in page.get_text("words") if pymupdf.Rect(w[:4]).intersects(rect)]
    box = rect
    if words:
        box = words[0]
        for w in words[1:]:
            box |= w
    page.add_redact_annot(box, fill=hex_to_rgb(bg) if bg else None)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    if not new_text:
        return
    fname = FONTS.get(font, "helv")
    target = pymupdf.Rect(box.x0, box.y0 - 1, max(box.x1, rect.x1) + 2, max(box.y1, rect.y1) + fontsize)
    size = fontsize
    while size >= 4:
        rc = page.insert_textbox(target, new_text, fontsize=size, fontname=fname,
                                 color=hex_to_rgb(color))
        if rc >= 0:
            return
        size -= 0.5
    # last resort – write at baseline without wrapping
    page.insert_text((box.x0, box.y1 - 2), new_text, fontsize=4, fontname=fname, color=hex_to_rgb(color))


def find_replace(doc, find, replace, match_case=False, pages=None):
    """Replace every occurrence of `find`. Returns number of replacements."""
    count = 0
    for pno in pages if pages is not None else range(doc.page_count):
        page = doc[pno]
        hits = page.search_for(find)
        if not match_case:
            pass  # search_for is already case-insensitive
        else:
            hits = [r for r in hits if find in page.get_text("text", clip=r + (-1, -1, 1, 1))]
        if not hits:
            continue
        styles = [text_style_in_rect(page, r) for r in hits]
        bases = [baseline_in_rect(page, r) for r in hits]
        for r in hits:
            page.add_redact_annot(r, fill=(1, 1, 1))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        for r, (size, col, bold, serif, mono), base in zip(hits, styles, bases):
            if replace:
                fname = FONTS[font_for(bold, serif, mono)]
                fs = size
                while fs > 4 and pymupdf.get_text_length(replace, fontname=fname, fontsize=fs) > max(r.width, 1) * 1.6:
                    fs -= 0.5
                y = base if base is not None else r.y1 - r.height * 0.22
                page.insert_text((r.x0, y), replace,
                                 fontsize=fs, fontname=fname, color=hex_to_rgb(col))
            count += 1
    return count


def add_watermark(doc, text, pages=None, fontsize=60, color="#9e9e9e", opacity=0.3, angle=45):
    rgb = hex_to_rgb(color)
    for pno in pages if pages is not None else range(doc.page_count):
        page = doc[pno]
        r = page.rect
        tl = pymupdf.get_text_length(text, fontname="hebo", fontsize=fontsize)
        center = pymupdf.Point(r.width / 2, r.height / 2)
        start = center + (-tl / 2, fontsize / 3)
        m = pymupdf.Matrix(angle)
        page.insert_text(start, text, fontsize=fontsize, fontname="hebo", color=rgb,
                         fill_opacity=opacity, stroke_opacity=opacity,
                         morph=(center, m), overlay=True)


def add_page_numbers(doc, fmt="Page {n} of {total}", position="bottom-center", fontsize=10,
                     start=1, pages=None, color="#000000", margin=24):
    total = doc.page_count
    for i, pno in enumerate(pages if pages is not None else range(doc.page_count)):
        page = doc[pno]
        r = page.rect
        label = fmt.format(n=start + i, total=total)
        tl = pymupdf.get_text_length(label, fontname="helv", fontsize=fontsize)
        vpos, hpos = position.split("-")
        y = margin if vpos == "top" else r.height - margin + fontsize / 2
        x = {"left": margin, "center": (r.width - tl) / 2, "right": r.width - margin - tl}[hpos]
        # account for rotated pages: insert in unrotated coordinates
        pt = pymupdf.Point(x, y) * page.derotation_matrix
        page.insert_text(pt, label, fontsize=fontsize, fontname="helv",
                         color=hex_to_rgb(color), rotate=page.rotation)


# ---------------------------------------------------------------- forms

WIDGET_TYPES = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: "Text",
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "Checkbox",
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "Radio",
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "Dropdown",
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: "List",
    pymupdf.PDF_WIDGET_TYPE_BUTTON: "Button",
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "Signature",
}


def list_fields(doc):
    out = []
    for pno in range(doc.page_count):
        for w in doc[pno].widgets() or []:
            out.append({
                "page": pno, "xref": w.xref, "name": w.field_name or "",
                "type": WIDGET_TYPES.get(w.field_type, str(w.field_type)),
                "value": w.field_value, "choices": list(w.choice_values or []),
                "on": w.on_state() if w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
                                                        pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON) else None,
                "rect": tuple(w.rect),
            })
    return out


def set_field(doc, pno, xref, value):
    page = doc[pno]
    for w in page.widgets():
        if w.xref == xref:
            if w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
                w.field_value = w.on_state() if value in (True, "On", "Yes", 1, "1", "true", w.on_state()) else "Off"
            else:
                w.field_value = value
            w.update()
            return True
    return False


def widget_at(page, point):
    pt = pymupdf.Point(point)
    for w in page.widgets() or []:
        if w.rect.contains(pt):
            return w
    return None


def add_field(page, rect, kind="text", name=None, choices=None):
    w = pymupdf.Widget()
    w.rect = pymupdf.Rect(rect)
    w.field_name = name or f"{kind}_{int(datetime.now().timestamp()*1000) % 10**8}"
    if kind == "text":
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        w.text_fontsize = 0  # auto
        w.field_value = ""
    elif kind == "checkbox":
        w.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
        w.field_value = False
    elif kind == "dropdown":
        w.field_type = pymupdf.PDF_WIDGET_TYPE_COMBOBOX
        w.choice_values = choices or ["Option 1", "Option 2"]
        w.field_value = w.choice_values[0]
    w.border_color = (0.4, 0.4, 0.6)
    w.border_width = 1
    w.fill_color = (0.93, 0.95, 1.0)
    page.add_widget(w)
    return w


def flatten_forms(doc):
    """Burn form fields & annotations into the page content (no longer editable)."""
    doc.bake(annots=True, widgets=True)


# ---------------------------------------------------------------- security

def encrypt_bytes(doc, user_pw, owner_pw=None, allow_print=True, allow_copy=True, allow_edit=False):
    perm = pymupdf.PDF_PERM_ACCESSIBILITY
    if allow_print:
        perm |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
    if allow_copy:
        perm |= pymupdf.PDF_PERM_COPY
    if allow_edit:
        perm |= pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ASSEMBLE
    return doc.tobytes(garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                       user_pw=user_pw or "", owner_pw=owner_pw or user_pw or "x", permissions=perm)


def set_metadata(doc, **meta):
    m = doc.metadata or {}
    m.update({k: v for k, v in meta.items() if v is not None})
    doc.set_metadata(m)


# ---------------------------------------------------------------- compress

COMPRESS_LEVELS = {
    "Low (best quality)": dict(dpi_threshold=300, dpi_target=220, quality=85),
    "Medium (recommended)": dict(dpi_threshold=180, dpi_target=150, quality=70),
    "High (smallest file)": dict(dpi_threshold=120, dpi_target=96, quality=50),
}


def compress_to_bytes(doc, level="Medium (recommended)", grayscale=False):
    tmp = pymupdf.open("pdf", doc.tobytes())
    cfg = COMPRESS_LEVELS.get(level, COMPRESS_LEVELS["Medium (recommended)"])
    try:
        tmp.rewrite_images(set_to_gray=grayscale, **cfg)
    except Exception:
        pass
    try:
        tmp.subset_fonts()
    except Exception:
        pass
    data = tmp.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True,
                       clean=True, use_objstms=1)
    tmp.close()
    return data


# ---------------------------------------------------------------- convert

def export_images(doc, out_dir, base, fmt="png", dpi=150, pages=None):
    os.makedirs(out_dir, exist_ok=True)
    files = []
    pages = list(pages) if pages is not None else list(range(doc.page_count))
    width = len(str(doc.page_count))
    for p in pages:
        pix = doc[p].get_pixmap(dpi=dpi, alpha=False)
        path = os.path.join(out_dir, f"{base}_p{str(p + 1).zfill(width)}.{fmt}")
        if fmt in ("jpg", "jpeg"):
            pix.save(path, jpg_quality=90)
        else:
            pix.save(path)
        files.append(path)
    return files


def images_to_pdf(paths, out_path, page_size="fit", margin=0):
    out = pymupdf.open()
    for p in paths:
        img = pymupdf.open(p)
        rect = img[0].rect
        pdfbytes = img.convert_to_pdf()
        img.close()
        imgpdf = pymupdf.open("pdf", pdfbytes)
        if page_size == "fit":
            page = out.new_page(width=rect.width + 2 * margin, height=rect.height + 2 * margin)
        else:
            pr = pymupdf.paper_rect(page_size)
            if rect.width > rect.height:
                pr = pymupdf.Rect(0, 0, pr.height, pr.width)
            page = out.new_page(width=pr.width, height=pr.height)
        target = page.rect + (margin, margin, -margin, -margin)
        page.show_pdf_page(target, imgpdf, 0, keep_proportion=True)
        imgpdf.close()
    out.save(out_path, garbage=3, deflate=True)
    out.close()


def to_text(doc, out_path):
    with open(out_path, "w", encoding="utf-8") as fh:
        for i, page in enumerate(doc):
            fh.write(f"===== Page {i + 1} =====\n")
            fh.write(page.get_text("text", sort=True))
            fh.write("\n")


def to_word(pdf_path, out_path, pages=None, progress=None):
    from pdf2docx import Converter
    cv = Converter(pdf_path)
    try:
        if pages:
            cv.convert(out_path, pages=pages)
        else:
            cv.convert(out_path)
    finally:
        cv.close()


def to_excel(doc, out_path, pages=None):
    """Each detected table becomes a sheet. Pages without tables get their text
    lines written one per row on a 'Text' sheet."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin", color="BBBBBB")
    text_rows = []
    tcount = 0
    for pno in pages if pages is not None else range(doc.page_count):
        page = doc[pno]
        try:
            tabs = page.find_tables()
            tables = list(tabs.tables)
        except Exception:
            tables = []
        if not tables:
            for line in page.get_text("text", sort=True).splitlines():
                if line.strip():
                    text_rows.append((pno + 1, line))
            continue
        for t in tables:
            tcount += 1
            ws = wb.create_sheet(f"P{pno + 1}_Table{tcount}"[:31])
            rows = t.extract()
            for r_i, row in enumerate(rows, 1):
                for c_i, val in enumerate(row, 1):
                    v = (val or "").replace("\n", " ").strip()
                    num = _to_number(v)
                    cell = ws.cell(row=r_i, column=c_i, value=num if num is not None else v)
                    cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                    if r_i == 1:
                        cell.font = Font(bold=True)
                        cell.fill = PatternFill("solid", fgColor="E8EEF8")
            for c_i in range(1, (max((len(r) for r in rows), default=0)) + 1):
                width = max((len(str((r[c_i - 1] if c_i - 1 < len(r) else "") or "")) for r in rows), default=8)
                ws.column_dimensions[get_column_letter(c_i)].width = min(max(8, width + 2), 60)
            ws.freeze_panes = "A2"
    if text_rows:
        ws = wb.create_sheet("Text")
        ws.append(["Page", "Text"])
        ws["A1"].font = ws["B1"].font = Font(bold=True)
        for r in text_rows:
            ws.append(list(r))
        ws.column_dimensions["B"].width = 100
    if not wb.sheetnames:
        wb.create_sheet("Empty")
    wb.save(out_path)
    return tcount


def _to_number(s):
    t = s.replace(",", "").replace(" ", "")
    if not t or len(t) > 18:
        return None
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    try:
        v = float(t)
    except ValueError:
        return None
    if neg:
        v = -v
    return int(v) if v.is_integer() and "." not in t else v


def office_to_pdf(path, out_path):
    """Word / Excel / PowerPoint -> PDF. Uses Microsoft Office on Windows if
    available, otherwise LibreOffice (soffice) if installed."""
    ext = os.path.splitext(path)[1].lower()
    path = os.path.abspath(path)
    out_path = os.path.abspath(out_path)
    if sys.platform == "win32":
        try:
            import win32com.client  # type: ignore
            if ext in (".doc", ".docx", ".rtf", ".odt", ".txt"):
                app = win32com.client.DispatchEx("Word.Application")
                app.Visible = False
                try:
                    d = app.Documents.Open(path, ReadOnly=True)
                    d.SaveAs(out_path, FileFormat=17)
                    d.Close(False)
                finally:
                    app.Quit()
                return
            if ext in (".xls", ".xlsx", ".xlsm", ".csv", ".ods"):
                app = win32com.client.DispatchEx("Excel.Application")
                app.Visible = False
                app.DisplayAlerts = False
                try:
                    wb = app.Workbooks.Open(path, ReadOnly=True)
                    wb.ExportAsFixedFormat(0, out_path)
                    wb.Close(False)
                finally:
                    app.Quit()
                return
            if ext in (".ppt", ".pptx", ".odp"):
                app = win32com.client.DispatchEx("PowerPoint.Application")
                try:
                    pr = app.Presentations.Open(path, ReadOnly=True, WithWindow=False)
                    pr.SaveAs(out_path, 32)
                    pr.Close()
                finally:
                    app.Quit()
                return
        except Exception:
            pass  # Office missing or failed -> try LibreOffice below
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("Microsoft Office or LibreOffice is needed to convert this file.")
    out_dir = os.path.dirname(out_path)
    subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, path],
                   check=True, capture_output=True, timeout=300,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    produced = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".pdf")
    if os.path.abspath(produced) != out_path:
        shutil.move(produced, out_path)


def find_soffice():
    for c in ("soffice", "libreoffice"):
        p = shutil.which(c)
        if p:
            return p
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if os.path.exists(p):
            return p
    return None


# ---------------------------------------------------------------- OCR

def find_tessdata():
    env = os.environ.get("TESSDATA_PREFIX")
    if env and os.path.isdir(env):
        return env
    for p in (r"C:\Program Files\Tesseract-OCR\tessdata",
              r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tessdata"),
              "/usr/share/tesseract-ocr/5/tessdata", "/usr/share/tesseract-ocr/4.00/tessdata",
              "/usr/share/tessdata", "/opt/homebrew/share/tessdata"):
        if p and os.path.isdir(p):
            return p
    return None


def ocr_document(doc, language="eng", dpi=300, pages=None, progress=None):
    """Return a new searchable PDF (bytes): page images with an invisible text layer.
    Pages that already contain text are copied unchanged."""
    tess = find_tessdata()
    if not tess:
        raise RuntimeError(
            "OCR needs the free Tesseract OCR engine.\n\n"
            "Install it from https://github.com/UB-Mannheim/tesseract/wiki "
            "(default folder), then try again.")
    out = pymupdf.open()
    pages = list(pages) if pages is not None else list(range(doc.page_count))
    for i in range(doc.page_count):
        page = doc[i]
        if i in pages and not page.get_text("text").strip():
            pix = page.get_pixmap(dpi=dpi)
            data = pix.pdfocr_tobytes(language=language, tessdata=tess)
            ocr = pymupdf.open("pdf", data)
            newp = out.new_page(width=page.rect.width, height=page.rect.height)
            newp.show_pdf_page(newp.rect, ocr, 0)
            ocr.close()
        else:
            out.insert_pdf(doc, from_page=i, to_page=i)
        if progress:
            progress(i + 1, doc.page_count)
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data
