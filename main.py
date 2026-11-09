"""PDF Studio – start the application.

Usage:  python main.py [file.pdf ...]
"""
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from pdfstudio.window import MainWindow, APP_NAME


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def selftest(log_path):
    """Exercise the main features without a window (used by the automated build)."""
    import tempfile
    import traceback
    import pymupdf
    from pdfstudio import ops
    lines = []
    ok = True
    d = tempfile.mkdtemp()
    src = os.path.join(d, "t.pdf")
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page()
        p.insert_text((72, 72), f"Self test page {i + 1} total 1,250.00", fontsize=14)
        for r in range(3):
            for c in range(3):
                rc = pymupdf.Rect(72 + c * 100, 120 + r * 20, 172 + c * 100, 140 + r * 20)
                p.draw_rect(rc, color=(0, 0, 0))
                p.insert_text((rc.x0 + 4, rc.y1 - 6), f"{r}{c}", fontsize=9)
    doc.save(src)
    tests = [
        ("open", lambda: ops.open_any(src)),
        ("annotate", lambda: ops.text_markup(ops.open_any(src)[0], (60, 55, 400, 80))),
        ("edit text", lambda: ops.replace_text_in_rect(ops.open_any(src)[0], (60, 55, 400, 80), "Edited")),
        ("excel", lambda: ops.to_excel(ops.open_any(src), os.path.join(d, "t.xlsx"))),
        ("word", lambda: ops.to_word(src, os.path.join(d, "t.docx"))),
        ("images", lambda: ops.export_images(ops.open_any(src), d, "t", "png", 72)),
        ("compress", lambda: ops.compress_to_bytes(ops.open_any(src))),
        ("encrypt", lambda: ops.encrypt_bytes(ops.open_any(src), "pw")),
        ("split", lambda: ops.split_document(ops.open_any(src), os.path.join(d, "s"), "t", "every", "1")),
    ]
    for name, fn in tests:
        try:
            fn()
            lines.append(f"PASS {name}")
        except Exception:
            ok = False
            lines.append(f"FAIL {name}\n{traceback.format_exc()}")
    lines.append("SELFTEST " + ("OK" if ok else "FAILED"))
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return 0 if ok else 1


def main():
    import multiprocessing
    multiprocessing.freeze_support()  # needed for the packaged .exe on Windows
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2]))
    if sys.platform == "win32":
        try:  # own taskbar icon/group instead of python.exe
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFStudio.PDFStudio.1")
        except Exception:
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("PDFStudio")
    app.setStyle("Fusion")
    ico = resource("pdfstudio.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    first = MainWindow(files[0] if files else None)
    first.show()
    for f in files[1:]:
        w = MainWindow(f)
        w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
