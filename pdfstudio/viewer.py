"""Continuous-scroll PDF page view with interactive tools."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QTimer
from PySide6.QtGui import (QBrush, QColor, QImage, QPainterPath, QPen, QPixmap,
                           QCursor, QPainter)
from PySide6.QtWidgets import (QGraphicsDropShadowEffect, QGraphicsPathItem,
                               QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView, QGraphicsEllipseItem,
                               QGraphicsLineItem)

GAP = 14
MARGIN = 20

# tools that need a rectangle drag
RECT_TOOLS = {"select_text", "highlight", "underline", "strikeout", "rect", "ellipse",
              "textbox", "image", "signature", "stamp", "custom_stamp", "redact",
              "edit_text", "field_text", "field_checkbox", "field_dropdown"}
CLICK_TOOLS = {"note", "add_text", "delete_annot"}


class PageView(QGraphicsView):
    toolFinished = Signal(str, int, object)       # tool, page index, payload (PDF coords)
    widgetClicked = Signal(int, int)              # page, widget xref
    annotMoved = Signal(int, int, float, float)   # page, annot xref, dx, dy
    currentPageChanged = Signal(int)
    zoomChanged = Signal(float)
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#3b3f45"))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setAcceptDrops(True)
        self.doc: pymupdf.Document | None = None
        self.zoom = 1.0
        self.tool = "hand"
        self.color = "#e53935"
        self.width = 2.0
        self.pages: list[dict] = []
        self._drag = None
        self._temp = None
        self._ink_path = None
        self._ink_points = []
        self._search_items = []
        self._current = 0
        self._render_timer = QTimer(self, singleShot=True, interval=30)
        self._render_timer.timeout.connect(self._render_visible)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.horizontalScrollBar().valueChanged.connect(lambda _: self._render_timer.start())
        self.set_tool("hand")

    # ------------------------------------------------------------ document
    def set_document(self, doc, keep_position=False):
        frac = 0.0
        if keep_position and self.verticalScrollBar().maximum() > 0:
            frac = self.verticalScrollBar().value() / max(1, self.verticalScrollBar().maximum())
        self.doc = doc
        self._layout()
        if keep_position:
            self.verticalScrollBar().setValue(int(frac * self.verticalScrollBar().maximum()))
        else:
            self.verticalScrollBar().setValue(0)
        self._render_visible()

    def clear(self):
        self.doc = None
        self.scene().clear()
        self.pages = []

    def _layout(self):
        sc = self.scene()
        sc.clear()
        self._temp = None
        self._search_items = []
        self.pages = []
        if not self.doc:
            return
        rects = [self.doc[i].rect for i in range(self.doc.page_count)]
        maxw = max(r.width for r in rects) * self.zoom
        y = MARGIN
        for i, r in enumerate(rects):
            w, h = r.width * self.zoom, r.height * self.zoom
            x = MARGIN + (maxw - w) / 2
            bg = QGraphicsRectItem(0, 0, w, h)
            bg.setPos(x, y)
            bg.setBrush(QBrush(Qt.white))
            bg.setPen(QPen(Qt.NoPen))
            eff = QGraphicsDropShadowEffect()
            eff.setBlurRadius(12)
            eff.setOffset(0, 2)
            eff.setColor(QColor(0, 0, 0, 110))
            bg.setGraphicsEffect(eff)
            sc.addItem(bg)
            pix = QGraphicsPixmapItem(bg)
            pix.setTransformationMode(Qt.SmoothTransformation)
            self.pages.append({"bg": bg, "pix": pix, "x": x, "y": y, "w": w, "h": h,
                               "zoom": None})
            y += h + GAP
        sc.setSceneRect(0, 0, maxw + 2 * MARGIN, y + MARGIN - GAP)

    def refresh(self, pages=None):
        """Re-render after an edit. If page sizes changed, relayout."""
        if not self.doc:
            return
        if len(self.pages) != self.doc.page_count or any(
                abs(self.doc[i].rect.width * self.zoom - p["w"]) > 0.5 or
                abs(self.doc[i].rect.height * self.zoom - p["h"]) > 0.5
                for i, p in enumerate(self.pages)):
            self.set_document(self.doc, keep_position=True)
            return
        for i, p in enumerate(self.pages):
            if pages is None or i in pages:
                p["zoom"] = None
        self._render_visible()

    def _visible_pages(self):
        vr = self.mapToScene(self.viewport().rect()).boundingRect()
        out = []
        for i, p in enumerate(self.pages):
            if p["y"] + p["h"] >= vr.top() - 200 and p["y"] <= vr.bottom() + 200:
                out.append(i)
        return out

    def _render_visible(self):
        if not self.doc:
            return
        dpr = self.devicePixelRatioF()
        vis = set(self._visible_pages())
        for i, p in enumerate(self.pages):
            if i in vis:
                if p["zoom"] != self.zoom:
                    m = pymupdf.Matrix(self.zoom * dpr, self.zoom * dpr)
                    pix = self.doc[i].get_pixmap(matrix=m, alpha=False, annots=True)
                    img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                                 QImage.Format_RGB888).copy()
                    qp = QPixmap.fromImage(img)
                    qp.setDevicePixelRatio(dpr)
                    p["pix"].setPixmap(qp)
                    p["zoom"] = self.zoom
            elif p["zoom"] is not None and abs(i - self._current) > 6:
                p["pix"].setPixmap(QPixmap())  # free memory far away
                p["zoom"] = None

    # ------------------------------------------------------------ navigation
    def _on_scroll(self, _):
        self._render_timer.start()
        if not self.pages:
            return
        vr = self.mapToScene(self.viewport().rect()).boundingRect()
        mid = vr.top() + vr.height() * 0.35
        cur = 0
        for i, p in enumerate(self.pages):
            if p["y"] <= mid:
                cur = i
        if cur != self._current:
            self._current = cur
            self.currentPageChanged.emit(cur)

    def current_page(self):
        return self._current

    def go_to(self, pno, y_pdf=None):
        if not self.pages:
            return
        pno = max(0, min(pno, len(self.pages) - 1))
        p = self.pages[pno]
        y = p["y"] - 8 if y_pdf is None else p["y"] + y_pdf * self.zoom - 60
        self.verticalScrollBar().setValue(int(y))
        self._current = pno
        self.currentPageChanged.emit(pno)
        self._render_visible()

    def set_zoom(self, z, anchor=None):
        z = max(0.1, min(z, 8.0))
        if not self.doc or abs(z - self.zoom) < 1e-4:
            self.zoom = z
            return
        # keep the same page position in view
        cur = self._current
        p = self.pages[cur] if self.pages else None
        offset = 0.0
        if p:
            vr = self.mapToScene(self.viewport().rect()).boundingRect()
            offset = (vr.top() - p["y"]) / self.zoom
        self.zoom = z
        self._layout()
        if p:
            np_ = self.pages[cur]
            self.verticalScrollBar().setValue(int(np_["y"] + offset * z))
        self._render_visible()
        self.zoomChanged.emit(z)

    def fit_width(self):
        if not self.doc:
            return
        w = max(self.doc[i].rect.width for i in range(self.doc.page_count))
        avail = self.viewport().width() - 2 * MARGIN - 4
        self.set_zoom(avail / w)

    def fit_page(self):
        if not self.doc:
            return
        r = self.doc[self._current].rect
        z = min((self.viewport().width() - 2 * MARGIN) / r.width,
                (self.viewport().height() - 2 * GAP) / r.height)
        self.set_zoom(z)
        self.go_to(self._current)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render_timer.start()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.set_zoom(self.zoom * f)
            e.accept()
            return
        super().wheelEvent(e)

    # ------------------------------------------------------------ coordinates
    def page_at(self, scene_pt: QPointF):
        for i, p in enumerate(self.pages):
            if p["x"] <= scene_pt.x() <= p["x"] + p["w"] and p["y"] <= scene_pt.y() <= p["y"] + p["h"]:
                return i
        return None

    def nearest_page(self, scene_pt):
        best, bd = 0, 1e18
        for i, p in enumerate(self.pages):
            cy = min(max(scene_pt.y(), p["y"]), p["y"] + p["h"])
            d = abs(cy - scene_pt.y())
            if d < bd:
                best, bd = i, d
        return best

    def to_pdf(self, pno, scene_pt: QPointF) -> pymupdf.Point:
        """Scene point -> unrotated PDF coordinates of page pno."""
        p = self.pages[pno]
        x = (scene_pt.x() - p["x"]) / self.zoom
        y = (scene_pt.y() - p["y"]) / self.zoom
        x = min(max(x, 0), p["w"] / self.zoom)
        y = min(max(y, 0), p["h"] / self.zoom)
        return pymupdf.Point(x, y) * self.doc[pno].derotation_matrix

    def rect_to_pdf(self, pno, a: QPointF, b: QPointF) -> pymupdf.Rect:
        pa, pb = self.to_pdf(pno, a), self.to_pdf(pno, b)
        return pymupdf.Rect(min(pa.x, pb.x), min(pa.y, pb.y), max(pa.x, pb.x), max(pa.y, pb.y))

    def pdf_rect_to_scene(self, pno, rect) -> QRectF:
        page = self.doc[pno]
        r = pymupdf.Rect(rect) * page.rotation_matrix
        r.normalize()
        p = self.pages[pno]
        return QRectF(p["x"] + r.x0 * self.zoom, p["y"] + r.y0 * self.zoom,
                      r.width * self.zoom, r.height * self.zoom)

    # ------------------------------------------------------------ tools
    def set_tool(self, tool):
        self.tool = tool
        self._cancel_temp()
        if tool == "hand":
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.viewport().setCursor(Qt.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.NoDrag)
            cur = {"select_text": Qt.IBeamCursor, "delete_annot": Qt.PointingHandCursor,
                   "move_annot": Qt.SizeAllCursor, "add_text": Qt.IBeamCursor}.get(tool, Qt.CrossCursor)
            self.viewport().setCursor(cur)

    def _cancel_temp(self):
        if self._temp is not None and self._temp.scene():
            self.scene().removeItem(self._temp)
        self._temp = None
        self._drag = None

    def _pen(self):
        pen = QPen(QColor(self.color))
        pen.setWidthF(max(1.0, self.width * self.zoom))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def mousePressEvent(self, e):
        if not self.doc or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        sp = self.mapToScene(e.position().toPoint())
        pno = self.page_at(sp)
        if self.tool == "hand":
            if pno is not None:
                from . import ops
                page = self.doc[pno]  # keep page alive while using its widget
                w = ops.widget_at(page, self.to_pdf(pno, sp))
                if w is not None:
                    xref = w.xref
                    del w
                    self.widgetClicked.emit(pno, xref)
                    return
            return super().mousePressEvent(e)
        if pno is None:
            return
        if self.tool in CLICK_TOOLS:
            self.toolFinished.emit(self.tool, pno, self.to_pdf(pno, sp))
            return
        if self.tool == "move_annot":
            from . import ops
            page = self.doc[pno]  # keep page alive while using its annotation
            a = ops.annot_at(page, self.to_pdf(pno, sp))
            if a is None:
                return
            r = self.pdf_rect_to_scene(pno, a.rect)
            self._temp = QGraphicsRectItem(r)
            pen = QPen(QColor("#1e88e5"), 1.5, Qt.DashLine)
            self._temp.setPen(pen)
            self._temp.setBrush(QColor(30, 136, 229, 40))
            self.scene().addItem(self._temp)
            self._drag = {"pno": pno, "start": sp, "xref": a.xref}
            return
        self._drag = {"pno": pno, "start": sp}
        if self.tool in ("ink",):
            self._ink_points = [[sp]]
            path = QPainterPath(sp)
            self._temp = QGraphicsPathItem(path)
            self._temp.setPen(self._pen())
            self.scene().addItem(self._temp)
        elif self.tool in ("line", "arrow"):
            self._temp = QGraphicsLineItem(sp.x(), sp.y(), sp.x(), sp.y())
            self._temp.setPen(self._pen())
            self.scene().addItem(self._temp)
        else:
            if self.tool == "ellipse":
                self._temp = QGraphicsEllipseItem(QRectF(sp, sp))
            else:
                self._temp = QGraphicsRectItem(QRectF(sp, sp))
            if self.tool in ("highlight",):
                self._temp.setPen(QPen(Qt.NoPen))
                c = QColor(self.color)
                c.setAlpha(90)
                self._temp.setBrush(c)
            elif self.tool in ("rect", "ellipse"):
                self._temp.setPen(self._pen())
            elif self.tool == "redact":
                self._temp.setPen(QPen(Qt.red, 1))
                self._temp.setBrush(QColor(0, 0, 0, 120))
            else:
                self._temp.setPen(QPen(QColor("#1e88e5"), 1.2, Qt.DashLine))
                self._temp.setBrush(QColor(30, 136, 229, 30))
            self.scene().addItem(self._temp)

    def _clamp(self, pno, sp):
        p = self.pages[pno]
        return QPointF(min(max(sp.x(), p["x"]), p["x"] + p["w"]),
                       min(max(sp.y(), p["y"]), p["y"] + p["h"]))

    def mouseMoveEvent(self, e):
        if self._drag is None or self._temp is None:
            return super().mouseMoveEvent(e)
        pno = self._drag["pno"]
        sp = self._clamp(pno, self.mapToScene(e.position().toPoint()))
        st = self._drag["start"]
        if self.tool == "ink":
            self._ink_points[-1].append(sp)
            path = self._temp.path()
            path.lineTo(sp)
            self._temp.setPath(path)
        elif self.tool in ("line", "arrow"):
            self._temp.setLine(st.x(), st.y(), sp.x(), sp.y())
        elif self.tool == "move_annot":
            d = sp - st
            r = self._temp.rect()
            self._temp.setPos(d)
        else:
            self._temp.setRect(QRectF(st, sp).normalized())

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return super().mouseReleaseEvent(e)
        pno = self._drag["pno"]
        st = self._drag["start"]
        sp = self._clamp(pno, self.mapToScene(e.position().toPoint()))
        tool = self.tool
        payload = None
        if tool == "ink":
            pts = [[tuple(self.to_pdf(pno, q)) for q in stroke] for stroke in self._ink_points]
            if len(pts[0]) >= 2:
                payload = pts
        elif tool in ("line", "arrow"):
            if (sp - st).manhattanLength() > 3:
                payload = (self.to_pdf(pno, st), self.to_pdf(pno, sp))
        elif tool == "move_annot":
            a, b = self.to_pdf(pno, st), self.to_pdf(pno, sp)
            xref = self._drag["xref"]
            self._cancel_temp()
            if (sp - st).manhattanLength() > 2:
                self.annotMoved.emit(pno, xref, b.x - a.x, b.y - a.y)
            return
        else:
            small = abs(sp.x() - st.x()) < 4 and abs(sp.y() - st.y()) < 4
            if small and tool in ("image", "signature", "stamp", "custom_stamp", "textbox",
                                  "field_text", "field_checkbox", "field_dropdown"):
                # click without drag: default size box at the click point
                c = self.to_pdf(pno, st)
                w, h = {"stamp": (180, 55), "custom_stamp": (160, 55), "signature": (160, 55),
                        "image": (200, 150), "textbox": (180, 40), "field_text": (180, 20),
                        "field_checkbox": (14, 14), "field_dropdown": (140, 20)}[tool]
                payload = pymupdf.Rect(c.x, c.y, c.x + w, c.y + h)
            elif not small or tool in ("select_text", "highlight", "underline", "strikeout", "edit_text"):
                if small and tool in ("highlight", "underline", "strikeout", "select_text", "edit_text"):
                    # click on a word -> use that word / line
                    c = self.to_pdf(pno, st)
                    payload = pymupdf.Rect(c.x - 1, c.y - 1, c.x + 1, c.y + 1)
                else:
                    payload = self.rect_to_pdf(pno, st, sp)
        self._cancel_temp()
        if payload is not None:
            self.toolFinished.emit(tool, pno, payload)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._cancel_temp()
        elif e.key() in (Qt.Key_PageDown,) and not e.modifiers():
            self.go_to(self._current + 1)
            return
        elif e.key() in (Qt.Key_PageUp,) and not e.modifiers():
            self.go_to(self._current - 1)
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------ search marks
    def show_marks(self, pno, rects, current=None):
        self.clear_marks()
        for i, r in enumerate(rects):
            item = QGraphicsRectItem(self.pdf_rect_to_scene(pno, r))
            col = QColor(255, 140, 0, 130) if i == current else QColor(255, 235, 59, 110)
            item.setBrush(col)
            item.setPen(QPen(Qt.NoPen))
            item.setZValue(10)
            self.scene().addItem(item)
            self._search_items.append(item)

    def clear_marks(self):
        for it in self._search_items:
            if it.scene():
                self.scene().removeItem(it)
        self._search_items = []

    # ------------------------------------------------------------ drag & drop files
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            if files:
                self.filesDropped.emit(files)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)
