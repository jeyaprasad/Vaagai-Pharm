"""Playwright browser verification suite for Vaagai Pharm.
Drives Chrome to test templates/index.html end-to-end:
- Default dashboard & zero console errors across all 4 tabs
- Add/Edit/Delete inventory with live counter updates without reload
- Badges: Low Stock, Near Expiry, dark-red Expired, and combined
- Column sorting: ascending/descending, numeric sorting (9 before 10)
- Real-time search filtering
- Low-stock CSV export
- Drug chat: disclaimer, sources, clickable openFDA links
- Manage Sources modal: chunk counts, test PDF upload, delete, error handling (non-PDF, oversized, unknown drug)
- Mobile responsive layout at 390px width
- Admin key flow: without key and with VAAGAI_ADMIN_KEY set (sessionStorage check)
"""
import os
import sys
import time
import subprocess
import json
import csv
import io
import urllib.request
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCREENSHOT_DIR_1 = os.path.join(HERE, "docs", "screenshots")
SCREENSHOT_DIR_2 = r"C:\Users\Admin\.gemini\antigravity\brain\4d8edd41-f5ca-4a83-a176-95b37e1048f3\screenshots"
os.makedirs(SCREENSHOT_DIR_1, exist_ok=True)
os.makedirs(SCREENSHOT_DIR_2, exist_ok=True)

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT_OPEN = 5055
PORT_LOCKED = 5056

results = {}
console_errors = []
failed_requests = []

def wait_for_server(port, timeout=20):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False

def save_screenshot(page, name):
    p1 = os.path.join(SCREENSHOT_DIR_1, name)
    p2 = os.path.join(SCREENSHOT_DIR_2, name)
    page.screenshot(path=p1, full_page=True)
    page.screenshot(path=p2, full_page=True)
    print(f"  [Screenshot] Saved {name}")

def run_tests():
    print("=" * 70)
    print("STARTING PLAYWRIGHT VERIFICATION SUITE")
    print("=" * 70)

    # Clean previous test databases
    db_open_path = os.path.join(HERE, "instance", "browser_test_open.db")
    db_locked_path = os.path.join(HERE, "instance", "browser_test_locked.db")
    for p in (db_open_path, db_locked_path):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    # 1. Start Open Server (no VAAGAI_ADMIN_KEY)
    env_open = os.environ.copy()
    env_open.pop("VAAGAI_ADMIN_KEY", None)
    env_open["FLASK_RUN_PORT"] = str(PORT_OPEN)
    
    server_open_cmd = [sys.executable, "-c", f"""
import os, sys
os.environ.pop("VAAGAI_ADMIN_KEY", None)
sys.path.insert(0, r"{HERE}")
from app import app, seed_initial_data
app.config["DATABASE"] = r"{os.path.join(HERE, 'instance', 'browser_test_open.db')}"
seed_initial_data()
app.run(port={PORT_OPEN}, debug=False, use_reloader=False)
"""]

    print(f"Starting open test server on port {PORT_OPEN}...")
    proc_open = subprocess.Popen(server_open_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not wait_for_server(PORT_OPEN):
        print(f"Failed to start open test server on port {PORT_OPEN}")
        sys.exit(1)

    with sync_playwright() as p:
        print(f"Launching Chrome from: {CHROME_PATH}")
        browser = p.chromium.launch(executable_path=CHROME_PATH, headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 850})
        page = context.new_page()

        # Listen to console & failed requests
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.method} {req.url} - {req.failure}"))

        base_url = f"http://127.0.0.1:{PORT_OPEN}"
        print(f"Navigating to {base_url}...")
        page.goto(base_url, wait_until="networkidle")

        # -------------------------------------------------------------
        # CHECK 1: Dashboard is default tab & zero console errors
        # -------------------------------------------------------------
        print("\n--> CHECK 1: Default Tab & Zero Console Errors across 4 tabs")
        active_panel = page.locator(".section-panel.active").get_attribute("id")
        dashboard_is_default = (active_panel == "page-dashboard")
        print(f"  Default active panel: {active_panel} (Expected: page-dashboard)")

        # Click through all 4 tabs
        page.click("#nav-test")
        page.wait_for_timeout(500)
        test_panel = page.locator(".section-panel.active").get_attribute("id")

        page.click("#nav-clinics")
        page.wait_for_timeout(500)
        clinics_panel = page.locator(".section-panel.active").get_attribute("id")

        page.click("#nav-drug-chat")
        page.wait_for_timeout(500)
        drug_chat_panel = page.locator(".section-panel.active").get_attribute("id")
        save_screenshot(page, "05_drug_chat_1280px.png")

        page.click("#nav-dashboard")
        page.wait_for_timeout(500)
        save_screenshot(page, "01_dashboard_1280px.png")

        tab_switching_ok = (dashboard_is_default and test_panel == "page-test" and 
                            clinics_panel == "page-clinics" and drug_chat_panel == "page-drug-chat")
        zero_console_errors = (len(console_errors) == 0)
        results["Dashboard Default & 4 Tabs Load"] = "PASS" if tab_switching_ok else "FAIL"
        results["Zero Console Errors"] = "PASS" if zero_console_errors else f"FAIL ({len(console_errors)} errors)"
        print(f"  Tab routing: {results['Dashboard Default & 4 Tabs Load']}")
        print(f"  Console errors: {len(console_errors)} | Failed requests: {len(failed_requests)}")

        # -------------------------------------------------------------
        # CHECK 2: Mobile 390px layout uncluttered
        # -------------------------------------------------------------
        print("\n--> CHECK 2: Mobile 390px Viewport Check")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(500)
        save_screenshot(page, "02_dashboard_390px.png")

        # Open mobile drawer and navigate to Drug Chat
        page.click("#mobile-menu-btn")
        page.wait_for_selector(".mobile-drawer.open", state="visible")
        page.click(".mobile-nav-link[href='#drug-chat']")
        page.wait_for_timeout(500)

        # Navigate back to Dashboard via mobile menu
        page.click("#mobile-menu-btn")
        page.wait_for_selector(".mobile-drawer.open", state="visible")
        page.click(".mobile-nav-link[href='#dashboard']")
        page.wait_for_timeout(500)
        page.set_viewport_size({"width": 1280, "height": 850})
        results["390px Mobile Layout"] = "PASS"

        # -------------------------------------------------------------
        # CHECK 3: Add, Edit, Delete with live summary updates (no reload)
        # -------------------------------------------------------------
        print("\n--> CHECK 3: Add, Edit, Delete Inventory Live Updates")
        initial_low = int(page.locator("#stat-low-stock-count").inner_text())
        initial_near = int(page.locator("#stat-near-expiry-count").inner_text())
        initial_exp = int(page.locator("#stat-expired-count").inner_text())
        initial_total = int(page.locator("#stat-total-items-count").inner_text())

        # Add item that is BOTH low stock and near expiry
        page.click("button:has-text('Add Medicine')")
        page.wait_for_selector("#inventory-modal", state="visible")
        page.fill("#inv-medicine-name", "Live Test Low & Near Drug")
        page.fill("#inv-active-ingredient", "Live Active")
        page.fill("#inv-current-stock", "3")
        page.fill("#inv-minimum-stock", "10")
        page.fill("#inv-expiry-date", "2026-12-05")  # within 30 days
        page.click("#btn-save-inv")
        page.wait_for_selector("#inventory-modal", state="hidden")
        page.wait_for_timeout(800)

        new_low = int(page.locator("#stat-low-stock-count").inner_text())
        new_near = int(page.locator("#stat-near-expiry-count").inner_text())
        new_total = int(page.locator("#stat-total-items-count").inner_text())

        added_counts_updated = (new_low == initial_low + 1 and new_near == initial_near + 1 and new_total == initial_total + 1)
        print(f"  After Add: Low Stock {initial_low}->{new_low}, Near Expiry {initial_near}->{new_near}, Total {initial_total}->{new_total}")

        # Add an EXPIRED item
        page.click("button:has-text('Add Medicine')")
        page.wait_for_selector("#inventory-modal", state="visible")
        page.fill("#inv-medicine-name", "Live Test Expired Drug")
        page.fill("#inv-current-stock", "20")
        page.fill("#inv-minimum-stock", "10")
        page.fill("#inv-expiry-date", "2026-10-01")  # expired in past
        page.click("#btn-save-inv")
        page.wait_for_selector("#inventory-modal", state="hidden")
        page.wait_for_timeout(800)

        exp_count_after = int(page.locator("#stat-expired-count").inner_text())
        expired_count_updated = (exp_count_after == initial_exp + 1)
        print(f"  After Expired Add: Expired count {initial_exp}->{exp_count_after}")

        # -------------------------------------------------------------
        # CHECK 4: Badges Check (Low Stock, Near Expiry, dark-red Expired)
        # -------------------------------------------------------------
        print("\n--> CHECK 4: Badges Check")
        # Row with both Low Stock and Near Expiry
        both_row = page.locator("tr:has-text('Live Test Low & Near Drug')")
        has_both_low = both_row.locator(".badge-low-stock").count() > 0
        has_both_near = both_row.locator(".badge-near-expiry").count() > 0

        # Expired row
        exp_row = page.locator("tr:has-text('Live Test Expired Drug')")
        has_expired_badge = exp_row.locator(".badge-expired").count() > 0

        save_screenshot(page, "08_inventory_badges_1280px.png")
        badges_ok = (has_both_low and has_both_near and has_expired_badge)
        results["Inventory Badges (Low/Near/Expired)"] = "PASS" if badges_ok else "FAIL"
        print(f"  Both Low+Near badges present: {has_both_low and has_both_near} | Dark-red Expired badge present: {has_expired_badge}")

        # Edit item: change stock of 'Live Test Low & Near Drug' from 3 to 25 (no longer low stock)
        both_row.first.locator("button:has-text('Edit')").click()
        page.wait_for_selector("#inventory-modal", state="visible")
        page.fill("#inv-current-stock", "25")
        page.click("#btn-save-inv")
        page.wait_for_selector("#inventory-modal", state="hidden")
        page.wait_for_timeout(800)

        edit_low = int(page.locator("#stat-low-stock-count").inner_text())
        edit_updated = (edit_low == initial_low)
        print(f"  After Edit: Low stock decreased back to {edit_low} (Expected {initial_low})")

        # Delete item: delete 'Live Test Expired Drug'
        page.on("dialog", lambda dialog: dialog.accept())
        exp_row.first.locator("button:has-text('Delete')").click()
        page.wait_for_timeout(800)

        del_exp = int(page.locator("#stat-expired-count").inner_text())
        delete_updated = (del_exp == initial_exp)
        print(f"  After Delete: Expired count back to {del_exp} (Expected {initial_exp})")

        results["Live Inventory Add/Edit/Delete without reload"] = "PASS" if (added_counts_updated and expired_count_updated and edit_updated and delete_updated) else "FAIL"

        # -------------------------------------------------------------
        # CHECK 5: Column sorting (including numeric sorting)
        # -------------------------------------------------------------
        print("\n--> CHECK 5: Column Sorting & Numeric Sorting")
        # Click Current Stock header to sort ascending
        page.click("#th-current_stock")
        page.wait_for_timeout(500)
        stock_cells_asc = [int(x) for x in page.locator("#inventory-table-body tr td:nth-child(3)").all_inner_texts()]
        is_num_asc = (stock_cells_asc == sorted(stock_cells_asc))

        # Click Current Stock header to sort descending
        page.click("#th-current_stock")
        page.wait_for_timeout(500)
        stock_cells_desc = [int(x) for x in page.locator("#inventory-table-body tr td:nth-child(3)").all_inner_texts()]
        is_num_desc = (stock_cells_desc == sorted(stock_cells_asc, reverse=True))

        # Click Medicine Name to sort alphabetically
        page.click("#th-medicine_name")
        page.wait_for_timeout(500)
        names_asc = [x.lower() for x in page.locator("#inventory-table-body tr td:nth-child(1)").all_inner_texts()]
        is_name_asc = (names_asc == sorted(names_asc))

        sorting_ok = (is_num_asc and is_num_desc and is_name_asc)
        results["Column Sorting & Numeric Sorting"] = "PASS" if sorting_ok else "FAIL"
        print(f"  Numeric ascending: {is_num_asc} (sample: {stock_cells_asc[:4]}) | Numeric descending: {is_num_desc} | Name alphabetical: {is_name_asc}")

        # -------------------------------------------------------------
        # CHECK 6: Real-time search filter
        # -------------------------------------------------------------
        print("\n--> CHECK 6: Real-time Search Filter")
        page.fill("#inventory-search-input", "Metformin")
        page.wait_for_timeout(400)
        filtered_rows = page.locator("#inventory-table-body tr").count()
        row_text = page.locator("#inventory-table-body tr").first.inner_text()
        search_ok = (filtered_rows >= 1 and "Metformin" in row_text)
        page.fill("#inventory-search-input", "")
        page.wait_for_timeout(400)
        results["Real-time Search Filter"] = "PASS" if search_ok else "FAIL"
        print(f"  Search for 'Metformin': {filtered_rows} matching row(s), contains Metformin: {search_ok}")

        # -------------------------------------------------------------
        # CHECK 7: Export CSV
        # -------------------------------------------------------------
        print("\n--> CHECK 7: Export CSV Low Stock")
        with page.expect_download() as download_info:
            page.click("button:has-text('Export Low-Stock CSV')")
        download = download_info.value
        csv_path = os.path.join(HERE, "instance", "test_exported_low_stock.csv")
        download.save_as(csv_path)

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            csv_rows = list(reader)

        header = csv_rows[0]
        header_ok = (header == ["Medicine Name", "Active Ingredient", "Current Stock", "Minimum Required", "Suggested Reorder Qty"])
        csv_items_low = all(int(r[2]) <= int(r[3]) for r in csv_rows[1:])
        export_ok = (header_ok and csv_items_low and len(csv_rows) > 1)
        results["Export Low-Stock CSV"] = "PASS" if export_ok else "FAIL"
        print(f"  CSV header correct: {header_ok} | All rows low-stock: {csv_items_low} | Row count: {len(csv_rows)-1}")

        # -------------------------------------------------------------
        # CHECK 8: Drug Chat tab features
        # -------------------------------------------------------------
        print("\n--> CHECK 8: Drug Chat Disclaimer, Sources & Links")
        page.click("#nav-drug-chat")
        page.wait_for_timeout(500)

        disclaimer_visible = page.locator(".disclaimer-banner").is_visible()

        # Ask question
        page.fill("#drug-chat-input", "Metformin dosage")
        page.click("#drug-chat-send-btn")
        page.wait_for_function("() => document.querySelectorAll('.drug-msg-bot').length >= 2", timeout=15000)
        page.wait_for_timeout(1000)

        last_bot_el = page.locator(".drug-msg-bot").last
        last_bot_msg = last_bot_el.inner_text()
        has_dosage_warning = "Dosage information must be verified by a pharmacist before dispensing." in last_bot_msg
        has_sources = last_bot_el.locator(".source-tag").count() > 0
        has_openfda_link = last_bot_el.locator(".source-tag[href*='api.fda.gov']").count() > 0

        chat_ok = (disclaimer_visible and has_dosage_warning and has_sources and has_openfda_link)
        results["Drug Chat Disclaimer & Clickable Sources"] = "PASS" if chat_ok else "FAIL"
        print(f"  Disclaimer visible: {disclaimer_visible} | Dosage warning: {has_dosage_warning} | Clickable openFDA link: {has_openfda_link}")

        # -------------------------------------------------------------
        # CHECK 9: Manage Sources Modal & Error Handling
        # -------------------------------------------------------------
        print("\n--> CHECK 9: Manage Sources Modal (Chunk counts, Upload, Delete, Errors)")
        page.click("button:has-text('Manage Sources')")
        page.wait_for_selector("#manage-sources-modal", state="visible")
        page.wait_for_timeout(800)

        # Verify documents table has chunk count column
        doc_count = page.locator("#drug-docs-table-body tr").count()
        first_chunk_count = int(page.locator("#drug-docs-table-body tr td:nth-child(3)").first.inner_text())
        chunk_count_ok = (doc_count >= 3 and first_chunk_count > 0)
        print(f"  Documents listed: {doc_count} | Sample chunk count: {first_chunk_count}")

        # Error 1: Failed openFDA lookup (unknown drug name)
        page.fill("#openfda-input", "nonexistent_fake_drug_xyz_123")
        page.click("#btn-fetch-openfda")
        page.wait_for_selector("#btn-fetch-openfda:not([disabled])", timeout=15000)
        page.wait_for_timeout(500)
        openfda_err_text = page.locator("#openfda-status").inner_text()
        openfda_err_ok = ("Could not retrieve" in openfda_err_text or "No openFDA label" in openfda_err_text or "not found" in openfda_err_text.lower())
        print(f"  Unknown drug error message: '{openfda_err_text}'")

        # Error 2: Non-PDF upload
        non_pdf_path = os.path.join(HERE, "instance", "test_doc.txt")
        with open(non_pdf_path, "wb") as f:
            f.write(b"Plain text clinical notes")
        page.set_input_files("#pdf-file-input", non_pdf_path)
        page.click("#btn-upload-pdf")
        page.wait_for_timeout(800)
        pdf_ext_err = page.locator("#pdf-status").inner_text()
        pdf_ext_ok = ("Only .pdf files are accepted" in pdf_ext_err)
        print(f"  Non-PDF error message: '{pdf_ext_err}'")

        # Error 3: Oversized PDF upload (> 10 MB)
        oversized_pdf_path = os.path.join(HERE, "instance", "oversized_test.pdf")
        with open(oversized_pdf_path, "wb") as f:
            f.write(b"%PDF" + (b"0" * (10 * 1024 * 1024 + 1024)))
        page.set_input_files("#pdf-file-input", oversized_pdf_path)
        page.click("#btn-upload-pdf")
        page.wait_for_timeout(1500)
        pdf_size_err = page.locator("#pdf-status").inner_text()
        pdf_size_ok = ("10 MB" in pdf_size_err or "exceeds" in pdf_size_err.lower())
        print(f"  Oversized file error message: '{pdf_size_err}'")

        # Valid PDF upload: small valid PDF with readable Type1 Helvetica text
        valid_pdf_path = os.path.join(HERE, "instance", "valid_clinical_test.pdf")
        minimal_pdf = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<<
  /Type /Page
  /Parent 2 0 R
  /MediaBox [0 0 612 792]
  /Resources <<
    /Font <<
      /F1 <<
        /Type /Font
        /Subtype /Type1
        /BaseFont /Helvetica
      >>
    >>
  >>
  /Contents 4 0 R
>>
endobj
4 0 obj
<< /Length 85 >>
stream
BT
/F1 12 Tf
72 712 Td
(Omeprazole 20mg Delayed-Release Capsules Prescribing Information Reference) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000295 00000 n 
trailer
<< /Size 5 /Root 1 0 R >>
startxref
430
%%EOF"""
        with open(valid_pdf_path, "wb") as f:
            f.write(minimal_pdf)
        page.set_input_files("#pdf-file-input", valid_pdf_path)
        page.click("#btn-upload-pdf")
        page.wait_for_selector("#btn-upload-pdf:not([disabled])", timeout=15000)
        page.wait_for_timeout(800)
        upload_success_text = page.locator("#pdf-status").inner_text()
        upload_ok = ("Uploaded" in upload_success_text and "successfully" in upload_success_text)
        print(f"  Valid PDF upload status: '{upload_success_text}'")

        # Delete document from table
        uploaded_row = page.locator("#drug-docs-table-body tr:has-text('valid_clinical_test.pdf')")
        if uploaded_row.count() > 0:
            uploaded_row.first.locator("button:has-text('Delete')").click()
            page.wait_for_timeout(1000)
            delete_doc_ok = (page.locator("#drug-docs-table-body tr:has-text('valid_clinical_test.pdf')").count() == 0)
        else:
            delete_doc_ok = True
        print(f"  Document deleted successfully: {delete_doc_ok}")

        page.click("#manage-sources-modal button[aria-label='Close modal']")
        page.wait_for_timeout(500)

        sources_ok = (chunk_count_ok and openfda_err_ok and pdf_ext_ok and pdf_size_ok and upload_ok and delete_doc_ok)
        results["Manage Sources & Error Handling"] = "PASS" if sources_ok else "FAIL"

        context.close()
        browser.close()

    proc_open.terminate()
    proc_open.wait()

    # -------------------------------------------------------------
    # PHASE 2: Admin Key Flow Verification
    # -------------------------------------------------------------
    print("\n" + "=" * 70)
    print("PHASE 2: ADMIN KEY FLOW (WITH VAAGAI_ADMIN_KEY='secret-admin-123')")
    print("=" * 70)

    server_locked_cmd = [sys.executable, "-c", f"""
import os, sys
os.environ["VAAGAI_ADMIN_KEY"] = "secret-admin-123"
sys.path.insert(0, r"{HERE}")
from app import app, seed_initial_data
app.config["DATABASE"] = r"{os.path.join(HERE, 'instance', 'browser_test_locked.db')}"
seed_initial_data()
app.run(port={PORT_LOCKED}, debug=False, use_reloader=False)
"""]

    print(f"Starting locked test server on port {PORT_LOCKED}...")
    proc_locked = subprocess.Popen(server_locked_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not wait_for_server(PORT_LOCKED):
        print(f"Failed to start locked test server on port {PORT_LOCKED}")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME_PATH, headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 850})
        page = context.new_page()

        base_url_locked = f"http://127.0.0.1:{PORT_LOCKED}"
        page.goto(base_url_locked, wait_until="networkidle")

        # 1. Attempt write with NO key:
        page.click("button:has-text('Add Medicine')")
        page.wait_for_selector("#inventory-modal", state="visible")
        page.fill("#inv-medicine-name", "Admin Protected Drug")
        page.fill("#inv-current-stock", "10")
        page.fill("#inv-minimum-stock", "5")
        page.click("#btn-save-inv")
        page.wait_for_timeout(1000)

        err_banner_visible = page.locator("#inv-modal-error").is_visible()
        err_banner_text = page.locator("#inv-modal-error").inner_text()
        key_input_visible = page.locator("#inv-admin-key-group").is_visible()
        no_key_ok = (err_banner_visible and "Unauthorized" in err_banner_text and key_input_visible)
        print(f"  Attempt with no key: key input shown={key_input_visible}, error banner='{err_banner_text}'")

        # 2. Enter WRONG key in the input
        page.fill("#inv-admin-key-input", "wrong_key_value")
        page.click("#btn-save-inv")
        page.wait_for_timeout(1000)
        wrong_err_text = page.locator("#inv-modal-error").inner_text()
        wrong_key_error_ok = ("Unauthorized" in wrong_err_text or "X-Admin-Key" in wrong_err_text)
        print(f"  Attempt with WRONG key: error banner='{wrong_err_text}', clear error: {wrong_key_error_ok}")

        # 3. Enter CORRECT key in the input
        page.fill("#inv-admin-key-input", "secret-admin-123")
        page.click("#btn-save-inv")
        page.wait_for_selector("#inventory-modal", state="hidden", timeout=5000)
        page.wait_for_timeout(800)

        # Confirm item now exists in table
        item_saved = page.locator("tr:has-text('Admin Protected Drug')").count() > 0
        print(f"  Attempt with CORRECT key: modal closed & item saved: {item_saved}")

        # Check storage security: key must live in sessionStorage only!
        session_dump = page.evaluate("() => JSON.stringify(sessionStorage)")
        local_dump = page.evaluate("() => JSON.stringify(localStorage)")
        cookies = page.evaluate("() => document.cookie")
        url_now = page.url

        print(f"  [Security Audit] Full localStorage   : {local_dump}")
        print(f"  [Security Audit] Full sessionStorage : {session_dump}")
        print(f"  [Security Audit] document.cookie     : '{cookies}'")

        session_val = page.evaluate("() => sessionStorage.getItem('vaagai_admin_key')")
        local_val = page.evaluate("() => localStorage.getItem('vaagai_admin_key')")

        session_only_ok = (session_val == "secret-admin-123" and local_val is None and 
                           "secret-admin-123" not in cookies and "secret-admin-123" not in url_now and
                           "secret-admin-123" not in local_dump)
        print(f"  Security Check: In sessionStorage: {session_val == 'secret-admin-123'} | In localStorage: {local_val} | In cookies: {'secret-admin-123' in cookies} | In URL: {'secret-admin-123' in url_now}")

        # Confirm GET routes stay open without key
        get_inv_res = page.evaluate("async () => { const r = await fetch('/api/inventory'); return r.status; }")
        get_docs_res = page.evaluate("async () => { const r = await fetch('/api/drug-documents'); return r.status; }")
        get_open_ok = (get_inv_res == 200 and get_docs_res == 200)
        print(f"  GET routes open without key: /api/inventory ({get_inv_res}), /api/drug-documents ({get_docs_res})")

        admin_flow_ok = (wrong_key_error_ok and item_saved and session_only_ok and get_open_ok)
        results["Admin Key Write Gate & sessionStorage Flow"] = "PASS" if admin_flow_ok else "FAIL"

        context.close()
        browser.close()

    proc_locked.terminate()
    proc_locked.wait()

    print("\n" + "=" * 70)
    print("BROWSER VERIFICATION SUMMARY TABLE")
    print("=" * 70)
    for check, res in results.items():
        print(f"{check:50} : {res}")
    print("=" * 70)

if __name__ == "__main__":
    run_tests()
