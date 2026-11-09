"""Dialogs used by the main window."""
from __future__ import annotations

import os
import glob
import time

from PySide6.QtCore import Qt, QPointF, QRectF, QStandardPaths, QSize, QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QImage, QFont, QPixmap, QIcon, QFontDatabase
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout, QFormLayout,
                               QLabel, QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox,
                               QPushButton, QColorDialog, QListWidget, QListWidgetItem, QFileDialog,
                               QTabWidget, QWidget, QCheckBox, QRadioButton, QButtonGroup,
                               QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
                               QAbstractItemView, QGridLayout)

from . import ops


def app_data_dir(*sub):
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser("~/.pdfstudio")
    path = os.path.join(base, *sub)
    os.makedirs(path, exist_ok=True)
    return path


class ColorButton(QPushButton):
    def __init__(self, color="#000000", parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 24)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, c):
        self.color = QColor(c).name()
        self.setStyleSheet(f"background:{self.color}; border:1px solid #888; border-radius:3px;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Choose colour")
        if c.isValid():
            self.set_color(c.name())


def _buttons(dlg, ok_text="OK"):
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText(ok_text)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


# ------------------------------------------------------------------ text
class TextDialog(QDialog):
    def __init__(self, parent=None, title="Add text", text="", size=12, color="#000000",
                 font="Helvetica", ok_text="Add"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 300)
        lay = QVBoxLayout(self)
        self.edit = QTextEdit()
        self.edit.setAcceptRichText(False)
        self.edit.setPlainText(text)
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        self.font = QComboBox()
        self.font.addItems(list(ops.FONTS))
        self.font.setCurrentText(font)
        self.size = QDoubleSpinBox()
        self.size.setRange(4, 200)
        self.size.setValue(round(size, 1))
        self.size.setSuffix(" pt")
        self.colorbtn = ColorButton(color)
        row.addWidget(QLabel("Font"))
        row.addWidget(self.font, 1)
        row.addWidget(QLabel("Size"))
        row.addWidget(self.size)
        row.addWidget(QLabel("Colour"))
        row.addWidget(self.colorbtn)
        lay.addLayout(row)
        lay.addWidget(_buttons(self, ok_text))
        self.edit.setFocus()

    def values(self):
        return (self.edit.toPlainText(), self.size.value(), self.colorbtn.color,
                self.font.currentText())


# ------------------------------------------------------------------ signature
class SignaturePad(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(520, 200)
        self.strokes: list[list[QPointF]] = []
        self.pen_color = QColor("#0d2a8a")
        self.pen_width = 2.6
        self.setCursor(Qt.CrossCursor)
        self.setStyleSheet("background:white;")
        self.setAttribute(Qt.WA_StyledBackground, True)

    def clear(self):
        self.strokes = []
        self.update()

    def mousePressEvent(self, e):
        self.strokes.append([e.position()])
        self.update()

    def mouseMoveEvent(self, e):
        if self.strokes:
            self.strokes[-1].append(e.position())
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), Qt.white)
        p.setPen(QPen(QColor("#c8c8c8"), 1, Qt.DashLine))
        y = self.height() * 0.75
        p.drawLine(20, int(y), self.width() - 20, int(y))
        self._draw(p, 1.0)

    def _draw(self, p, scale, offset=QPointF(0, 0)):
        pen = QPen(self.pen_color, self.pen_width * scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        for s in self.strokes:
            if len(s) == 1:
                p.drawPoint((s[0] - offset) * scale)
                continue
            path = QPainterPath((s[0] - offset) * scale)
            for pt in s[1:]:
                path.lineTo((pt - offset) * scale)
            p.drawPath(path)

    def to_png(self):
        if not self.strokes:
            return None
        xs = [pt.x() for s in self.strokes for pt in s]
        ys = [pt.y() for s in self.strokes for pt in s]
        pad = 6
        bx, by = min(xs) - pad, min(ys) - pad
        bw, bh = max(xs) - bx + pad, max(ys) - by + pad
        scale = 4
        img = QImage(int(bw * scale), int(bh * scale), QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        self._draw(p, scale, QPointF(bx, by))
        p.end()
        return qimage_png(img)


def qimage_png(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def text_signature_png(text, family, color="#0d2a8a"):
    font = QFont(family, 72)
    from PySide6.QtGui import QFontMetrics
    fm = QFontMetrics(font)
    w = fm.horizontalAdvance(text) + 40
    h = fm.height() + 20
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, text)
    p.end()
    return qimage_png(img)


SCRIPT_FONTS = ["Segoe Script", "Lucida Handwriting", "Brush Script MT", "Ink Free",
                "Freestyle Script", "Mistral", "Bradley Hand ITC", "Comic Sans MS"]


class SignatureDialog(QDialog):
    """Pick a saved signature or create a new one (draw / type / image)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Signatures & initials")
        self.resize(640, 460)
        self.result_png: bytes | None = None
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        # saved
        saved = QWidget()
        sl = QVBoxLayout(saved)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(220, 80))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setSpacing(8)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        sl.addWidget(self.list)
        rm = QPushButton("Delete selected")
        rm.clicked.connect(self._delete)
        sl.addWidget(rm, 0, Qt.AlignRight)
        self.tabs.addTab(saved, "Saved")

        # draw
        draw = QWidget()
        dl = QVBoxLayout(draw)
        dl.addWidget(QLabel("Draw your signature with the mouse, pen or touch screen:"))
        self.pad = SignaturePad()
        dl.addWidget(self.pad, 1)
        row = QHBoxLayout()
        self.pad_color = ColorButton("#0d2a8a")
        self.pad_color.clicked.connect(lambda: (setattr(self.pad, "pen_color", QColor(self.pad_color.color)), self.pad.update()))
        clr = QPushButton("Clear")
        clr.clicked.connect(self.pad.clear)
        row.addWidget(QLabel("Ink"))
        row.addWidget(self.pad_color)
        row.addStretch()
        row.addWidget(clr)
        dl.addLayout(row)
        self.tabs.addTab(draw, "Draw")

        # type
        typ = QWidget()
        tl = QFormLayout(typ)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Your name")
        self.font_box = QComboBox()
        fams = set(QFontDatabase.families())
        avail = [f for f in SCRIPT_FONTS if f in fams] or ["Serif"]
        self.font_box.addItems(avail)
        self.preview = QLabel()
        self.preview.setMinimumHeight(110)
        self.preview.setStyleSheet("background:white; border:1px solid #ccc;")
        self.preview.setAlignment(Qt.AlignCenter)
        self.type_color = ColorButton("#0d2a8a")
        self.name.textChanged.connect(self._update_preview)
        self.font_box.currentTextChanged.connect(self._update_preview)
        self.type_color.clicked.connect(self._update_preview)
        tl.addRow("Name", self.name)
        tl.addRow("Style", self.font_box)
        tl.addRow("Colour", self.type_color)
        tl.addRow(self.preview)
        self.tabs.addTab(typ, "Type")

        # image
        im = QWidget()
        il = QVBoxLayout(im)
        self.img_label = QLabel("Choose a scanned signature image (PNG with transparent background works best).")
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setMinimumHeight(150)
        self.img_label.setStyleSheet("background:white; border:1px solid #ccc;")
        self.img_bytes = None
        b = QPushButton("Browse image…")
        b.clicked.connect(self._browse)
        self.whiten = QCheckBox("Make white background transparent")
        self.whiten.setChecked(True)
        il.addWidget(self.img_label, 1)
        il.addWidget(self.whiten)
        il.addWidget(b, 0, Qt.AlignLeft)
        self.tabs.addTab(im, "Image")

        self.save_box = QCheckBox("Save this signature for next time")
        self.save_box.setChecked(True)
        lay.addWidget(self.save_box)
        lay.addWidget(_buttons(self, "Use signature"))
        self._load_saved()
        if self.list.count() == 0:
            self.tabs.setCurrentIndex(1)

    def _sig_dir(self):
        return app_data_dir("signatures")

    def _load_saved(self):
        self.list.clear()
        for f in sorted(glob.glob(os.path.join(self._sig_dir(), "*.png")), reverse=True):
            it = QListWidgetItem(QIcon(QPixmap(f)), "")
            it.setData(Qt.UserRole, f)
            it.setSizeHint(QSize(240, 100))
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _delete(self):
        it = self.list.currentItem()
        if it:
            os.remove(it.data(Qt.UserRole))
            self._load_saved()

    def _update_preview(self):
        t = self.name.text().strip()
        if not t:
            self.preview.clear()
            return
        png = text_signature_png(t, self.font_box.currentText(), self.type_color.color)
        pm = QPixmap()
        pm.loadFromData(png)
        self.preview.setPixmap(pm.scaled(self.preview.width() - 10, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _browse(self):
        f, _ = QFileDialog.getOpenFileName(self, "Signature image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not f:
            return
        img = QImage(f)
        if img.isNull():
            return
        if self.whiten.isChecked():
            img = img.convertToFormat(QImage.Format_ARGB32)
            for y in range(img.height()):
                for x in range(img.width()):
                    c = img.pixelColor(x, y)
                    if c.red() > 225 and c.green() > 225 and c.blue() > 225:
                        img.setPixelColor(x, y, QColor(0, 0, 0, 0))
        self.img_bytes = qimage_png(img)
        self.img_label.setPixmap(QPixmap.fromImage(img).scaled(400, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def accept(self):
        tab = self.tabs.currentIndex()
        png = None
        new = False
        if tab == 0:
            it = self.list.currentItem()
            if it:
                with open(it.data(Qt.UserRole), "rb") as fh:
                    png = fh.read()
        elif tab == 1:
            png, new = self.pad.to_png(), True
        elif tab == 2:
            if self.name.text().strip():
                png, new = text_signature_png(self.name.text().strip(), self.font_box.currentText(), self.type_color.color), True
        else:
            png, new = self.img_bytes, True
        if not png:
            QMessageBox.information(self, "Signature", "Please create or choose a signature first.")
            return
        if new and self.save_box.isChecked():
            with open(os.path.join(self._sig_dir(), f"sig_{int(time.time())}.png"), "wb") as fh:
                fh.write(png)
        self.result_png = png
        super().accept()


# ------------------------------------------------------------------ merge
class MergeDialog(QDialog):
    def __init__(self, parent=None, files=None):
        super().__init__(parent)
        self.setWindowTitle("Combine files")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Add PDFs or images, then drag to set the order:"))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn in (("Add files…", self._add), ("Remove", self._remove),
                         ("Move up", lambda: self._move(-1)), ("Move down", lambda: self._move(1)),
                         ("Sort A–Z", self.list.sortItems)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(_buttons(self, "Combine"))
        for f in files or []:
            self._add_path(f)

    def _add_path(self, f):
        it = QListWidgetItem(os.path.basename(f))
        it.setToolTip(f)
        it.setData(Qt.UserRole, f)
        self.list.addItem(it)

    def _add(self):
        fs, _ = QFileDialog.getOpenFileNames(self, "Add files", "", ALL_OPEN_FILTER)
        for f in fs:
            self._add_path(f)

    def _remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def _move(self, d):
        r = self.list.currentRow()
        if r < 0 or not (0 <= r + d < self.list.count()):
            return
        it = self.list.takeItem(r)
        self.list.insertItem(r + d, it)
        self.list.setCurrentRow(r + d)

    def files(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]


ALL_OPEN_FILTER = ("PDF and images (*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.xps *.epub);;"
                   "PDF files (*.pdf);;All files (*)")


# ------------------------------------------------------------------ split
class SplitDialog(QDialog):
    def __init__(self, parent, page_count):
        super().__init__(parent)
        self.setWindowTitle("Split document")
        lay = QVBoxLayout(self)
        self.grp = QButtonGroup(self)
        g = QGridLayout()
        self.r_every = QRadioButton("Every")
        self.every = QSpinBox()
        self.every.setRange(1, max(1, page_count))
        g.addWidget(self.r_every, 0, 0)
        g.addWidget(self.every, 0, 1)
        g.addWidget(QLabel("page(s)"), 0, 2)
        self.r_ranges = QRadioButton("Page ranges")
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText("e.g. 1-3; 4-8; 9-")
        g.addWidget(self.r_ranges, 1, 0)
        g.addWidget(self.ranges, 1, 1, 1, 2)
        self.r_size = QRadioButton("Max file size")
        self.size = QDoubleSpinBox()
        self.size.setRange(0.1, 2000)
        self.size.setValue(5)
        self.size.setSuffix(" MB")
        g.addWidget(self.r_size, 2, 0)
        g.addWidget(self.size, 2, 1)
        for i, r in enumerate((self.r_every, self.r_ranges, self.r_size)):
            self.grp.addButton(r, i)
        self.r_every.setChecked(True)
        lay.addLayout(g)
        lay.addWidget(QLabel(f"The document has {page_count} pages. Files are saved to a folder you choose."))
        lay.addWidget(_buttons(self, "Split"))

    def values(self):
        i = self.grp.checkedId()
        if i == 0:
            return "every", str(self.every.value())
        if i == 1:
            return "ranges", self.ranges.text()
        return "size", str(self.size.value())


# ------------------------------------------------------------------ forms
class FormsDialog(QDialog):
    def __init__(self, parent, doc, focus_xref=None):
        super().__init__(parent)
        self.setWindowTitle("Fill form fields")
        self.resize(640, 460)
        self.doc = doc
        self.fields = ops.list_fields(doc)
        lay = QVBoxLayout(self)
        if not self.fields:
            lay.addWidget(QLabel("This document has no fillable form fields.\n"
                                 "Use Forms ▸ Add text field / checkbox to create some."))
        self.table = QTableWidget(len(self.fields), 4)
        self.table.setHorizontalHeaderLabels(["Page", "Field", "Type", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.editors = []
        focus_row = 0
        for r, f in enumerate(self.fields):
            for c, v in enumerate((str(f["page"] + 1), f["name"], f["type"])):
                it = QTableWidgetItem(v)
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, it)
            if f["type"] in ("Checkbox", "Radio"):
                ed = QCheckBox()
                ed.setChecked(f["value"] not in (None, False, "Off", ""))
            elif f["type"] in ("Dropdown", "List"):
                ed = QComboBox()
                ed.setEditable(f["type"] == "Dropdown")
                ed.addItems([c if isinstance(c, str) else c[-1] for c in f["choices"]])
                ed.setCurrentText(str(f["value"] or ""))
            elif f["type"] in ("Button", "Signature"):
                ed = QLabel("(not editable)")
            else:
                ed = QLineEdit(str(f["value"] or ""))
            self.table.setCellWidget(r, 3, ed)
            self.editors.append(ed)
            if f["xref"] == focus_xref:
                focus_row = r
        lay.addWidget(self.table, 1)
        lay.addWidget(_buttons(self, "Apply"))
        if self.fields:
            self.table.setCurrentCell(focus_row, 3)
            w = self.editors[focus_row]
            w.setFocus()
            if isinstance(w, QLineEdit):
                w.selectAll()

    def apply(self):
        for f, ed in zip(self.fields, self.editors):
            if isinstance(ed, QCheckBox):
                val = ed.isChecked()
                cur = f["value"] not in (None, False, "Off", "")
                if val != cur:
                    ops.set_field(self.doc, f["page"], f["xref"], val)
            elif isinstance(ed, QComboBox):
                if ed.currentText() != str(f["value"] or ""):
                    ops.set_field(self.doc, f["page"], f["xref"], ed.currentText())
            elif isinstance(ed, QLineEdit):
                if ed.text() != str(f["value"] or ""):
                    ops.set_field(self.doc, f["page"], f["xref"], ed.text())


# ------------------------------------------------------------------ watermark / numbers
class WatermarkDialog(QDialog):
    def __init__(self, parent, page_count):
        super().__init__(parent)
        self.setWindowTitle("Add watermark")
        f = QFormLayout(self)
        self.text = QLineEdit("CONFIDENTIAL")
        self.size = QSpinBox()
        self.size.setRange(8, 300)
        self.size.setValue(64)
        self.color = ColorButton("#9e9e9e")
        self.opacity = QSpinBox()
        self.opacity.setRange(5, 100)
        self.opacity.setValue(30)
        self.opacity.setSuffix(" %")
        self.angle = QSpinBox()
        self.angle.setRange(-90, 90)
        self.angle.setValue(45)
        self.angle.setSuffix("°")
        self.pages = QLineEdit(f"1-{page_count}")
        f.addRow("Text", self.text)
        f.addRow("Font size", self.size)
        f.addRow("Colour", self.color)
        f.addRow("Opacity", self.opacity)
        f.addRow("Angle", self.angle)
        f.addRow("Pages", self.pages)
        f.addRow(_buttons(self, "Add"))


class PageNumbersDialog(QDialog):
    def __init__(self, parent, page_count):
        super().__init__(parent)
        self.setWindowTitle("Add page numbers")
        f = QFormLayout(self)
        self.fmt = QComboBox()
        self.fmt.setEditable(True)
        self.fmt.addItems(["Page {n} of {total}", "{n}", "Page {n}", "{n} / {total}", "- {n} -"])
        self.pos = QComboBox()
        self.pos.addItems(["bottom-center", "bottom-right", "bottom-left", "top-center", "top-right", "top-left"])
        self.size = QSpinBox()
        self.size.setRange(5, 48)
        self.size.setValue(10)
        self.start = QSpinBox()
        self.start.setRange(0, 100000)
        self.start.setValue(1)
        self.pages = QLineEdit(f"1-{page_count}")
        self.color = ColorButton("#000000")
        f.addRow("Format", self.fmt)
        f.addRow("Position", self.pos)
        f.addRow("Font size", self.size)
        f.addRow("Start at", self.start)
        f.addRow("Colour", self.color)
        f.addRow("Pages", self.pages)
        f.addRow(QLabel("{n} = page number, {total} = page count"))
        f.addRow(_buttons(self, "Add"))


class CropDialog(QDialog):
    def __init__(self, parent, page_count, current):
        super().__init__(parent)
        self.setWindowTitle("Crop pages")
        f = QFormLayout(self)
        self.m = []
        for name in ("Left", "Top", "Right", "Bottom"):
            s = QDoubleSpinBox()
            s.setRange(0, 2000)
            s.setSuffix(" mm")
            s.setValue(10)
            self.m.append(s)
            f.addRow(name, s)
        self.pages = QLineEdit(str(current + 1))
        f.addRow("Pages", self.pages)
        f.addRow(QLabel(f"Use e.g. 1-{page_count} for all pages."))
        f.addRow(_buttons(self, "Crop"))

    def margins_pt(self):
        return tuple(s.value() * 72 / 25.4 for s in self.m)


# ------------------------------------------------------------------ security
class PasswordDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Protect with password")
        f = QFormLayout(self)
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.Password)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        self.owner = QLineEdit()
        self.owner.setEchoMode(QLineEdit.Password)
        self.owner.setPlaceholderText("optional – needed to change permissions")
        self.p_print = QCheckBox("Allow printing")
        self.p_print.setChecked(True)
        self.p_copy = QCheckBox("Allow copying text")
        self.p_copy.setChecked(True)
        self.p_edit = QCheckBox("Allow editing / commenting")
        f.addRow("Open password", self.pw1)
        f.addRow("Confirm", self.pw2)
        f.addRow("Permissions password", self.owner)
        f.addRow(self.p_print)
        f.addRow(self.p_copy)
        f.addRow(self.p_edit)
        f.addRow(QLabel("Encryption: AES-256"))
        f.addRow(_buttons(self, "Save protected copy…"))

    def accept(self):
        if not self.pw1.text():
            QMessageBox.warning(self, "Password", "Enter a password.")
            return
        if self.pw1.text() != self.pw2.text():
            QMessageBox.warning(self, "Password", "The passwords do not match.")
            return
        super().accept()


class MetadataDialog(QDialog):
    KEYS = ["title", "author", "subject", "keywords", "creator", "producer"]

    def __init__(self, parent, doc, path):
        super().__init__(parent)
        self.setWindowTitle("Document properties")
        f = QFormLayout(self)
        meta = doc.metadata or {}
        self.edits = {}
        for k in self.KEYS:
            e = QLineEdit(meta.get(k, "") or "")
            self.edits[k] = e
            f.addRow(k.capitalize(), e)
        info = [("File", path or "(unsaved)"), ("Pages", str(doc.page_count)),
                ("Page size", "%.0f × %.0f mm" % (doc[0].rect.width / 72 * 25.4, doc[0].rect.height / 72 * 25.4)),
                ("PDF version", meta.get("format", "")), ("Created", meta.get("creationDate", "")),
                ("Modified", meta.get("modDate", ""))]
        if path and os.path.exists(path):
            info.insert(1, ("Size", f"{os.path.getsize(path) / 1024:.1f} KB"))
        for k, v in info:
            lbl = QLabel(v)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            f.addRow(k, lbl)
        f.addRow(_buttons(self, "Save"))

    def values(self):
        return {k: e.text() for k, e in self.edits.items()}


class ChoiceDialog(QDialog):
    """Small generic dialog: a form of (label, widget) pairs."""

    def __init__(self, parent, title, rows, ok_text="OK"):
        super().__init__(parent)
        self.setWindowTitle(title)
        f = QFormLayout(self)
        for label, w in rows:
            if label:
                f.addRow(label, w)
            else:
                f.addRow(w)
        f.addRow(_buttons(self, ok_text))
