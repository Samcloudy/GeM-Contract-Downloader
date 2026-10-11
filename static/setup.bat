@echo off
echo ============================================================
echo   GeM Contract Downloader - Setup
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python not found. Install from https://python.org
    echo     Check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo [OK] Python detected

echo.
echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo [X] Dependency install failed.
    pause
    exit /b 1
)
echo [OK] Dependencies installed

echo.
where tesseract >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
        echo [OK] Tesseract found
    ) else (
        echo [!] Tesseract OCR not found.
        echo     Download: https://github.com/UB-Mannheim/tesseract/wiki
    )
) else (
    echo [OK] Tesseract is on PATH
)

echo.
where chrome >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
        echo [OK] Chrome found
    ) else (
        echo [!] Chrome not detected. Install from https://google.com/chrome
    )
) else (
    echo [OK] Chrome is on PATH
)

echo.
echo ============================================================
echo   Setup complete!
echo   Run:   python app.py
echo   Open:  http://127.0.0.1:5000
echo ============================================================
pause