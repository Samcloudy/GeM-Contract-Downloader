from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    UnexpectedAlertPresentException, NoAlertPresentException,
    ElementNotInteractableException, StaleElementReferenceException,
)
import base64
import re
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract
import time
import os
import requests
from config import TESSERACT_PATH

if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

CAPTCHA_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captcha")
os.makedirs(CAPTCHA_FOLDER, exist_ok=True)

UP = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
LOW = "abcdefghijklmnopqrstuvwxyz"
WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# Compatibility fix for Pillow 10.0+ (Image.LANCZOS was deprecated)
RESAMPLING_LANCZOS = getattr(Image, 'Resampling', Image).LANCZOS

def _lower(expr):
    return "translate(" + expr + ",'" + UP + "','" + LOW + "')"

# ─────────────────────── CATEGORY MODE ─────────────────────────
def select_category(driver, category):
    element = Select(driver.find_element(By.ID, "buyer_category"))
    for opt in element.options:
        if opt.text.strip() == category.strip():
            element.select_by_visible_text(opt.text)
            print("Selected Category (exact): " + opt.text)
            return
    normalized = category.replace("  ", " ").lower()
    for opt in element.options:
        if opt.text.replace("  ", " ").lower() == normalized:
            element.select_by_visible_text(opt.text)
            print("Selected Category (normalized): " + opt.text)
            return
    for opt in element.options:
        if normalized in opt.text.replace("  ", " ").lower():
            element.select_by_visible_text(opt.text)
            print("Selected Category (substring): " + opt.text)
            return
    raise ValueError("Category not found: " + category)

def select_date_range(driver, from_date, to_date):
    from_el = driver.find_element(By.ID, "from_date_contract_search1")
    to_el = driver.find_element(By.ID, "to_date_contract_search1")
    driver.execute_script("arguments[0].removeAttribute('readonly')", from_el)
    driver.execute_script("arguments[0].removeAttribute('readonly')", to_el)
    from_el.clear()
    from_el.send_keys(from_date)
    to_el.clear()
    to_el.send_keys(to_date)
    print("Selected Date Range: " + from_date + " to " + to_date)

# ─────────────────────── CONTRACT-NUMBER MODE ────────────────────
def select_contract_number(driver, contract_no):
    WebDriverWait(driver, 20).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    time.sleep(1)
    field = None
    xpaths = [
        "//input[@placeholder='Search by Contract / BID / RA Number']",
        "//input[contains(@placeholder,'Contract / BID')]",
        "//input[contains(@placeholder,'Contract')]",
        "//input[contains(" + _lower("@placeholder") + ",'contract')]",
        "//label[contains(" + _lower(".") + ",'search by contract')]/following::input[1]",
        "//*[contains(" + _lower("normalize-space(.)") + ",'search by contract')]/following::input[1]",
    ]
    for xp in xpaths:
        try:
            f = driver.find_element(By.XPATH, xp)
            if f.is_displayed() and f.is_enabled():
                field = f
                print("   Found input via xpath: " + xp[:60])
                break
        except Exception:
            continue
    if field is None:
        for attr, val in [
            ("id", "contract_search"), ("id", "contract_no"),
            ("id", "contract_number"), ("id", "txtcontract"),
            ("name", "contract_no"), ("name", "contract_number"),
        ]:
            try:
                f = driver.find_element(By.XPATH, "//input[@" + attr + "='" + val + "']")
                if f.is_displayed():
                    field = f
                    print("   Found input via " + attr + "=" + val)
                    break
            except Exception:
                continue
    if field is None:
        raise RuntimeError("Could not find the contract-number input.")
    driver.execute_script("arguments[0].removeAttribute('readonly')", field)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'})", field)
    time.sleep(0.3)
    field.clear()
    field.send_keys(contract_no)
    print("Entered contract number: " + contract_no)
    time.sleep(0.7)
    search_btn = None
    for xp in [
        "//button[normalize-space()='Search']",
        "//input[@value='Search']",
        "//button[contains(" + _lower(".") + ",'search')]",
        "//input[contains(" + _lower("@value") + ",'search')]",
        "//input[@type='submit']",
        "//button[@type='submit']",
    ]:
        try:
            b = driver.find_element(By.XPATH, xp)
            if b.is_displayed() and b.is_enabled():
                search_btn = b
                print("   Found Search button via: " + xp[:60])
                break
        except Exception:
            search_btn = None
    if search_btn is None:
        print("   Warning: Search button not found - pressing Enter")
        field.send_keys(Keys.RETURN)
    else:
        driver.execute_script("arguments[0].click();", search_btn)
    time.sleep(2)

# ───────────────────────── SEARCH CAPTCHA HELPERS ───────────────────────
def extract_captcha(driver, img_id):
    try:
        captcha_img = driver.find_element(By.ID, img_id)
        img_src = captcha_img.get_attribute("src")
        match = re.search(r"base64,(.*)", img_src)
        if match:
            image_data = base64.b64decode(match.group(1))
            with open(os.path.join(CAPTCHA_FOLDER, "captcha.jpg"), "wb") as f:
                f.write(image_data)
        else:
            print("Failed to read Captcha! Refreshing and Retrying.")
    except Exception as e:
        print("Error extracting Captcha: " + str(e))

def read_captcha():
    captcha_file = os.path.join(CAPTCHA_FOLDER, "captcha.jpg")
    try:
        img = Image.open(captcha_file)
        w, h = img.size
        img = img.resize((w * 3, h * 3), RESAMPLING_LANCZOS)
        img = img.convert("L")
        img = img.point(lambda x: 0 if x < 140 else 255, "1")
        cfg = "--psm 8 -c tessedit_char_whitelist=" + WHITELIST
        text = pytesseract.image_to_string(img, config=cfg)
        return text.replace("  ", " ").strip()
    except Exception as e:
        print("Error reading Captcha: " + str(e))
        return ""

def refresh_captcha(driver, img_id):
    try:
        print("Refreshing Captcha...")
        btn = driver.find_element(By.XPATH, "//a[contains(@onclick, 'loadCap1')]")
        btn.click()
        time.sleep(2)
        extract_captcha(driver, img_id)
    except Exception as e:
        print("Error refreshing Captcha: " + str(e))

def enter_captcha(driver, captcha_code, img_id):
    """Solve the SEARCH-page CAPTCHA. Returns True/False."""
    attempts = 0
    max_attempts = 15
    while attempts < max_attempts:
        attempts += 1
        extract_captcha(driver, img_id)
        captcha = read_captcha()
        if not captcha:
            refresh_captcha(driver, img_id)
            continue
        cap_input = driver.find_element(By.ID, captcha_code)
        cap_input.clear()
        cap_input.send_keys(captcha)
        search_button = driver.find_element(By.ID, "searchlocation1")
        driver.execute_script("arguments[0].click();", search_button)
        time.sleep(2)
        try:
            error = driver.find_element(By.ID, "pcaptcha_code1").text.strip()
            if error:
                print("   Search captcha attempt " + str(attempts) + " failed, retrying...")
                refresh_captcha(driver, img_id)
            else:
                print("   Search captcha accepted!")
                return True
        except Exception:
            print("   Search captcha accepted!")
            return True
    print("   Search captcha failed after " + str(max_attempts) + " attempts.")
    return False

# ───────────────────── DOWNLOAD-PAGE HELPERS ───────────
def _snapshot_download_folder(folder):
    """Return {filename: mtime} of completed files (ignores temporary Chrome files)."""
    snap = {}
    try:
        for f in os.listdir(folder):
            if f.lower().endswith((".crdownload", ".tmp", ".part")):
                continue
            try:
                snap[f] = os.path.getmtime(os.path.join(folder, f))
            except OSError:
                pass
    except Exception:
        pass
    return snap

def _in_progress(folder):
    try:
        return any(f.lower().endswith((".crdownload", ".tmp", ".part"))
                   for f in os.listdir(folder))
    except Exception:
        return False

def wait_for_new_file(folder, before, timeout=90):
    """Wait until a new (or re-written) completed file shows up. Returns filename or None."""
    end = time.time() + timeout
    while time.time() < end:
        if _in_progress(folder):
            time.sleep(1)
            continue
        now = _snapshot_download_folder(folder)
        for name, mtime in now.items():
            if name not in before or mtime > before[name] + 0.5:
                time.sleep(1)  # let the OS finish flushing
                return name
        time.sleep(1)
    return None

def _modal_visible(driver):
    for xp in [
        "//div[contains(@class,'modal') and contains(@style,'display: block')]",
        "//div[contains(@class,'modal-dialog')]",
        "//div[contains(@id,'modal') and not(contains(@style,'display: none'))]",
    ]:
        try:
            for el in driver.find_elements(By.XPATH, xp):
                if el.is_displayed():
                    return True
        except Exception:
            continue
    return False

def _download_link_element(driver):
    """Return the actual clickable <a> Download element or None."""
    try:
        for el in driver.find_elements(By.CSS_SELECTOR, "#dwnbtn a, a#dwnbtn"):
            if el.is_displayed() and el.get_attribute("href"):
                return el
    except Exception:
        pass
    return None

def _download_link_href(driver):
    """Return the href of the download link if generated, else None."""
    try:
        href = driver.execute_script(
            "var a = document.querySelector('#dwnbtn a, a#dwnbtn'); "
            "return (a && a.href && a.href.indexOf('http') === 0) ? a.href : null;"
        )
        if href:
            return href
    except Exception:
        pass
    el = _download_link_element(driver)
    if el:
        return el.get_attribute("href")
    return None

def _download_button(driver):
    return _download_link_element(driver)

def _download_button_ready(driver):
    return _download_link_href(driver) is not None

def _captcha_input(driver):
    for cid in ("captcha_code", "captcha_code1"):
        try:
            for c in driver.find_elements(By.ID, cid):
                if c.is_displayed():
                    return c
        except Exception:
            pass
    # fallback: any visible text input inside a modal that looks like captcha
    try:
        for c in driver.find_elements(
            By.XPATH, "//div[contains(@class,'modal')]//input[@type='text']"):
            if c.is_displayed():
                return c
    except Exception:
        pass
    return None

def _captcha_input_visible(driver):
    return _captcha_input(driver) is not None

def _on_home_page(driver, start_url):
    """True if the portal threw us back to the home / search page."""
    try:
        url = driver.current_url.rstrip("/")
    except Exception:
        return False
    if url == start_url.rstrip("/"):
        return False
    low = url.lower()
    return low.endswith("gem.gov.in") or "view_contracts" in low or low.endswith("/home")

def _handle_alert(driver):
    """Accept an alert if present. Returns its text or None."""
    try:
        alert = driver.switch_to.alert
        txt = alert.text
        alert.accept()
        return txt
    except NoAlertPresentException:
        return None
    except Exception:
        return None

def _find_captcha_image(driver):
    for img_id in ("captchaimg", "captchaimg2", "captchaimg1"):
        try:
            el = driver.find_element(By.ID, img_id)
            if el.is_displayed():
                return el
        except Exception:
            continue
    try:
        for el in driver.find_elements(By.TAG_NAME, "img"):
            src = el.get_attribute("src") or ""
            if "base64," in src and el.is_displayed():
                return el
    except Exception:
        pass
    return None

def _ocr_download_captcha(path):
    """OCR with a few preprocessing variants; returns the best candidate string."""
    try:
        img = Image.open(path).convert("L")
    except Exception:
        return ""
    w, h = img.size
    img = img.resize((w * 3, h * 3), RESAMPLING_LANCZOS)
    img = ImageOps.autocontrast(img)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = img.filter(ImageFilter.MedianFilter(3))
    img = img.filter(ImageFilter.SHARPEN)
    results = []
    for thr in (120, 140, 160):
        bw = img.point(lambda x, t=thr: 0 if x < t else 255, "1")
        for psm in (8, 7):
            cfg = "--psm " + str(psm) + " -c tessedit_char_whitelist=" + WHITELIST
            try:
                txt = pytesseract.image_to_string(bw, config=cfg)
            except Exception:
                continue
            txt = re.sub(r"[^A-Za-z0-9]", "", txt)
            if txt:
                results.append(txt)
    if not results:
        return ""
    # most frequent result wins; ties -> longest
    best = max(set(results), key=lambda r: (results.count(r), len(r)))
    return best

def _describe(el):
    try:
        return ("<" + el.tag_name + " id='" + str(el.get_attribute("id")) +
                "' class='" + str(el.get_attribute("class"))[:40] +
                "' text='" + (el.text or el.get_attribute("value") or "").strip()[:30] + "'>")
    except Exception:
        return "<element>"

def _click_modal_submit(driver, cap_input):
    """Click the submit/download button of the captcha modal and log which one was used."""
    M = "//div[contains(@class,'modal')]"
    candidates = [
        M + "//button[contains(" + _lower(".") + ",'download')]",
        M + "//input[contains(" + _lower("@value") + ",'download')]",
        M + "//a[contains(" + _lower(".") + ",'download')]",
        M + "//button[contains(" + _lower(".") + ",'submit')]",
        M + "//input[@type='submit']",
        M + "//button[@type='submit']",
        M + "//button[contains(" + _lower(".") + ",'verify')]",
        M + "//button[contains(" + _lower(".") + ",'proceed')]",
        M + "//button[contains(" + _lower(".") + ",'ok')]",
        # last resort: any visible button that is not close / cancel
        M + "//button[not(contains(" + _lower(".") + ",'close')) and "
            "not(contains(" + _lower(".") + ",'cancel')) and "
            "not(contains(@class,'close')) and normalize-space(.)!='\u00d7']",
    ]
    for xp in candidates:
        try:
            for b in driver.find_elements(By.XPATH, xp):
                if b.is_displayed() and b.is_enabled():
                    print("   Clicking: " + _describe(b))
                    driver.execute_script("arguments[0].click();", b)
                    return True
        except Exception:
            continue
    print("   No modal button found - pressing Enter in captcha box")
    try:
        cap_input.send_keys(Keys.RETURN)
        return True
    except Exception:
        return False

def _modal_text(driver):
    """Visible text of the open modal (shows messages like 'Invalid captcha')."""
    try:
        t = driver.execute_script(
            "var m=document.querySelector('.modal.show, .modal[style*=\"display: block\"]'); "
            "return m ? m.innerText : '';")
        return " | ".join(x.strip() for x in (t or "").splitlines() if x.strip())[:300]
    except Exception:
        return ""

def _refresh_download_captcha(driver):
    for xp in [
        "//div[contains(@class,'modal')]//a[contains(@onclick,'loadCap')]",
        "//a[contains(@onclick,'loadCap')]",
        "//div[contains(@class,'modal')]//*[contains(@class,'refresh') or contains(@class,'reload')]",
    ]:
        try:
            for el in driver.find_elements(By.XPATH, xp):
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    time.sleep(1.5)
                    return
        except Exception:
            continue

def _check_new_file(folder, before):
    """Return the name of a new/re-written completed file, or None."""
    if _in_progress(folder):
        return None
    now = _snapshot_download_folder(folder)
    for name, mtime in now.items():
        if name not in before or mtime > before[name] + 0.5:
            return name
    return None

def _dump_debug(driver, tag):
    """Save a screenshot + page HTML so we can see what the portal is showing."""
    try:
        driver.save_screenshot(os.path.join(CAPTCHA_FOLDER, "debug_" + tag + ".png"))
        with open(os.path.join(CAPTCHA_FOLDER, "debug_" + tag + ".html"),
                  "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print("   Debug saved: captcha/debug_" + tag + ".png / .html")
    except Exception as e:
        print("   Debug dump failed: " + str(e))

def _modal_summary(driver):
    """Print the visible buttons/inputs so we know what the page really contains."""
    try:
        for b in driver.find_elements(By.XPATH, "//button | //input[@type='button' or @type='submit'] | //a[contains(@class,'btn')]"):
            if b.is_displayed():
                label = (b.text or b.get_attribute("value") or "").strip()
                print("   [button] id=" + str(b.get_attribute("id")) + " text='" + label[:40] + "'")
        for i in driver.find_elements(By.XPATH, "//input[@type='text' or not(@type)]"):
            if i.is_displayed():
                print("   [input ] id=" + str(i.get_attribute("id")) + " name=" + str(i.get_attribute("name")))
    except Exception:
        pass

# ───────────────────── DOWNLOAD-PAGE CAPTCHA + DOWNLOAD ───────────
def _get_modal_captcha_code(driver):
    """
    GeM loads the modal captcha text into hidden field #h_captcha via loadCap().
    We check #h_captcha first; if missing/empty, we OCR the captcha image.
    """
    try:
        val = driver.execute_script("return ($('#h_captcha').length ? $('#h_captcha').val() : '');")
        if val and val.strip():
            print("   Modal captcha from DOM: " + val.strip())
            return val.strip()
    except Exception:
        pass

    img_el = _find_captcha_image(driver)
    if img_el is not None:
        path = os.path.join(CAPTCHA_FOLDER, "download_captcha.jpg")
        try:
            src = img_el.get_attribute("src") or ""
            m = re.search(r"base64,(.*)", src)
            if m:
                with open(path, "wb") as f:
                    f.write(base64.b64decode(m.group(1)))
                code = _ocr_download_captcha(path)
                print("   Modal captcha from OCR: " + code)
                return code
        except Exception as e:
            print("   Modal captcha image extract error: " + str(e))
    return ""

def _submit_modal_captcha(driver, code):
    """Enter captcha code into #captcha_code and click Submit (#modelsbt)."""
    try:
        driver.execute_script(
            "$('#captcha_code').val(arguments[0]);"
            "$('#pcaptcha_code').html('');", code
        )
    except Exception:
        pass
    cap_input = _captcha_input(driver)
    if cap_input:
        try:
            cap_input.clear()
            cap_input.send_keys(code)
        except Exception:
            pass

    # Click Submit button (#modelsbt)
    try:
        sbt = driver.find_elements(By.ID, "modelsbt")
        if sbt and sbt[0].is_displayed():
            driver.execute_script("arguments[0].click();", sbt[0])
            print("   Clicked modal Submit button (#modelsbt)")
            return True
    except Exception:
        pass

    if cap_input:
        return _click_modal_submit(driver, cap_input)
    return False

def _download_contract_file(driver, href, download_dir, before, timeout=20):
    """
    Download contract file into download_dir:
    1) Click the Download link (with target='_blank' removed so browser downloads directly).
    2) If browser download does not complete within 6s, fallback to requests with session cookies.
    """
    # 1. Browser click
    try:
        driver.execute_script("""
            var a = document.querySelector('#dwnbtn a, a#dwnbtn');
            if (a) {
                a.removeAttribute('target');
                a.click();
            }
        """)
        print("   Triggered browser click on Download link")
    except Exception as e:
        print("   Browser click failed: " + str(e))

    # Wait up to 6 seconds for browser to save file
    end_time = time.time() + 6
    while time.time() < end_time:
        time.sleep(0.5)
        fname = _check_new_file(download_dir, before)
        if fname:
            time.sleep(1)
            return (True, fname)

    # 2. Direct download fallback using session cookies
    print("   Browser download pending; initiating direct download with session cookies...")
    try:
        cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        user_agent = driver.execute_script("return navigator.userAgent;")
        headers = {
            "User-Agent": user_agent,
            "Referer": driver.current_url or "https://gem.gov.in/view_contracts"
        }
        resp = requests.get(href, cookies=cookies, headers=headers, timeout=30)
        if resp.status_code == 200 and resp.content:
            cd = resp.headers.get("Content-Disposition", "")
            fname_match = re.search(r'filename=["\']?([^"\';]+)', cd)
            if fname_match:
                filename = fname_match.group(1).strip()
            else:
                oid = driver.execute_script("return ($('#oid').length ? $('#oid').val() : '');") or "contract"
                filename = f"{oid}.pdf"
            filename = os.path.basename(filename)
            dest_path = os.path.join(download_dir, filename)
            with open(dest_path, "wb") as f:
                f.write(resp.content)
            print(f"   Saved via direct download: {filename} ({len(resp.content)} bytes)")
            return (True, filename)
    except Exception as e:
        print("   Direct download fallback error: " + str(e))

    # 3. Final wait for browser download if requests was not applicable
    end_time = time.time() + (timeout - 6)
    while time.time() < end_time:
        time.sleep(0.5)
        fname = _check_new_file(download_dir, before)
        if fname:
            time.sleep(1)
            return (True, fname)

    return (False, "download_timeout")

def solve_download_captcha_and_save(driver, download_dir):
    """
    Download flow for ONE contract modal.
    1. Wait for modal to be open.
    2. Get captcha from DOM (#h_captcha) or OCR.
    3. Type into #captcha_code and click Submit (#modelsbt).
    4. Wait for download link (<a ...>Download</a>) inside #dwnbtn.
    5. Download PDF to download_dir.
    6. Close modal cleanly.
    """
    download_dir = os.path.abspath(download_dir)
    os.makedirs(download_dir, exist_ok=True)
    before = _snapshot_download_folder(download_dir)
    print("   Files before download: " + str(len(before)))
    try:
        main_handle = driver.current_window_handle
    except Exception:
        main_handle = None

    _handle_alert(driver)

    # Wait for download modal
    print("   Waiting for download modal...")
    modal_opened = False
    for _ in range(20):
        if _modal_visible(driver):
            modal_opened = True
            break
        time.sleep(0.5)

    if not modal_opened:
        href = _download_link_href(driver)
        if href:
            modal_opened = True

    if not modal_opened:
        _dump_debug(driver, "no_modal")
        return (False, "no_modal")

    time.sleep(0.5)
    _dump_debug(driver, "modal")

    for attempt in range(1, 6):
        print(f"   Download modal attempt {attempt}/5")
        href = _download_link_href(driver)

        if not href:
            code = _get_modal_captcha_code(driver)
            if not code:
                _refresh_download_captcha(driver)
                time.sleep(1)
                continue

            print(f"   Entering modal captcha: '{code}'")
            _submit_modal_captcha(driver, code)

            # Wait for response (up to 8s)
            link_ready = False
            for _ in range(16):
                time.sleep(0.5)
                # Check error
                err_text = ""
                try:
                    err_text = driver.execute_script("return ($('#pcaptcha_code').length ? $('#pcaptcha_code').text() : '');") or ""
                except Exception:
                    pass
                if "correct" in err_text.lower() or "invalid" in err_text.lower():
                    print(f"   Modal captcha rejected: {err_text}")
                    break

                href = _download_link_href(driver)
                if href:
                    print(f"   Download link generated: {href}")
                    link_ready = True
                    break

            if not link_ready:
                _refresh_download_captcha(driver)
                time.sleep(1)
                continue

        # Download the file
        ok, res = _download_contract_file(driver, href, download_dir, before)

        # Close modal cleanly
        try:
            driver.execute_script("$('#viewContractModel').modal('hide');")
        except Exception:
            pass

        # Close any stray tabs
        try:
            if main_handle and len(driver.window_handles) > 1:
                for h in list(driver.window_handles):
                    if h != main_handle:
                        driver.switch_to.window(h)
                        driver.close()
                driver.switch_to.window(main_handle)
        except Exception:
            pass

        if ok:
            return (True, res)
        else:
            return (False, res)

    # If all 5 attempts failed
    try:
        driver.execute_script("$('#viewContractModel').modal('hide');")
    except Exception:
        pass
    return (False, "download_captcha_failed")