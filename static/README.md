# GeM Contract Downloader

A local web app that bulk-downloads contract PDFs from the Government
e-Marketplace (GeM) portal — either by category + date range, or by
uploading an Excel file of contract numbers.

## Two modes

1. **By Category** — pick a category (e.g. "Labels Stickers") and a date range.
2. **By Contract Number** — upload an `.xlsx` / `.csv` / `.txt` file with one
   contract number per row, e.g. `GEM-511687702723426-29072026`.

In both modes the app:
- Opens Chrome automatically
- Solves the GeM CAPTCHA using Tesseract OCR
- Downloads matching PDFs to `C:\Users\<you>\Downloads\GeM-PDFs\`
- Shows live progress and a clickable file list in the browser

## Requirements

- Windows 10 / 11
- Python 3.10 – 3.12
- Google Chrome
- Tesseract OCR 5.x (with English language data)

## Quick start

```bat
cd "C:\Users\defaultuser0\Downloads\GeM Contract Downloader"
setup.bat
python app.py