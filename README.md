# PDF Studio

A desktop PDF editor for Windows, with the main Acrobat Pro-style tools: organize pages,
comment and sign, edit text, fill and create forms, convert, compress, OCR, and password protection.
It works fully offline.

## Make the Windows app (.exe)

1. Install **Python 3.11 or newer** from <https://www.python.org/downloads/>.
   During setup, tick **"Add python.exe to PATH"**.
2. Unzip this folder anywhere, e.g. `C:\PDFStudio`.
3. Double-click **`build.bat`**.
   The first run downloads the packages and takes about 5–10 minutes.
4. When it finishes, the app is in **`dist\PDF Studio\PDF Studio.exe`**.
   Pin it to the taskbar, or copy the whole `dist\PDF Studio` folder anywhere.

**Optional installer.** If [Inno Setup 6](https://jrsoftware.org/isdl.php) is installed,
`build.bat` also makes **`dist\PDFStudio-Setup.exe`**. That installer adds Start-menu and
desktop shortcuts, and adds PDF Studio to "Open with" for PDF files.

To run without building: `pip install -r requirements.txt`, then `python main.py`.

## Features

| Area | What you can do |
|---|---|
| **View** | Continuous scroll, zoom (Ctrl + wheel, fit width/page), page thumbnails, bookmarks, find text (Ctrl+F, F3), print, drag & drop files |
| **Organize pages** | Drag thumbnails to reorder, rotate, delete, duplicate, insert blank pages, insert pages from other PDFs/images, extract, split (every N pages / ranges / max size), combine files, crop, reverse order |
| **Comment** | Highlight, underline, strikethrough, sticky notes, text-box comments, freehand drawing, arrows, lines, rectangles, ovals, standard stamps (Approved, Draft, Confidential…), custom dated stamps, move and delete comments |
| **Fill & sign** | Draw, type or import your signature (saved for reuse), then place it on any page |
| **Edit PDF** | Add text (font, size, colour), edit existing text in place, find & replace across the document, add images, watermark, page numbers, redact (permanently black out content), flatten |
| **Forms** | Click fields to fill them, or fill all fields in a table. Create text fields, checkboxes and dropdowns |
| **Convert** | PDF to Word, Excel (detects tables), images (PNG/JPG at any DPI) or text. Images to PDF. Word, Excel or PowerPoint to PDF |
| **Compress** | Three levels, plus an optional grayscale setting; shows the size before and after |
| **OCR** | Makes scanned PDFs searchable and selectable (needs Tesseract, see below) |
| **Protect** | AES-256 password with print/copy/edit permissions; opens protected PDFs; edit document properties |

Every edit can be undone (Ctrl+Z, up to 30 steps). Nothing changes on disk until you **Save**.

## Optional add-ons

- **OCR (text recognition):** install the free Tesseract engine from
  <https://github.com/UB-Mannheim/tesseract/wiki> with the default options.
  To recognise Arabic too, tick *Arabic* under "Additional language data" during setup,
  then enter `eng+ara` as the language in the OCR dialog.
- **Word, Excel or PowerPoint to PDF:** this uses Microsoft Office if it is installed.
  If not, it uses LibreOffice.

## Tips

- Press **F1** for the list of keyboard shortcuts.
- With the **Edit text**, **Highlight** or **Select text** tool, a single click picks the whole line.
- **Edit text** replaces the text with a standard font (Helvetica, Times or Courier) at the same
  size and colour. For heavily styled text, check the result and adjust it with the dialog.
- Scanned pages are images. To edit or search them, run **Convert ▸ Recognize text (OCR)** first.
- Opening a password-protected PDF and saving it saves it **without** the password.
  Use **Protect ▸ Protect with password** to save a protected copy.

## Project layout

```
main.py               start-up
pdfstudio/ops.py      all PDF operations (no UI – can be scripted)
pdfstudio/viewer.py   page view and mouse tools
pdfstudio/window.py   main window, menus, panels
pdfstudio/dialogs.py  dialogs (signature pad, forms, split, merge…)
build.bat             builds the .exe with PyInstaller
installer.iss         Inno Setup installer script
```

## Licence note

The PDF engine is PyMuPDF, which is licensed under the AGPL. Personal and internal use is fine.
If you distribute the app to others or sell it, you must publish your source code under the AGPL,
or buy a commercial licence from Artifex.
