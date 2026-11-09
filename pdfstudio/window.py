"""Main application window."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback

import pymupdf
from PySide6.QtCore import Qt, QSettings, QSize, QTimer, QUrl
from PySide6.QtGui import (QAction, QActionGroup, QColor, QDesktopServices, QIcon, QImage,
                           QKeySequence, QPainter, QPixmap, QGuiApplication)
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget, QFileDialog,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QProgressDialog, QPushButton,
                               QSpinBox, QStyle, QTabWidget, QToolBar, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget, QScrollArea, QAbstractItemView,
                               QDoubleSpinBox, QCheckBox)

from . import ops, __version__
from .dialogs import (ALL_OPEN_FILTER, ChoiceDialog, ColorButton, CropDialog, FormsDialog,
                      MergeDialog, MetadataDialog, PageNumbersDialog, PasswordDialog,
                      SignatureDialog, SplitDialog, TextDialog, WatermarkDialog)
from .viewer import PageView

APP_NAME = "PDF Studio"
WINDOWS: list["MainWindow"] = []
MAX_UNDO = 30

TOOL_HINTS = {
    "hand": "Hand: drag to scroll. Click a form field to fill it.",
    "select_text": "Select text: drag over text (or click a line) to copy it.",
    "highlight": "Highlight: drag over text.",
    "underline": "Underline: drag over text.",
    "strikeout": "Strikethrough: drag over text.",
    "note": "Sticky note: click where the note should go.",
    "textbox": "Text box comment: drag a box (or click).",
    "ink": "Draw: drag to draw freehand.",
    "arrow": "Arrow: drag from start to end.",
    "line": "Line: drag from start to end.",
    "rect": "Rectangle: drag a box.",
    "ellipse": "Oval: drag a box.",
    "stamp": "Stamp: drag a box (or click) to place the stamp.",
    "custom_stamp": "Stamp: drag a box (or click) to place the stamp.",
    "signature": "Signature: drag a box (or click) to place your signature.",
    "image": "Image: drag a box (or click) where the image should go.",
    "add_text": "Add text: click where the text should start.",
    "edit_text": "Edit text: drag over the text to change (or click a line).",
    "redact": "Redact: drag over content to remove it permanently (black box).",
    "move_annot": "Move: drag a comment, stamp, shape or note to a new place.",
    "delete_annot": "Delete: click a comment, shape, note, stamp or form field.",
    "field_text": "Add text field: drag a box.",
    "field_checkbox": "Add checkbox: click or drag a small box.",
    "field_dropdown": "Add dropdown: drag a box.",
}

STYLE = """
QMainWindow { background:#f3f4f6; }
QToolBar { background:#ffffff; border:none; border-bottom:1px solid #dcdfe4; spacing:2px; padding:3px; }
QToolBar QToolButton { padding:4px 7px; border-radius:5px; }
QToolBar QToolButton:hover { background:#eef2fb; }
QToolBar QToolButton:checked { background:#dbe6fd; color:#0b4fd6; }
QDockWidget::title { background:#ffffff; padding:5px; border-bottom:1px solid #dcdfe4; }
QListWidget#thumbs { background:#e9ebef; border:none; }
QListWidget#thumbs::item { color:#333; border-radius:6px; padding:4px; }
QListWidget#thumbs::item:selected { background:#c9d8fb; color:#0b3a9e; }
#toolsPanel { background:#ffffff; }
#toolsPanel QLabel[group="true"] { font-weight:bold; color:#5a6270; padding:10px 4px 3px 4px; }
#toolsPanel QPushButton { text-align:left; padding:6px 10px; border:none; border-radius:5px; }
#toolsPanel QPushButton:hover { background:#eef2fb; }
QStatusBar { background:#ffffff; border-top:1px solid #dcdfe4; }
"""


def icon(widget, name):
    return widget.style().standardIcon(getattr(QStyle, name))


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        WINDOWS.append(self)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.settings = QSettings("PDFStudio", "PDFStudio")
        self.doc: pymupdf.Document | None = None
        self.path: str | None = None
        self.dirty = False
        self.undo_stack: list[bytes] = []
        self.redo_stack: list[bytes] = []
        self.signature_png: bytes | None = None
        self.stamp_name = "Approved"
        self.custom_stamp_text = "Received"
        self.search_hits: list[tuple[int, pymupdf.Rect]] = []
        self.search_index = -1
        self.search_query = ""
        self._thumb_queue: list[int] = []

        self.setStyleSheet(STYLE)
        self.resize(1360, 860)
        self.view = PageView(self)
        self.setCentralWidget(self.view)
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_docks()
        self.statusBar().showMessage("Open a PDF to get started (Ctrl+O), or drop files here.")
        self.page_label = QLabel()
        self.statusBar().addPermanentWidget(self.page_label)

        self.view.toolFinished.connect(self.on_tool)
        self.view.widgetClicked.connect(self.on_widget_clicked)
        self.view.annotMoved.connect(self.on_annot_moved)
        self.view.currentPageChanged.connect(self.on_page_changed)
        self.view.zoomChanged.connect(self.on_zoom_changed)
        self.view.filesDropped.connect(self.on_files_dropped)

        self._thumb_timer = QTimer(self, interval=0)
        self._thumb_timer.timeout.connect(self._render_thumb_batch)

        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self._update_title()
        self._update_enabled()
        if path:
            QTimer.singleShot(0, lambda: self.open_path(path))

    # ================================================================ UI build
    def _act(self, text, slot, shortcut=None, tip=None, std_icon=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(tip)
        if std_icon:
            a.setIcon(icon(self, std_icon))
        a.setCheckable(checkable)
        if slot:
            a.triggered.connect(lambda checked=False, s=slot: self._safe(s))
        self._all_actions.append(a)
        return a

    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except PermissionError as e:
            QMessageBox.warning(self, APP_NAME, f"Permission denied:\n{e}\n\nIs the file open in another program?")
        except Exception as e:  # keep the app alive, show the error
            traceback.print_exc()
            QMessageBox.critical(self, APP_NAME, f"Something went wrong:\n\n{e}")

    def _build_actions(self):
        self._all_actions = []
        A = self._act
        self.a_open = A("&Open…", self.open_dialog, "Ctrl+O", "Open a PDF or image", "SP_DialogOpenButton")
        self.a_new_win = A("New &window", lambda: MainWindow().show(), "Ctrl+N")
        self.a_save = A("&Save", self.save, "Ctrl+S", "Save", "SP_DialogSaveButton")
        self.a_save_as = A("Save &as…", self.save_as, "Ctrl+Shift+S")
        self.a_print = A("&Print…", self.print_doc, "Ctrl+P", "Print")
        self.a_close = A("&Close document", self.close_document, "Ctrl+W")
        self.a_props = A("Document &properties…", self.properties, "Ctrl+D")
        self.a_exit = A("E&xit", self.close, "Alt+F4")
        self.a_undo = A("&Undo", self.undo, "Ctrl+Z", "Undo", "SP_ArrowBack")
        self.a_redo = A("&Redo", self.redo, "Ctrl+Y", "Redo", "SP_ArrowForward")
        self.a_find = A("&Find…", lambda: (self.find_box.setFocus(), self.find_box.selectAll()), "Ctrl+F")
        self.a_copy_page_text = A("Copy all text of page", self.copy_page_text)
        self.a_zoom_in = A("Zoom &in", lambda: self.view.set_zoom(self.view.zoom * 1.2), "Ctrl++")
        self.a_zoom_out = A("Zoom &out", lambda: self.view.set_zoom(self.view.zoom / 1.2), "Ctrl+-")
        self.a_fit_w = A("Fit &width", self.view.fit_width, "Ctrl+2")
        self.a_fit_p = A("Fit &page", self.view.fit_page, "Ctrl+0")
        self.a_actual = A("&Actual size", lambda: self.view.set_zoom(1.0), "Ctrl+1")
        self.a_first = A("First page", lambda: self.view.go_to(0), "Home")
        self.a_last = A("Last page", lambda: self.view.go_to(10 ** 9), "End")

        # tools (checkable, exclusive)
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions = {}
        tools = [
            ("hand", "Hand", "H"), ("select_text", "Select text", "V"),
            ("highlight", "Highlight", "Ctrl+Shift+H"), ("underline", "Underline", None),
            ("strikeout", "Strikethrough", None), ("note", "Sticky note", "Ctrl+Shift+N"),
            ("textbox", "Text box", None), ("ink", "Draw", "Ctrl+Shift+D"),
            ("arrow", "Arrow", None), ("line", "Line", None), ("rect", "Rectangle", None),
            ("ellipse", "Oval", None), ("stamp", "Stamp", None), ("signature", "Fill && Sign", "Ctrl+Shift+G"),
            ("move_annot", "Move comment", None), ("delete_annot", "Delete comment", None),
            ("add_text", "Add text", "Ctrl+T"), ("edit_text", "Edit text", "Ctrl+E"),
            ("image", "Add image", None), ("redact", "Redact", None),
            ("field_text", "Text field", None), ("field_checkbox", "Checkbox", None),
            ("field_dropdown", "Dropdown", None),
        ]
        for key, text, sc in tools:
            a = QAction(text, self)
            a.setCheckable(True)
            if sc:
                a.setShortcut(QKeySequence(sc))
            a.setToolTip(TOOL_HINTS.get(key, text))
            a.triggered.connect(lambda checked=False, k=key: self.set_tool(k))
            self.tool_group.addAction(a)
            self.tool_actions[key] = a
        self.tool_actions["hand"].setChecked(True)

        # organize
        self.a_rot_l = A("Rotate left", lambda: self.rotate_selected(-90), "Ctrl+Shift+-")
        self.a_rot_r = A("Rotate right", lambda: self.rotate_selected(90), "Ctrl+Shift++")
        self.a_rotate = A("Rotate pages…", self.rotate_dialog)
        self.a_delete_pages = A("Delete pages…", self.delete_pages_dialog)
        self.a_delete_sel = A("Delete selected pages", self.delete_selected, "Delete")
        self.a_extract = A("Extract pages…", self.extract_dialog)
        self.a_insert_blank = A("Insert blank page", self.insert_blank)
        self.a_insert_file = A("Insert pages from file…", self.insert_file_dialog)
        self.a_duplicate = A("Duplicate page", self.duplicate_page)
        self.a_reverse = A("Reverse page order", self.reverse_pages)
        self.a_split = A("Split document…", self.split_dialog)
        self.a_merge = A("Combine files…", self.merge_dialog, "Ctrl+Shift+M")
        self.a_crop = A("Crop pages…", self.crop_dialog)
        self.a_move_up = A("Move page up", lambda: self.move_selected(-1), "Ctrl+Up")
        self.a_move_down = A("Move page down", lambda: self.move_selected(1), "Ctrl+Down")

        # edit pdf
        self.a_find_replace = A("Find && replace text…", self.find_replace_dialog, "Ctrl+H")
        self.a_watermark = A("Add watermark…", self.watermark_dialog)
        self.a_numbers = A("Add page numbers…", self.page_numbers_dialog)
        self.a_flatten = A("Flatten comments && forms", self.flatten)

        # forms
        self.a_fill = A("Fill form fields…", lambda: self.fill_forms())

        # convert
        self.a_to_word = A("Export to Word (.docx)…", self.export_word)
        self.a_to_excel = A("Export to Excel (.xlsx)…", self.export_excel)
        self.a_to_images = A("Export to images…", self.export_images)
        self.a_to_text = A("Export to text (.txt)…", self.export_text)
        self.a_from_images = A("Create PDF from images…", self.create_from_images)
        self.a_from_office = A("Create PDF from Word / Excel / PowerPoint…", self.create_from_office)
        self.a_compress = A("Compress (reduce file size)…", self.compress_dialog)
        self.a_ocr = A("Recognize text (OCR)…", self.ocr_dialog)

        # protect
        self.a_encrypt = A("Protect with password…", self.encrypt_dialog)
        self.a_decrypt = A("Remove password", self.remove_password)

        self.a_about = A("About", self.about)
        self.a_keys = A("Keyboard shortcuts", self.shortcuts_help, "F1")

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_open, self.a_new_win):
            m.addAction(a)
        self.recent_menu = m.addMenu("Open &recent")
        self._fill_recent()
        m.addSeparator()
        for a in (self.a_save, self.a_save_as, self.a_print):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_from_images)
        m.addAction(self.a_from_office)
        m.addAction(self.a_merge)
        m.addSeparator()
        m.addAction(self.a_props)
        m.addAction(self.a_close)
        m.addAction(self.a_exit)

        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_find)
        m.addAction(self.a_find_replace)
        m.addAction(self.a_copy_page_text)

        m = mb.addMenu("&View")
        for a in (self.a_zoom_in, self.a_zoom_out, self.a_actual, self.a_fit_w, self.a_fit_p):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_first)
        m.addAction(self.a_last)
        self.view_menu = m

        m = mb.addMenu("&Organize")
        for a in (self.a_rot_l, self.a_rot_r, self.a_rotate, None, self.a_insert_blank, self.a_insert_file,
                  self.a_duplicate, self.a_move_up, self.a_move_down, self.a_reverse, None,
                  self.a_delete_sel, self.a_delete_pages, self.a_extract, self.a_split, self.a_merge,
                  None, self.a_crop):
            m.addSeparator() if a is None else m.addAction(a)

        m = mb.addMenu("&Comment")
        for k in ("highlight", "underline", "strikeout", "note", "textbox", None, "ink", "arrow", "line",
                  "rect", "ellipse", None, "stamp", "signature", None, "move_annot", "delete_annot"):
            m.addSeparator() if k is None else m.addAction(self.tool_actions[k])

        m = mb.addMenu("E&dit PDF")
        for k in ("add_text", "edit_text", "image"):
            m.addAction(self.tool_actions[k])
        m.addAction(self.a_find_replace)
        m.addSeparator()
        m.addAction(self.a_watermark)
        m.addAction(self.a_numbers)
        m.addAction(self.tool_actions["redact"])
        m.addAction(self.a_flatten)

        m = mb.addMenu("F&orms")
        m.addAction(self.a_fill)
        m.addSeparator()
        for k in ("field_text", "field_checkbox", "field_dropdown"):
            m.addAction(self.tool_actions[k])
        m.addSeparator()
        m.addAction(self.tool_actions["signature"])
        m.addAction(self.a_flatten)

        m = mb.addMenu("Co&nvert")
        for a in (self.a_to_word, self.a_to_excel, self.a_to_images, self.a_to_text, None,
                  self.a_from_images, self.a_from_office, None, self.a_compress, self.a_ocr):
            m.addSeparator() if a is None else m.addAction(a)

        m = mb.addMenu("&Protect")
        m.addAction(self.a_encrypt)
        m.addAction(self.a_decrypt)
        m.addAction(self.tool_actions["redact"])
        m.addAction(self.a_props)

        m = mb.addMenu("&Help")
        m.addAction(self.a_keys)
        m.addAction(self.a_about)

    def _build_toolbars(self):
        tb = QToolBar("Main")
        tb.setObjectName("main_tb")
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(self.a_open)
        tb.addAction(self.a_save)
        b = QAction("Print", self)
        b.triggered.connect(lambda: self._safe(self.print_doc))
        b.setIcon(icon(self, "SP_FileDialogDetailedView"))
        tb.addAction(b)
        tb.addSeparator()
        tb.addAction(self.a_undo)
        tb.addAction(self.a_redo)
        tb.addSeparator()
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.setFixedWidth(64)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(lambda v: self.view.go_to(v - 1))
        self.page_count_label = QLabel(" / 0 ")
        tb.addWidget(QLabel(" Page "))
        tb.addWidget(self.page_spin)
        tb.addWidget(self.page_count_label)
        tb.addSeparator()
        zo = QToolButton(text="−")
        zo.clicked.connect(self.a_zoom_out.trigger)
        zi = QToolButton(text="+")
        zi.clicked.connect(self.a_zoom_in.trigger)
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.addItems(["Fit width", "Fit page", "50%", "75%", "100%", "125%", "150%", "200%", "300%", "400%"])
        self.zoom_box.setFixedWidth(100)
        self.zoom_box.textActivated.connect(self._zoom_from_box)
        self.zoom_box.lineEdit().returnPressed.connect(lambda: self._zoom_from_box(self.zoom_box.currentText()))
        tb.addWidget(zo)
        tb.addWidget(self.zoom_box)
        tb.addWidget(zi)
        tb.addSeparator()
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText("Find text…  (Enter = next)")
        self.find_box.setClearButtonEnabled(True)
        self.find_box.setFixedWidth(220)
        self.find_box.returnPressed.connect(lambda: self._safe(self.find_next, 1))
        tb.addWidget(self.find_box)
        prev_b = QToolButton(text="▲")
        prev_b.setToolTip("Previous match (Shift+F3)")
        prev_b.clicked.connect(lambda: self._safe(self.find_next, -1))
        next_b = QToolButton(text="▼")
        next_b.setToolTip("Next match (F3)")
        next_b.clicked.connect(lambda: self._safe(self.find_next, 1))
        tb.addWidget(prev_b)
        tb.addWidget(next_b)
        a = QAction(self)
        a.setShortcut("F3")
        a.triggered.connect(lambda: self._safe(self.find_next, 1))
        self.addAction(a)
        a = QAction(self)
        a.setShortcut("Shift+F3")
        a.triggered.connect(lambda: self._safe(self.find_next, -1))
        self.addAction(a)
        self.find_status = QLabel("")
        tb.addWidget(self.find_status)

        self.addToolBarBreak()
        tb2 = QToolBar("Tools")
        tb2.setObjectName("tools_tb")
        tb2.setMovable(False)
        tb2.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb2)
        for k in ("hand", "select_text", None, "highlight", "underline", "strikeout", "note", "textbox",
                  None, "ink", "arrow", "rect", "ellipse", None, "stamp", "signature", None,
                  "add_text", "edit_text", "image", None, "move_annot", "delete_annot"):
            tb2.addSeparator() if k is None else tb2.addAction(self.tool_actions[k])
        tb2.addSeparator()
        tb2.addWidget(QLabel(" Colour "))
        self.color_btn = ColorButton(self.settings.value("color", "#e53935"))
        self.color_btn.clicked.connect(self._style_changed)
        tb2.addWidget(self.color_btn)
        tb2.addWidget(QLabel("  Width "))
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.5, 20)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setValue(float(self.settings.value("width", 2.0)))
        self.width_spin.valueChanged.connect(self._style_changed)
        tb2.addWidget(self.width_spin)
        tb2.addWidget(QLabel("  Text "))
        self.font_size = QSpinBox()
        self.font_size.setRange(4, 144)
        self.font_size.setValue(int(self.settings.value("font_size", 12)))
        self.font_size.setSuffix(" pt")
        tb2.addWidget(self.font_size)
        self._style_changed()

    def _build_docks(self):
        # left: thumbnails + bookmarks
        self.thumbs = QListWidget()
        self.thumbs.setObjectName("thumbs")
        self.thumbs.setViewMode(QListWidget.ListMode)
        self.thumbs.setFlow(QListWidget.TopToBottom)
        self.thumbs.setIconSize(QSize(130, 170))
        self.thumbs.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.thumbs.setDragDropMode(QAbstractItemView.InternalMove)
        self.thumbs.setDefaultDropAction(Qt.MoveAction)
        self.thumbs.setSpacing(4)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self._thumb_menu)
        self.thumbs.itemClicked.connect(lambda it: self.view.go_to(self.thumbs.row(it)))
        self.thumbs.model().rowsMoved.connect(lambda *a: QTimer.singleShot(0, self._thumbs_reordered))
        self.toc = QTreeWidget()
        self.toc.setHeaderHidden(True)
        self.toc.itemClicked.connect(self._toc_clicked)
        tabs = QTabWidget()
        tabs.addTab(self.thumbs, "Pages")
        tabs.addTab(self.toc, "Bookmarks")
        d = QDockWidget("Navigation", self)
        d.setObjectName("nav_dock")
        d.setWidget(tabs)
        d.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.LeftDockWidgetArea, d)
        self.view_menu.addSeparator()
        self.view_menu.addAction(d.toggleViewAction())

        # right: all tools panel
        panel = QWidget()
        panel.setObjectName("toolsPanel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(0)
        groups = [
            ("Organize pages", [self.a_merge, self.a_split, self.a_extract, self.a_insert_file,
                                self.a_insert_blank, self.a_rotate, self.a_delete_pages, self.a_crop]),
            ("Edit PDF", [self.tool_actions["add_text"], self.tool_actions["edit_text"], self.a_find_replace,
                          self.tool_actions["image"], self.a_watermark, self.a_numbers]),
            ("Comment & sign", [self.tool_actions["highlight"], self.tool_actions["note"],
                                self.tool_actions["ink"], self.tool_actions["stamp"],
                                self.tool_actions["signature"]]),
            ("Forms", [self.a_fill, self.tool_actions["field_text"], self.tool_actions["field_checkbox"],
                       self.tool_actions["field_dropdown"], self.a_flatten]),
            ("Convert", [self.a_to_word, self.a_to_excel, self.a_to_images, self.a_from_images,
                         self.a_from_office, self.a_compress, self.a_ocr]),
            ("Protect", [self.a_encrypt, self.a_decrypt, self.tool_actions["redact"], self.a_props]),
        ]
        self.panel_buttons = []
        for title, acts in groups:
            lbl = QLabel(title.upper())
            lbl.setProperty("group", True)
            lay.addWidget(lbl)
            for a in acts:
                b = QPushButton(a.text().replace("…", ""))
                b.setToolTip(a.toolTip())
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(a.trigger)
                lay.addWidget(b)
                self.panel_buttons.append((b, a))
        lay.addStretch()
        sa = QScrollArea()
        sa.setWidget(panel)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.NoFrame)
        d2 = QDockWidget("All tools", self)
        d2.setObjectName("tools_dock")
        d2.setWidget(sa)
        d2.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        d2.setMinimumWidth(230)
        self.addDockWidget(Qt.RightDockWidgetArea, d2)
        self.view_menu.addAction(d2.toggleViewAction())
        state = self.settings.value("state")
        if state is not None:
            self.restoreState(state)

    # ================================================================ state helpers
    def _style_changed(self, *_):
        self.view.color = self.color_btn.color
        self.view.width = self.width_spin.value()
        self.settings.setValue("color", self.color_btn.color)
        self.settings.setValue("width", self.width_spin.value())

    def _update_title(self):
        name = os.path.basename(self.path) if self.path else ("Untitled" if self.doc else "")
        star = "*" if self.dirty else ""
        self.setWindowTitle(f"{star}{name} – {APP_NAME}" if name else APP_NAME)

    def _update_enabled(self):
        has = self.doc is not None
        always = {self.a_open, self.a_new_win, self.a_merge, self.a_from_images, self.a_from_office,
                  self.a_exit, self.a_about, self.a_keys}
        for a in self._all_actions + list(self.tool_actions.values()):
            if a not in always:
                a.setEnabled(has)
        self.a_undo.setEnabled(has and bool(self.undo_stack))
        self.a_redo.setEnabled(has and bool(self.redo_stack))
        for b, a in self.panel_buttons:
            b.setEnabled(a.isEnabled())

    def _fill_recent(self):
        self.recent_menu.clear()
        files = self._recent()
        for f in files[:12]:
            a = self.recent_menu.addAction(f)
            a.triggered.connect(lambda checked=False, p=f: self._safe(self.open_path, p))
        if not files:
            a = self.recent_menu.addAction("(none)")
            a.setEnabled(False)

    def _recent(self):
        v = self.settings.value("recent", []) or []
        if isinstance(v, str):
            v = [v]
        return [f for f in v if isinstance(f, str)]

    def _add_recent(self, path):
        files = [f for f in self._recent() if f != path]
        files.insert(0, path)
        self.settings.setValue("recent", files[:12])
        for w in WINDOWS:
            w._fill_recent()

    # ================================================================ open / save
    def open_dialog(self):
        start = os.path.dirname(self.path) if self.path else self.settings.value("last_dir", "")
        files, _ = QFileDialog.getOpenFileNames(self, "Open", start, ALL_OPEN_FILTER)
        for f in files:
            self.open_path(f)  # first one opens here if empty, others in new windows

    def open_path(self, path):
        if self.doc is not None:
            # keep one document per window: open in a new window
            w = MainWindow()
            w.show()
            w.open_path(path)
            return
        password = None
        while True:
            try:
                doc = ops.open_any(path, password)
                break
            except PermissionError:
                password, ok = QInputDialog.getText(self, "Password", f"“{os.path.basename(path)}” is protected.\nEnter the password:",
                                                    QLineEdit.Password)
                if not ok:
                    return
            except Exception as e:
                QMessageBox.critical(self, APP_NAME, f"Cannot open the file:\n{path}\n\n{e}")
                return
        self.doc = doc
        is_pdf = path.lower().endswith(".pdf")
        self.path = path if is_pdf else None
        self.dirty = not is_pdf
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.settings.setValue("last_dir", os.path.dirname(path))
        if is_pdf:
            self._add_recent(path)
        self._after_load()
        if password:
            self.statusBar().showMessage("Opened a protected PDF. Saving keeps it unprotected unless you use Protect ▸ Protect with password.", 8000)

    def load_new_document(self, doc, title_dirty=True):
        if self.doc is not None:
            w = MainWindow()
            w.show()
            w.load_new_document(doc, title_dirty)
            return
        self.doc = doc
        self.path = None
        self.dirty = title_dirty
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._after_load()

    def _after_load(self):
        self.view.set_document(self.doc)
        QTimer.singleShot(0, self.view.fit_width)
        self._rebuild_thumbs()
        self._rebuild_toc()
        self._update_pages_ui()
        self._update_title()
        self._update_enabled()
        self.search_hits = []
        self.search_query = ""
        if self.doc and self.doc.is_form_pdf:
            self.statusBar().showMessage("This PDF has form fields – click a field with the Hand tool, or use Forms ▸ Fill form fields.", 8000)

    def close_document(self):
        if not self.maybe_save():
            return
        self.doc = None
        self.path = None
        self.dirty = False
        self.view.clear()
        self.thumbs.clear()
        self.toc.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._update_pages_ui()
        self._update_title()
        self._update_enabled()

    def maybe_save(self):
        if not self.doc or not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save changes to the document before closing?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save()
        return True

    def save(self):
        if not self.doc:
            return False
        if not self.path:
            return self.save_as()
        return self._write(self.path, ops.save_bytes(self.doc))

    def save_as(self):
        if not self.doc:
            return False
        start = self.path or os.path.join(self.settings.value("last_dir", ""), "document.pdf")
        f, _ = QFileDialog.getSaveFileName(self, "Save as", start, "PDF files (*.pdf)")
        if not f:
            return False
        if not f.lower().endswith(".pdf"):
            f += ".pdf"
        if self._write(f, ops.save_bytes(self.doc)):
            self.path = f
            self._add_recent(f)
            self._update_title()
            return True
        return False

    def _write(self, path, data: bytes):
        tmp = path + ".pdfstudio-tmp"
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except PermissionError:
            if os.path.exists(tmp):
                os.remove(tmp)
            QMessageBox.warning(self, APP_NAME, f"Cannot write to:\n{path}\n\nThe file may be open in another program. "
                                                "Close it there, or use Save as.")
            return False
        if path == self.path or self.path is None:
            self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}", 5000)
        return True

    def closeEvent(self, e):
        if not self.maybe_save():
            e.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        self.settings.setValue("font_size", self.font_size.value())
        if self in WINDOWS:
            WINDOWS.remove(self)
        e.accept()

    def on_files_dropped(self, files):
        if self.doc is None:
            self.open_path(files[0])
            for f in files[1:]:
                self.open_path(f)
            return
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setText(f"{len(files)} file(s) dropped. What would you like to do?")
        b_ins = box.addButton("Insert after current page", QMessageBox.AcceptRole)
        b_end = box.addButton("Append at end", QMessageBox.AcceptRole)
        b_open = box.addButton("Open in new window", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        c = box.clickedButton()
        if c == b_open:
            for f in files:
                self.open_path(f)
        elif c in (b_ins, b_end):
            at = self.view.current_page() + 1 if c == b_ins else self.doc.page_count

            def do():
                pos = at
                for f in files:
                    pos += ops.insert_file(self.doc, f, pos)
            self.mutate("Insert files", do, structure=True)

    # ================================================================ undo / mutate
    def mutate(self, label, fn, pages=None, structure=False):
        """Run fn() that edits self.doc, with undo support."""
        if not self.doc:
            return None
        snap = self.doc.tobytes()
        try:
            result = fn()
        except Exception:
            # restore the document to its previous state
            self.doc = pymupdf.open("pdf", snap)
            self.view.set_document(self.doc, keep_position=True)
            raise
        if result is False:
            return result
        self.undo_stack.append(snap)
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.dirty = True
        self._after_change(pages, structure)
        self.statusBar().showMessage(label, 3000)
        return result

    def _after_change(self, pages=None, structure=False):
        if structure:
            self.view.set_document(self.doc, keep_position=True)
            self._rebuild_thumbs()
            self._rebuild_toc()
        else:
            self.view.refresh(pages)
            self._update_thumbs(pages)
        self.search_hits = []
        self.search_query = ""
        self.view.clear_marks()
        self._update_pages_ui()
        self._update_title()
        self._update_enabled()

    def _restore(self, data):
        cur = self.view.current_page()
        self.doc = pymupdf.open("pdf", data)
        self.dirty = True
        self._after_change(structure=True)
        self.view.go_to(min(cur, self.doc.page_count - 1))

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.doc.tobytes())
        self._restore(self.undo_stack.pop())
        self.statusBar().showMessage("Undone", 2000)

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.doc.tobytes())
        self._restore(self.redo_stack.pop())
        self.statusBar().showMessage("Redone", 2000)

    # ================================================================ thumbnails / toc
    def _rebuild_thumbs(self):
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        if not self.doc:
            self.thumbs.blockSignals(False)
            return
        ph = QPixmap(110, 150)
        ph.fill(QColor("#ffffff"))
        for i in range(self.doc.page_count):
            it = QListWidgetItem(QIcon(ph), str(i + 1))
            it.setData(Qt.UserRole, i)
            it.setTextAlignment(Qt.AlignHCenter)
            self.thumbs.addItem(it)
        self.thumbs.blockSignals(False)
        self._thumb_queue = list(range(self.doc.page_count))
        self._thumb_timer.start()

    def _update_thumbs(self, pages=None):
        if not self.doc:
            return
        if self.thumbs.count() != self.doc.page_count:
            self._rebuild_thumbs()
            return
        targets = range(self.doc.page_count) if pages is None else pages
        for p in targets:
            if p not in self._thumb_queue:
                self._thumb_queue.append(p)
        self._thumb_timer.start()

    def _render_thumb_batch(self):
        if not self.doc or not self._thumb_queue:
            self._thumb_timer.stop()
            return
        dpr = self.devicePixelRatioF()
        for _ in range(6):
            if not self._thumb_queue:
                break
            i = self._thumb_queue.pop(0)
            if i >= self.doc.page_count or i >= self.thumbs.count():
                continue
            page = self.doc[i]
            z = min(110 / page.rect.width, 150 / page.rect.height) * dpr
            pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
            qp = QPixmap.fromImage(img)
            qp.setDevicePixelRatio(dpr)
            ic = QIcon()
            ic.addPixmap(qp, QIcon.Normal)
            ic.addPixmap(qp, QIcon.Selected)  # no blue tint on the selected page
            self.thumbs.item(i).setIcon(ic)

    def _thumbs_reordered(self):
        if not self.doc:
            return
        order = [self.thumbs.item(i).data(Qt.UserRole) for i in range(self.thumbs.count())]
        if order == list(range(self.doc.page_count)):
            return
        self.mutate("Pages reordered", lambda: ops.reorder(self.doc, order), structure=True)

    def _rebuild_toc(self):
        self.toc.clear()
        if not self.doc:
            return
        stack = [self.toc.invisibleRootItem()]
        for lvl, title, page, *rest in self.doc.get_toc(simple=False):
            while len(stack) > lvl:
                stack.pop()
            while len(stack) < lvl:
                stack.append(stack[-1])
            it = QTreeWidgetItem([title])
            it.setData(0, Qt.UserRole, page - 1)
            stack[-1].addChild(it)
            stack.append(it)
        if self.toc.topLevelItemCount() == 0:
            it = QTreeWidgetItem(["(no bookmarks)"])
            it.setFlags(Qt.NoItemFlags)
            self.toc.addTopLevelItem(it)

    def _toc_clicked(self, it, _col):
        p = it.data(0, Qt.UserRole)
        if p is not None and p >= 0:
            self.view.go_to(p)

    def selected_pages(self):
        rows = sorted(self.thumbs.row(i) for i in self.thumbs.selectedItems())
        return rows or [self.view.current_page()]

    def _thumb_menu(self, pos):
        if not self.doc:
            return
        m = QMenu(self)
        for a in (self.a_rot_l, self.a_rot_r, None, self.a_duplicate, self.a_insert_blank, self.a_insert_file,
                  None, self.a_move_up, self.a_move_down, None, self.a_delete_sel):
            m.addSeparator() if a is None else m.addAction(a)
        ex = m.addAction("Extract selected pages…")
        ex.triggered.connect(lambda: self._safe(self._extract_pages, self.selected_pages()))
        m.exec(self.thumbs.mapToGlobal(pos))

    # ================================================================ navigation
    def on_page_changed(self, p):
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(p + 1)
        self.page_spin.blockSignals(False)
        if 0 <= p < self.thumbs.count() and not self.thumbs.item(p).isSelected():
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(p)
            self.thumbs.blockSignals(False)
        self._update_page_label()

    def on_zoom_changed(self, z):
        self.zoom_box.setEditText(f"{round(z * 100)}%")
        self._update_page_label()

    def _zoom_from_box(self, text):
        if text == "Fit width":
            self.view.fit_width()
        elif text == "Fit page":
            self.view.fit_page()
        else:
            try:
                self.view.set_zoom(float(text.strip().rstrip("%")) / 100)
            except ValueError:
                pass

    def _update_pages_ui(self):
        n = self.doc.page_count if self.doc else 0
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1, max(1, n))
        self.page_spin.blockSignals(False)
        self.page_count_label.setText(f" / {n} ")
        self._update_page_label()

    def _update_page_label(self):
        if not self.doc:
            self.page_label.setText("")
            return
        p = self.view.current_page()
        r = self.doc[min(p, self.doc.page_count - 1)].rect
        self.page_label.setText(f"Page {p + 1} of {self.doc.page_count}   |   "
                                f"{r.width / 72 * 25.4:.0f} × {r.height / 72 * 25.4:.0f} mm   |   "
                                f"{round(self.view.zoom * 100)}%")

    # ================================================================ tools
    def set_tool(self, tool):
        if tool == "signature":
            dlg = SignatureDialog(self)
            if not dlg.exec() or not dlg.result_png:
                self.tool_actions["hand"].setChecked(True)
                self.view.set_tool("hand")
                return
            self.signature_png = dlg.result_png
        elif tool == "stamp":
            names = list(ops.STAMPS) + ["Custom text stamp…"]
            cur = names.index(self.stamp_name) if self.stamp_name in names else 0
            name, ok = QInputDialog.getItem(self, "Stamp", "Choose a stamp:", names, cur, False)
            if not ok:
                self.tool_actions["hand"].setChecked(True)
                self.view.set_tool("hand")
                return
            if name.startswith("Custom"):
                txt, ok = QInputDialog.getText(self, "Custom stamp", "Stamp text (today's date is added below it):",
                                               text=self.custom_stamp_text)
                if not ok or not txt.strip():
                    self.tool_actions["hand"].setChecked(True)
                    self.view.set_tool("hand")
                    return
                self.custom_stamp_text = txt.strip()
                tool = "custom_stamp"
            else:
                self.stamp_name = name
        self.tool_actions["stamp" if tool == "custom_stamp" else tool].setChecked(True)
        self.view.set_tool(tool)
        self.statusBar().showMessage(TOOL_HINTS.get(tool, ""), 0)

    def on_tool(self, tool, pno, data):
        self._safe(self._handle_tool, tool, pno, data)

    def _line_rect(self, page, rect):
        """Expand a tiny click-rect to the whole text line under it."""
        rect = pymupdf.Rect(rect)
        if rect.width > 3 or rect.height > 3:
            return rect
        words = page.get_text("words")
        hit = [w for w in words if pymupdf.Rect(w[:4]).intersects(rect)]
        if not hit:
            return None
        b, l = hit[0][5], hit[0][6]
        r = None
        for w in words:
            if w[5] == b and w[6] == l:
                r = pymupdf.Rect(w[:4]) if r is None else r | pymupdf.Rect(w[:4])
        return r

    def _handle_tool(self, tool, pno, data):
        page = self.doc[pno]
        color = self.color_btn.color
        width = self.width_spin.value()
        fs = self.font_size.value()
        if tool == "select_text":
            rect = self._line_rect(page, data)
            if rect is None:
                return
            text = ops.text_in_rect(page, rect + (-1, -1, 1, 1))
            if text:
                QGuiApplication.clipboard().setText(text)
                self.view.show_marks(pno, [rect])
                QTimer.singleShot(900, self.view.clear_marks)
                self.statusBar().showMessage(f"Copied {len(text)} characters to the clipboard.", 4000)
            else:
                self.statusBar().showMessage("No text found there (scanned page? try Convert ▸ OCR).", 5000)
        elif tool in ("highlight", "underline", "strikeout"):
            hl_color = color if tool != "highlight" or color != "#e53935" else "#ffe600"
            self.mutate(tool.capitalize(), lambda: ops.text_markup(page, data, tool, hl_color), [pno])
        elif tool == "rect":
            self.mutate("Rectangle", lambda: ops.add_rect(page, data, color, width), [pno])
        elif tool == "ellipse":
            self.mutate("Oval", lambda: ops.add_ellipse(page, data, color, width), [pno])
        elif tool in ("line", "arrow"):
            self.mutate("Line", lambda: ops.add_line(page, data[0], data[1], color, width, tool == "arrow"), [pno])
        elif tool == "ink":
            self.mutate("Drawing", lambda: ops.add_ink(page, data, color, width), [pno])
        elif tool == "note":
            text, ok = QInputDialog.getMultiLineText(self, "Sticky note", "Comment:")
            if ok and text.strip():
                self.mutate("Note", lambda: ops.add_note(page, data, text), [pno])
        elif tool == "textbox":
            dlg = TextDialog(self, "Text box comment", "", fs, color if color != "#e53935" else "#000000")
            if dlg.exec():
                text, size, col, _font = dlg.values()
                if text.strip():
                    r = pymupdf.Rect(data)
                    lines = text.count("\n") + 1
                    longest = max(pymupdf.get_text_length(s, "helv", size) for s in text.split("\n"))
                    r.x1 = max(r.x1, r.x0 + longest + 10)
                    r.y1 = max(r.y1, r.y0 + lines * size * 1.3 + 6)
                    self.mutate("Text box", lambda: ops.add_textbox_annot(page, r, text, size, col), [pno])
        elif tool == "add_text":
            dlg = TextDialog(self, "Add text", "", fs, "#000000")
            if dlg.exec():
                text, size, col, font = dlg.values()
                if text:
                    pt = pymupdf.Point(data.x, data.y + size * 0.8)
                    self.mutate("Text added", lambda: ops.add_text(page, pt, text, size, col, font), [pno])
        elif tool == "edit_text":
            rect = self._line_rect(page, data)
            if rect is None:
                self.statusBar().showMessage("No text there. Drag over the text you want to change.", 4000)
                return
            old = ops.text_in_rect(page, rect + (-1, -1, 1, 1))
            if not old:
                self.statusBar().showMessage("No editable text there (scanned image? try OCR).", 5000)
                return
            size, col, bold, serif, mono = ops.text_style_in_rect(page, rect)
            dlg = TextDialog(self, "Edit text", old, size, col, ops.font_for(bold, serif, mono), "Replace")
            if dlg.exec():
                text, size, col, font = dlg.values()
                self.mutate("Text edited", lambda: ops.replace_text_in_rect(page, rect, text, size, col, font), [pno])
        elif tool == "image":
            f, _ = QFileDialog.getOpenFileName(self, "Choose image", self.settings.value("last_dir", ""),
                                               "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)")
            if f:
                self.mutate("Image added", lambda: ops.add_image(page, data, f), [pno])
        elif tool == "signature":
            if self.signature_png:
                self.mutate("Signed", lambda: ops.add_signature(page, data, self.signature_png), [pno])
        elif tool == "stamp":
            self.mutate("Stamp", lambda: ops.add_stamp(page, data, self.stamp_name), [pno])
        elif tool == "custom_stamp":
            self.mutate("Stamp", lambda: ops.add_custom_stamp(page, data, self.custom_stamp_text, color), [pno])
        elif tool == "redact":
            self.mutate("Redacted", lambda: ops.redact(page, data), [pno])
        elif tool == "delete_annot":
            def do():
                if ops.delete_annot_at(page, data):
                    return True
                w = ops.widget_at(page, data)
                if w is not None:
                    page.delete_widget(w)
                    return True
                return False
            if self.mutate("Deleted", do, [pno]) is False:
                self.statusBar().showMessage("Nothing to delete there. (Text and images in the page itself: use Redact or Edit text.)", 5000)
        elif tool in ("field_text", "field_checkbox", "field_dropdown"):
            kind = tool.split("_")[1]
            n = len(ops.list_fields(self.doc)) + 1
            name, ok = QInputDialog.getText(self, "Field name", "Name of the new field:", text=f"{kind}{n}")
            if not ok:
                return
            choices = None
            if kind == "dropdown":
                txt, ok = QInputDialog.getText(self, "Dropdown", "Options (comma separated):", text="Option 1, Option 2, Option 3")
                if not ok:
                    return
                choices = [c.strip() for c in txt.split(",") if c.strip()]
            rect = pymupdf.Rect(data)
            if kind == "checkbox":
                s = max(10, min(rect.width, rect.height, 24))
                rect = pymupdf.Rect(rect.x0, rect.y0, rect.x0 + s, rect.y0 + s)
            self.mutate("Field added", lambda: ops.add_field(page, rect, kind, name.strip() or None, choices), [pno])

    def on_annot_moved(self, pno, xref, dx, dy):
        def do():
            if not ops.move_annot(self.doc[pno], xref, dx, dy):
                return False
        if self.mutate("Moved", do, [pno]) is False:
            self.statusBar().showMessage("That item cannot be moved (text highlights stay with their text).", 4000)

    def on_widget_clicked(self, pno, xref):
        page = self.doc[pno]
        for w in page.widgets():
            if w.xref == xref:
                if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
                    on = w.field_value not in (False, "Off", "", None)
                    self.mutate("Checkbox", lambda: ops.set_field(self.doc, pno, xref, not on), [pno])
                    return
                if w.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
                    self.mutate("Option", lambda: ops.set_field(self.doc, pno, xref, True), structure=False)
                    return
                break
        self.fill_forms(xref)

    def fill_forms(self, focus_xref=None):
        dlg = FormsDialog(self, self.doc, focus_xref)
        if dlg.exec() and dlg.fields:
            self.mutate("Form updated", dlg.apply)

    # ================================================================ page operations
    def rotate_selected(self, deg):
        pages = self.selected_pages()
        self.mutate("Rotated", lambda: ops.rotate_pages(self.doc, pages, deg), structure=True)
        self._reselect(pages)

    def _reselect(self, pages):
        for p in pages:
            if p < self.thumbs.count():
                self.thumbs.item(p).setSelected(True)

    def _ask_pages(self, title, label, default=None):
        n = self.doc.page_count
        text, ok = QInputDialog.getText(self, title, f"{label}\nPages (e.g. 1-3, 5, 8-):  [1–{n}]",
                                        text=default or self._sel_text())
        if not ok:
            return None
        pages = ops.parse_ranges(text, n)
        if not pages:
            QMessageBox.information(self, title, "No valid pages given.")
            return None
        return pages

    def _sel_text(self):
        return ",".join(str(p + 1) for p in self.selected_pages())

    def rotate_dialog(self):
        n = self.doc.page_count
        rng = QLineEdit(self._sel_text())
        ang = QComboBox()
        ang.addItems(["90° clockwise", "90° counter-clockwise", "180°"])
        hint = QLabel(f"e.g. 1-{n} for all pages")
        dlg = ChoiceDialog(self, "Rotate pages", [("Pages", rng), ("Direction", ang), ("", hint)], "Rotate")
        if dlg.exec():
            pages = ops.parse_ranges(rng.text(), n)
            deg = {0: 90, 1: -90, 2: 180}[ang.currentIndex()]
            if pages:
                self.mutate("Rotated", lambda: ops.rotate_pages(self.doc, pages, deg), structure=True)

    def delete_selected(self):
        if not (self.thumbs.hasFocus() or self.view.hasFocus()):
            return
        pages = self.selected_pages()
        if QMessageBox.question(self, "Delete pages", f"Delete {len(pages)} page(s)?") == QMessageBox.Yes:
            self.mutate("Pages deleted", lambda: ops.delete_pages(self.doc, pages), structure=True)

    def delete_pages_dialog(self):
        pages = self._ask_pages("Delete pages", "Pages to delete:")
        if pages:
            self.mutate("Pages deleted", lambda: ops.delete_pages(self.doc, pages), structure=True)

    def extract_dialog(self):
        pages = self._ask_pages("Extract pages", "Pages to save as a new PDF:")
        if pages:
            self._extract_pages(pages)

    def _extract_pages(self, pages):
        base = os.path.splitext(os.path.basename(self.path or "document.pdf"))[0]
        f, _ = QFileDialog.getSaveFileName(self, "Save extracted pages",
                                           os.path.join(self._out_dir(), f"{base}_extract.pdf"), "PDF files (*.pdf)")
        if f:
            ops.extract_pages(self.doc, pages, f)
            self._done_file(f, f"Extracted {len(pages)} page(s).")
        if f and QMessageBox.question(self, "Extract", "Also delete these pages from the current document?",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.mutate("Pages deleted", lambda: ops.delete_pages(self.doc, pages), structure=True)

    def insert_blank(self):
        at = self.selected_pages()[-1] + 1
        self.mutate("Blank page inserted", lambda: ops.insert_blank(self.doc, at), structure=True)
        self.view.go_to(at)

    def insert_file_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Insert pages from", self._out_dir(), ALL_OPEN_FILTER)
        if not files:
            return
        n = self.doc.page_count
        pos, ok = QInputDialog.getInt(self, "Insert pages", f"Insert after page (0 = at the start, {n} = at the end):",
                                      self.selected_pages()[-1] + 1, 0, n)
        if not ok:
            return

        def do():
            p = pos
            for f in files:
                p += ops.insert_file(self.doc, f, p)
        self.mutate("Pages inserted", do, structure=True)
        self.view.go_to(pos)

    def duplicate_page(self):
        p = self.selected_pages()[0]
        self.mutate("Page duplicated", lambda: ops.duplicate_page(self.doc, p), structure=True)

    def reverse_pages(self):
        n = self.doc.page_count
        self.mutate("Order reversed", lambda: ops.reorder(self.doc, list(range(n - 1, -1, -1))), structure=True)

    def move_selected(self, d):
        pages = self.selected_pages()
        target = pages[0] + d
        if target < 0 or pages[-1] + d >= self.doc.page_count:
            return
        self.mutate("Page moved", lambda: ops.move_pages(self.doc, pages, target), structure=True)
        self._reselect([p + d for p in pages])
        self.view.go_to(target)

    def split_dialog(self):
        dlg = SplitDialog(self, self.doc.page_count)
        if not dlg.exec():
            return
        mode, value = dlg.values()
        out = QFileDialog.getExistingDirectory(self, "Folder for the split files", self._out_dir())
        if not out:
            return
        base = os.path.splitext(os.path.basename(self.path or "document.pdf"))[0]
        data = self.doc.tobytes()

        def work():
            d = pymupdf.open("pdf", data)
            return ops.split_document(d, out, base, mode, value)
        files = self.run_busy("Splitting…", work)
        if files is not None:
            self._done_folder(out, f"Created {len(files)} files.")

    def merge_dialog(self):
        start = [self.path] if self.path and not self.dirty else []
        dlg = MergeDialog(self, start)
        if not dlg.exec():
            return
        files = dlg.files()
        if len(files) < 1:
            return

        def work():
            out = pymupdf.open()
            for f in files:
                src = ops.open_any(f)
                out.insert_pdf(src)
                src.close()
            return out.tobytes(garbage=3, deflate=True)
        data = self.run_busy("Combining files…", work)
        if data is not None:
            self.load_new_document(pymupdf.open("pdf", data))
            self.statusBar().showMessage(f"Combined {len(files)} files – use Save to store the result.", 8000)

    def crop_dialog(self):
        dlg = CropDialog(self, self.doc.page_count, self.view.current_page())
        if dlg.exec():
            pages = ops.parse_ranges(dlg.pages.text(), self.doc.page_count)
            m = dlg.margins_pt()
            self.mutate("Cropped", lambda: ops.crop_pages(self.doc, pages, m), structure=True)

    # ================================================================ edit pdf
    def find_replace_dialog(self):
        f = QLineEdit(self.find_box.text())
        r = QLineEdit()
        rng = QLineEdit(f"1-{self.doc.page_count}")
        dlg = ChoiceDialog(self, "Find & replace", [("Find", f), ("Replace with", r), ("Pages", rng),
                                                    ("", QLabel("Works on real text (not scanned pages). Matching is not case-sensitive."))],
                           "Replace all")
        if dlg.exec() and f.text():
            pages = ops.parse_ranges(rng.text(), self.doc.page_count)
            n = self.mutate("Replaced", lambda: ops.find_replace(self.doc, f.text(), r.text(), pages=pages))
            QMessageBox.information(self, "Find & replace", f"{n or 0} occurrence(s) replaced.")

    def watermark_dialog(self):
        dlg = WatermarkDialog(self, self.doc.page_count)
        if dlg.exec() and dlg.text.text():
            pages = ops.parse_ranges(dlg.pages.text(), self.doc.page_count)
            self.mutate("Watermark added", lambda: ops.add_watermark(
                self.doc, dlg.text.text(), pages, dlg.size.value(), dlg.color.color,
                dlg.opacity.value() / 100, dlg.angle.value()))

    def page_numbers_dialog(self):
        dlg = PageNumbersDialog(self, self.doc.page_count)
        if dlg.exec():
            pages = ops.parse_ranges(dlg.pages.text(), self.doc.page_count)
            self.mutate("Page numbers added", lambda: ops.add_page_numbers(
                self.doc, dlg.fmt.currentText(), dlg.pos.currentText(), dlg.size.value(),
                dlg.start.value(), pages, dlg.color.color))

    def flatten(self):
        if QMessageBox.question(self, "Flatten", "Make all comments and form fields a permanent part of the pages?\n"
                                                 "They can no longer be edited afterwards (Undo still works).") == QMessageBox.Yes:
            self.mutate("Flattened", lambda: ops.flatten_forms(self.doc))

    def copy_page_text(self):
        t = self.doc[self.view.current_page()].get_text("text", sort=True)
        QGuiApplication.clipboard().setText(t)
        self.statusBar().showMessage(f"Copied {len(t)} characters.", 3000)

    # ================================================================ search
    def find_next(self, direction=1):
        q = self.find_box.text().strip()
        if not self.doc or not q:
            self.view.clear_marks()
            self.find_status.setText("")
            return
        if q != self.search_query:
            self.search_query = q
            self.search_hits = []
            for pno in range(self.doc.page_count):
                for r in self.doc[pno].search_for(q):
                    self.search_hits.append((pno, r))
            cur = self.view.current_page()
            idx = next((i for i, (p, _) in enumerate(self.search_hits) if p >= cur), 0)
            self.search_index = idx - direction
        if not self.search_hits:
            self.find_status.setText("  no matches")
            self.view.clear_marks()
            return
        self.search_index = (self.search_index + direction) % len(self.search_hits)
        pno, rect = self.search_hits[self.search_index]
        same = [r for p, r in self.search_hits if p == pno]
        cur_i = [i for i, (p, _) in enumerate(self.search_hits) if p == pno].index(self.search_index)
        y = (pymupdf.Rect(rect) * self.doc[pno].rotation_matrix).y0
        self.view.go_to(pno, y)
        self.view.show_marks(pno, same, cur_i)
        self.find_status.setText(f"  {self.search_index + 1} of {len(self.search_hits)}")

    # ================================================================ convert
    def _out_dir(self):
        return os.path.dirname(self.path) if self.path else self.settings.value("last_dir", "")

    def _base(self):
        return os.path.splitext(os.path.basename(self.path or "document.pdf"))[0]

    def _temp_pdf(self):
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(tmp, "wb") as fh:
            fh.write(self.doc.tobytes(garbage=1))
        return tmp

    def export_word(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export to Word", os.path.join(self._out_dir(), self._base() + ".docx"),
                                           "Word document (*.docx)")
        if not f:
            return
        tmp = self._temp_pdf()
        try:
            r = self.run_busy("Converting to Word… (can take a while for long documents)", lambda: ops.to_word(tmp, f))
        finally:
            os.remove(tmp)
        if r is not None or os.path.exists(f):
            self._done_file(f, "Exported to Word.")

    def export_excel(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export to Excel", os.path.join(self._out_dir(), self._base() + ".xlsx"),
                                           "Excel workbook (*.xlsx)")
        if not f:
            return
        data = self.doc.tobytes()
        n = self.run_busy("Finding tables…", lambda: ops.to_excel(pymupdf.open("pdf", data), f))
        if n is not None:
            self._done_file(f, f"Exported {n} table(s) to Excel." if n else
                            "No ruled tables were found, so the text was exported line by line.")

    def export_images(self):
        fmt = QComboBox()
        fmt.addItems(["png", "jpg"])
        dpi = QSpinBox()
        dpi.setRange(36, 1200)
        dpi.setValue(150)
        rng = QLineEdit(f"1-{self.doc.page_count}")
        dlg = ChoiceDialog(self, "Export to images", [("Format", fmt), ("Resolution (DPI)", dpi), ("Pages", rng)], "Export")
        if not dlg.exec():
            return
        out = QFileDialog.getExistingDirectory(self, "Folder for the images", self._out_dir())
        if not out:
            return
        pages = ops.parse_ranges(rng.text(), self.doc.page_count)
        data = self.doc.tobytes()
        files = self.run_busy("Exporting images…", lambda: ops.export_images(
            pymupdf.open("pdf", data), out, self._base(), fmt.currentText(), dpi.value(), pages))
        if files is not None:
            self._done_folder(out, f"Saved {len(files)} image(s).")

    def export_text(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export text", os.path.join(self._out_dir(), self._base() + ".txt"),
                                           "Text file (*.txt)")
        if f:
            ops.to_text(self.doc, f)
            self._done_file(f, "Text exported.")

    def create_from_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose images (in order)", self.settings.value("last_dir", ""),
                                                "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)")
        if not files:
            return
        size = QComboBox()
        size.addItems(["Same as image", "A4", "Letter", "A3"])
        margin = QSpinBox()
        margin.setRange(0, 100)
        margin.setSuffix(" mm")
        dlg = ChoiceDialog(self, "Create PDF from images", [("Page size", size), ("Margin", margin),
                                                             ("", QLabel(f"{len(files)} image(s) selected"))], "Create")
        if not dlg.exec():
            return
        ps = {"Same as image": "fit", "A4": "a4", "Letter": "letter", "A3": "a3"}[size.currentText()]
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            r = self.run_busy("Creating PDF…", lambda: ops.images_to_pdf(files, tmp, ps, margin.value() * 72 / 25.4))
            if os.path.getsize(tmp):
                with open(tmp, "rb") as fh:
                    data = fh.read()
                self.load_new_document(pymupdf.open("pdf", data))
        finally:
            os.remove(tmp)

    def create_from_office(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose a document", self.settings.value("last_dir", ""),
                                           "Office documents (*.doc *.docx *.rtf *.odt *.txt *.xls *.xlsx *.xlsm *.csv *.ods *.ppt *.pptx *.odp)")
        if not f:
            return
        out = os.path.splitext(f)[0] + ".pdf"
        if os.path.exists(out):
            out, _ = QFileDialog.getSaveFileName(self, "Save PDF as", out, "PDF files (*.pdf)")
            if not out:
                return
        r = self.run_busy("Converting with Office… (this may take a minute)", lambda: ops.office_to_pdf(f, out))
        if os.path.exists(out):
            self.open_path(out)

    def compress_dialog(self):
        level = QComboBox()
        level.addItems(list(ops.COMPRESS_LEVELS))
        level.setCurrentIndex(1)
        gray = QCheckBox("Convert images to grayscale")
        before = len(self.doc.tobytes(garbage=3, deflate=True))
        dlg = ChoiceDialog(self, "Compress PDF", [("Level", level), ("", gray),
                                                  ("", QLabel(f"Current size ≈ {before / 1024 / 1024:.2f} MB"))], "Compress")
        if not dlg.exec():
            return
        data = self.doc.tobytes()
        lvl, g = level.currentText(), gray.isChecked()
        new = self.run_busy("Compressing…", lambda: ops.compress_to_bytes(pymupdf.open("pdf", data), lvl, g))
        if new is None:
            return

        def do():
            self.doc = pymupdf.open("pdf", new)
        self.mutate("Compressed", do, structure=True)
        QMessageBox.information(self, "Compress",
                                f"Before: {before / 1024 / 1024:.2f} MB\nAfter:  {len(new) / 1024 / 1024:.2f} MB "
                                f"({100 - len(new) * 100 / max(before, 1):.0f}% smaller)\n\nUse Save / Save as to keep it.")

    def ocr_dialog(self):
        if not ops.find_tessdata():
            r = QMessageBox.question(self, "OCR",
                                     "Text recognition uses the free Tesseract OCR engine, which is not installed.\n\n"
                                     "Open the download page now? (Install with default options, then restart PDF Studio.)")
            if r == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl("https://github.com/UB-Mannheim/tesseract/wiki"))
            return
        lang = QLineEdit("eng")
        rng = QLineEdit(f"1-{self.doc.page_count}")
        dpi = QSpinBox()
        dpi.setRange(150, 600)
        dpi.setValue(300)
        dlg = ChoiceDialog(self, "Recognize text (OCR)", [
            ("Language(s)", lang), ("Pages", rng), ("Scan resolution", dpi),
            ("", QLabel("Pages that already contain text are left as they are.\n"
                        "Several languages: eng+ara (each must be installed in Tesseract)."))], "Run OCR")
        if not dlg.exec():
            return
        pages = ops.parse_ranges(rng.text(), self.doc.page_count)
        data = self.doc.tobytes()
        new = self.run_busy("Recognizing text… (about 2–5 seconds per page)", lambda: ops.ocr_document(
            pymupdf.open("pdf", data), lang.text().strip() or "eng", dpi.value(), pages))
        if new is None:
            return

        def do():
            self.doc = pymupdf.open("pdf", new)
        self.mutate("OCR complete – text is now searchable and selectable", do, structure=True)

    # ================================================================ protect
    def encrypt_dialog(self):
        dlg = PasswordDialog(self)
        if not dlg.exec():
            return
        f, _ = QFileDialog.getSaveFileName(self, "Save protected PDF",
                                           os.path.join(self._out_dir(), self._base() + "_protected.pdf"), "PDF files (*.pdf)")
        if not f:
            return
        data = ops.encrypt_bytes(self.doc, dlg.pw1.text(), dlg.owner.text() or None,
                                 dlg.p_print.isChecked(), dlg.p_copy.isChecked(), dlg.p_edit.isChecked())
        tmp = f + ".pdfstudio-tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, f)
        self._done_file(f, "Protected PDF saved (AES-256). Keep the password safe – it cannot be recovered.")

    def remove_password(self):
        QMessageBox.information(self, "Remove password",
                                "The open document is already unlocked in PDF Studio.\n"
                                "Use File ▸ Save as… to save a copy without a password.")

    def properties(self):
        dlg = MetadataDialog(self, self.doc, self.path)
        if dlg.exec():
            vals = dlg.values()
            self.mutate("Properties updated", lambda: ops.set_metadata(self.doc, **vals))

    # ================================================================ print
    def print_doc(self):
        from PySide6.QtPrintSupport import QPrinter, QPrintDialog
        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(self._base())
        printer.setFromTo(1, self.doc.page_count)
        dlg = QPrintDialog(printer, self)
        dlg.setOption(QPrintDialog.PrintPageRange, True)
        dlg.setMinMax(1, self.doc.page_count)
        if not dlg.exec():
            return
        if printer.printRange() == QPrinter.PageRange:
            pages = list(range(printer.fromPage() - 1, printer.toPage()))
        elif printer.printRange() == QPrinter.CurrentPage:
            pages = [self.view.current_page()]
        else:
            pages = list(range(self.doc.page_count))
        dpi = min(printer.resolution(), 300)
        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.warning(self, "Print", "Could not start printing.")
            return
        prog = QProgressDialog("Printing…", "Cancel", 0, len(pages), self)
        prog.setWindowModality(Qt.WindowModal)
        try:
            for i, p in enumerate(pages):
                if prog.wasCanceled():
                    break
                prog.setValue(i)
                QApplication.processEvents()
                if i:
                    printer.newPage()
                pix = self.doc[p].get_pixmap(dpi=dpi, alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                area = painter.viewport()
                size = img.size().scaled(area.size(), Qt.KeepAspectRatio)
                x = area.x() + (area.width() - size.width()) // 2
                y = area.y() + (area.height() - size.height()) // 2
                painter.setViewport(x, y, size.width(), size.height())
                painter.setWindow(img.rect())
                painter.drawImage(0, 0, img)
                painter.setViewport(area)
                painter.setWindow(area)
        finally:
            painter.end()
            prog.close()

    # ================================================================ misc
    def run_busy(self, text, fn):
        """Run fn in a worker thread with a busy dialog. Returns result or None on error."""
        box = {}

        def target():
            try:
                box["result"] = fn()
            except Exception as e:  # noqa
                box["error"] = e
                box["tb"] = traceback.format_exc()
        prog = QProgressDialog(text, "", 0, 0, self)
        prog.setWindowTitle(APP_NAME)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(300)
        prog.setCancelButton(None)
        th = threading.Thread(target=target, daemon=True)
        th.start()
        while th.is_alive():
            QApplication.processEvents()
            time.sleep(0.02)
        prog.close()
        if "error" in box:
            sys.stderr.write(box.get("tb", ""))
            QMessageBox.critical(self, APP_NAME, f"{text.rstrip('.… ')} failed:\n\n{box['error']}")
            return None
        return box.get("result", True)

    def _done_file(self, path, msg):
        r = QMessageBox.question(self, APP_NAME, f"{msg}\n\n{path}\n\nOpen it now?")
        if r == QMessageBox.Yes:
            if path.lower().endswith(".pdf"):
                self.open_path(path)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _done_folder(self, folder, msg):
        r = QMessageBox.question(self, APP_NAME, f"{msg}\n\nOpen the folder?")
        if r == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def about(self):
        QMessageBox.about(self, APP_NAME,
                          f"<h3>{APP_NAME} {__version__}</h3>"
                          "<p>A desktop PDF editor for Windows: organize pages, comment, sign, edit text, "
                          "fill and create forms, convert, compress, OCR and protect PDFs.</p>"
                          f"<p>PDF engine: PyMuPDF {pymupdf.VersionBind} (MuPDF {pymupdf.VersionFitz}).<br>"
                          "UI: Qt for Python (PySide6).</p>")

    def shortcuts_help(self):
        rows = []
        for a in self.findChildren(QAction):
            if a.shortcut().toString() and a.text():
                rows.append(f"<tr><td><b>{a.shortcut().toString(QKeySequence.NativeText)}</b></td>"
                            f"<td>&nbsp;&nbsp;{a.text().replace('&&', '&#38;').replace('&', '')}</td></tr>")
        rows.append("<tr><td><b>Ctrl + mouse wheel</b></td><td>&nbsp;&nbsp;Zoom</td></tr>")
        rows.append("<tr><td><b>Esc</b></td><td>&nbsp;&nbsp;Cancel the current drag</td></tr>")
        QMessageBox.information(self, "Keyboard shortcuts", "<table>" + "".join(sorted(set(rows))) + "</table>")
