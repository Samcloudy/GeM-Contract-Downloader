from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import tempfile
import threading
import time
import pandas as pd
from werkzeug.utils import secure_filename
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from actions import (
    enter_captcha,
    select_category,
    select_date_range,
    solve_download_captcha_and_save,
    select_contract_number,
)
from config import DOWNLOAD_DIR as _CFG_DOWNLOAD_DIR, report

# Always use an absolute path - relative paths are a common reason Chrome ignores the folder.
DOWNLOAD_DIR = os.path.abspath(_CFG_DOWNLOAD_DIR)
SEARCH_URL = "https://gem.gov.in/view_contracts"

app = Flask(__name__)
UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "gem_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

driver_creation_lock = threading.Lock()
state = {
    "running": False,
    "log": [],
    "downloaded": 0,
    "total": 0,
    "done": False,
    "error": None,
}

def log(msg):
    print(msg)
    state["log"].append(msg)

# ══════════════════════════ CHROME SETUP ══════════════════════════
def build_chrome_options(headless=False, download_dir=None):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
    else:
        opts.add_argument("--start-maximized")
        opts.add_argument(f"--user-data-dir={tempfile.mkdtemp()}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--remote-allow-origins=*")
    opts.add_argument("--disable-features=DownloadBubble,DownloadBubbleV2,"
                      "InsecureDownloadWarnings,SafeBrowsingEnhancedProtection")
    opts.add_argument("--safebrowsing-disable-download-protection")
    opts.add_argument("--allow-running-insecure-content")
    if download_dir:
        download_dir = os.path.abspath(download_dir)
        os.makedirs(download_dir, exist_ok=True)
        prefs = {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "download.extensions_to_open": "",
            "profile.default_content_setting_values.automatic_downloads": 1,
            "profile.default_content_settings.popups": 0,
            "safebrowsing.enabled": False,
            "safebrowsing.disable_download_protection": True,
            "plugins.always_open_pdf_externally": True,
        }
        opts.add_experimental_option("prefs", prefs)
    return opts

def make_driver(headless=False, download_dir=None):
    opts = build_chrome_options(headless=headless, download_dir=download_dir)
    with driver_creation_lock:
        driver = webdriver.Chrome(options=opts)
        # Force the download folder at browser level (works even when prefs are ignored)
        if download_dir:
            download_dir = os.path.abspath(download_dir)
            os.makedirs(download_dir, exist_ok=True)
            try:
                driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
                    "behavior": "allow",
                    "downloadPath": download_dir,
                    "eventsEnabled": True,
                })
            except Exception:
                try:
                    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                        "behavior": "allow",
                        "downloadPath": download_dir,
                    })
                except Exception as e:
                    print(f"[WARN] Could not set download behavior via CDP: {e}")
    return driver

# ══════════════════════════ HELPERS ═══════════════════════════════
def dismiss_alert(driver):
    try:
        driver.switch_to.alert.accept()
        return True
    except Exception:
        return False

def open_search_page(driver):
    driver.get(SEARCH_URL)
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.ID, "buyer_category"))
    )
    time.sleep(2)
    dismiss_alert(driver)

def scroll_load_all(driver):
    """Scroll until the number of result cards stops growing."""
    last_count, stable = 0, 0
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        count = len(driver.find_elements(By.CLASS_NAME, "border.block"))
        log(f"   {count} documents loaded")
        if count == last_count:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        last_count = count
    return count

def pick_link(doc):
    """Pick the contract link inside a result card (not just the first <a>)."""
    try:
        caps = doc.find_elements(By.XPATH, ".//a[contains(@onclick, 'openCap')]")
        if caps:
            return caps[0]
    except Exception:
        pass
    try:
        bids = doc.find_elements(By.XPATH, ".//a[.//span[contains(@class,'ajxtag_order_number')] or contains(@class,'bid_no') or .//p[contains(@class,'bid_no')]]")
        if bids:
            return bids[0]
    except Exception:
        pass
    anchors = doc.find_elements(By.TAG_NAME, "a")
    for a in anchors:
        href = (a.get_attribute("href") or "").strip().lower()
        onclick = (a.get_attribute("onclick") or "").strip().lower()
        if any(k in href or k in onclick for k in ("contract", "bid", "showbid", "order", "opencap")):
            return a
    return anchors[0] if anchors else None

def open_contract(driver, link):
    """Click the contract link. If it opens in a new tab, switch to it. Returns main window handle."""
    main = driver.current_window_handle
    before = set(driver.window_handles)
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'})", link)
        time.sleep(0.5)
        link.click()
    except Exception:
        driver.execute_script("arguments[0].click();", link)
    time.sleep(3)
    new = set(driver.window_handles) - before
    if new:
        driver.switch_to.window(new.pop())
        log("   Contract opened in a new tab - switched to it")
    return main

def close_extra_tabs(driver, main):
    """Close every tab except `main` and return to it."""
    try:
        for h in list(driver.window_handles):
            if h != main:
                driver.switch_to.window(h)
                driver.close()
    except Exception:
        pass
    try:
        driver.switch_to.window(main)
    except Exception:
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[0])

def retryable(reason):
    r = reason.lower()
    return "captcha" in r or "redirect" in r or "no_file" in r or "no_modal" in r

# ════════════════════════ MODE 1: CATEGORY ═════════════════════════
def apply_category_filters(driver, category, from_date, to_date):
    open_search_page(driver)
    select_category(driver=driver, category=category)
    select_date_range(driver=driver, from_date=from_date, to_date=to_date)
    ok = enter_captcha(driver=driver, captcha_code="captcha_code1", img_id="captchaimg1")
    if not ok:
        return False
    time.sleep(3)
    scroll_load_all(driver)
    return True

def run_scraper_category(category, from_date, to_date):
    state.update({"running": True, "log": [], "downloaded": 0,
                  "total": 0, "done": False, "error": None})
    log(f"Download folder: {DOWNLOAD_DIR}")
    try:
        driver = make_driver(headless=False, download_dir=DOWNLOAD_DIR)
    except Exception as e:
        log(f"ChromeDriver failed: {e}")
        state["error"] = f"ChromeDriver failed: {e}"
        state["running"] = False
        return
    try:
        log("Opening GeM portal...")
        log(f"Selecting category: {category}")
        log(f"Setting date range: {from_date} to {to_date}")
        log("Solving SEARCH captcha and loading document list...")
        if not apply_category_filters(driver, category, from_date, to_date):
            state["error"] = "Could not get past the first CAPTCHA."
            return
        total = len(driver.find_elements(By.CLASS_NAME, "border.block"))
        state["total"] = total
        log(f"Total documents: {total}")
        saved = 0
        for i in range(1, total + 1):
            log(f"── Document {i}/{total}")
            main = None
            try:
                documents = driver.find_elements(By.CLASS_NAME, "border.block")
                if i - 1 >= len(documents):
                    log("   Document list shorter than expected - stopping")
                    break
                doc = documents[i - 1]
                link = pick_link(doc)
                if link is None:
                    log("   ❌ No link found in card")
                    continue
                success = False
                for retry in range(3):
                    if retry > 0:
                        log(f"   🔄 Retrying (attempt {retry + 1}/3)...")
                        if main:
                            close_extra_tabs(driver, main)
                        if not apply_category_filters(driver, category, from_date, to_date):
                            log("   ❌ Search captcha failed on retry")
                            break
                        documents = driver.find_elements(By.CLASS_NAME, "border.block")
                        if i - 1 >= len(documents):
                            break
                        link = pick_link(documents[i - 1])
                        if link is None:
                            break
                    main = open_contract(driver, link)
                    time.sleep(2)
                    ok, reason = solve_download_captcha_and_save(driver, DOWNLOAD_DIR)
                    if ok:
                        saved += 1
                        state["downloaded"] = saved
                        log(f"   ✅ Saved: {reason}")
                        success = True
                        break
                    log(f"   ❌ Failed: {reason}")
                    if not retryable(reason):
                        break
                if not success:
                    log(f"   ⚠️ Skipped document {i}")
                time.sleep(1)
                if main:
                    close_extra_tabs(driver, main)
                try:
                    driver.execute_script("$('#viewContractModel').modal('hide');")
                except Exception:
                    pass
                # Only restore search results if they were navigated away from or lost
                if i < total:
                    docs_now = driver.find_elements(By.CLASS_NAME, "border.block")
                    if len(docs_now) < total:
                        log("   Search results lost - restoring filters...")
                        if not apply_category_filters(driver, category, from_date, to_date):
                            log("   ❌ Could not restore search results - stopping")
                            break
            except Exception as e:
                log(f"   Error on document {i}: {e}")
                dismiss_alert(driver)
                try:
                    if main:
                        close_extra_tabs(driver, main)
                    if i < total:
                        apply_category_filters(driver, category, from_date, to_date)
                except Exception:
                    pass
                continue
        log(f"Done! {state['downloaded']} files saved to {DOWNLOAD_DIR}")
        state["done"] = True
    except Exception as e:
        state["error"] = str(e)
        log(f"ERROR: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        state["running"] = False

# ══════════════════════ MODE 2: CONTRACT NUMBERS ═══════════════════
def read_contract_numbers(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(path, dtype=str, header=None)
    elif ext == ".csv":
        df = pd.read_csv(path, dtype=str, header=None)
    elif ext == ".txt":
        with open(path, "r", encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    numbers = []
    for val in df.values.flatten():
        if val is None:
            continue
        s = str(val).strip()
        if s and s.upper() != "NAN":
            numbers.append(s)
    return numbers

def search_contract(driver, cno):
    """Fresh search page -> enter contract number -> solve captcha -> return first result card or None."""
    open_search_page(driver)
    select_contract_number(driver, cno)
    log("   Solving SEARCH captcha...")
    if not enter_captcha(driver=driver, captcha_code="captcha_code1", img_id="captchaimg1"):
        log("   ❌ Search captcha failed")
        return "captcha_failed"
    time.sleep(3)
    docs = driver.find_elements(By.CLASS_NAME, "border.block")
    return docs[0] if docs else None

def run_scraper_contracts(contract_numbers):
    state.update({"running": True, "log": [], "downloaded": 0,
                  "total": len(contract_numbers), "done": False, "error": None})
    log(f"Download folder: {DOWNLOAD_DIR}")
    log(f"Loaded {len(contract_numbers)} contract number(s)")
    try:
        driver = make_driver(headless=False, download_dir=DOWNLOAD_DIR)
    except Exception as e:
        log(f"ChromeDriver failed: {e}")
        state["error"] = f"ChromeDriver failed: {e}"
        state["running"] = False
        return
    try:
        saved = 0
        for idx, cno in enumerate(contract_numbers, 1):
            log("")
            log("═══════════════════════════════════════")
            log(f"[{idx}/{len(contract_numbers)}] {cno}")
            log("═══════════════════════════════════════")
            main = None
            try:
                max_retries = 3
                success = False
                for retry in range(max_retries):
                    if retry > 0:
                        log(f"   🔄 Retrying download (Attempt {retry + 1}/{max_retries})...")
                        if main:
                            close_extra_tabs(driver, main)
                    result = search_contract(driver, cno)
                    if result == "captcha_failed":
                        continue
                    if result is None:
                        log(f"   ⚠️ No result found for {cno}")
                        break
                    log("   Result found. Opening contract...")
                    link = pick_link(result)
                    if link is None:
                        log("   ❌ No link in result card")
                        break
                    main = open_contract(driver, link)
                    time.sleep(2)
                    ok, reason = solve_download_captcha_and_save(driver, DOWNLOAD_DIR)
                    if ok:
                        saved += 1
                        state["downloaded"] = saved
                        log(f"   ✅ {reason}")
                        success = True
                        break
                    log(f"   ❌ Attempt {retry + 1} failed: {reason}")
                    if not retryable(reason):
                        break
                if not success:
                    log(f"   ⚠️ Skipped {cno}")
                if main:
                    close_extra_tabs(driver, main)
                try:
                    driver.execute_script("$('#viewContractModel').modal('hide');")
                except Exception:
                    pass
                time.sleep(1)
            except Exception as e:
                log(f"   ❌ Error: {e}")
                dismiss_alert(driver)
                try:
                    if main:
                        close_extra_tabs(driver, main)
                except Exception:
                    pass
                continue
        log("")
        log("═══════════════════════════════════════")
        log(f"Done! {state['downloaded']} / {len(contract_numbers)} downloaded")
        log(f"Folder: {DOWNLOAD_DIR}")
        log("═══════════════════════════════════════")
        state["done"] = True
    except Exception as e:
        state["error"] = str(e)
        log(f"ERROR: {e}")
    finally:
        time.sleep(2)
        try:
            driver.quit()
        except Exception:
            pass
        state["running"] = False

# ══════════════════════════ ROUTES ═════════════════════════════════
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/categories")
def categories():
    try:
        driver = make_driver(headless=True)
    except Exception as e:
        return jsonify({"error": f"ChromeDriver failed: {e}"}), 500
    try:
        driver.get(SEARCH_URL)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "buyer_category"))
        )
        time.sleep(2)
        dropdown = Select(driver.find_element(By.ID, "buyer_category"))
        cats = [o.text.strip() for o in dropdown.options
                if o.text.strip() and not o.text.strip().startswith("--")]
        return jsonify(cats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            driver.quit()
        except Exception:
            pass

@app.route("/start_category", methods=["POST"])
def start_category():
    if state["running"]:
        return jsonify({"error": "Already running"}), 400
    data = request.json
    category = data.get("category", "").strip()
    from_date = data.get("from_date", "").strip()
    to_date = data.get("to_date", "").strip()
    if not category or not from_date or not to_date:
        return jsonify({"error": "Missing fields"}), 400
    threading.Thread(target=run_scraper_category,
                     args=(category, from_date, to_date), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/upload", methods=["POST"])
def upload():
    if state["running"]:
        return jsonify({"error": "Already running"}), 400
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    fname = secure_filename(f.filename)
    save_path = os.path.join(UPLOAD_DIR, fname)
    f.save(save_path)
    try:
        numbers = read_contract_numbers(save_path)
    except Exception as e:
        return jsonify({"error": f"Could not read file: {e}"}), 400
    if not numbers:
        return jsonify({"error": "No contract numbers found in file"}), 400
    threading.Thread(target=run_scraper_contracts, args=(numbers,), daemon=True).start()
    return jsonify({"ok": True, "count": len(numbers)})

@app.route("/status")
def status():
    return jsonify(state)

@app.route("/files")
def files():
    try:
        items = []
        for f in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(path):
                items.append({"name": f, "size": os.path.getsize(path)})
        return jsonify(items)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    report()
    print(f"\n[INFO] Download folder: {DOWNLOAD_DIR}")
    print("\nGeM Contract Downloader running at: http://127.0.0.1:5000\n")
    app.run(host="127.0.0.1", port=5000, debug=False)