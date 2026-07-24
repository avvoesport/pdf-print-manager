import os
import time
import re
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from PySide6.QtCore import QThread, Signal

WHATSAPP_PROFILE_DIR = str(Path.home() / ".printavvo_wa_session")
DOWNLOAD_TEMP_DIR = str(Path.home() / ".printavvo_wa_downloads")


class WhatsAppScraper(QThread):
    """
    Playwright-based WhatsApp Web scraper using a 2-step process:
    1. Index: Find files in DOM, inject IDs, extract dates, and return metadata.
    2. Download: Click specific injected IDs and save files.
    """
    status_update  = Signal(str)
    login_required = Signal()
    logged_in      = Signal()
    contacts_ready = Signal(list)
    files_indexed  = Signal(list)  # Emits list of metadata dicts
    files_downloaded = Signal(list) # Emits list of file paths
    error          = Signal(str)

    def __init__(self):
        super().__init__()
        self._action = "login"
        self._target_chat_index = None
        self._items_to_download = []
        os.makedirs(WHATSAPP_PROFILE_DIR, exist_ok=True)
        os.makedirs(DOWNLOAD_TEMP_DIR, exist_ok=True)

    def request_contacts(self):
        self._action = "list_contacts"

    def request_scrape(self, chat_index: int):
        self._target_chat_index = chat_index
        self._action = "index_chat"

    def request_download(self, items: list):
        self._items_to_download = items
        self._action = "download_items"

    # ------------------------------------------------------------------ #
    def run(self):
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            self.error.emit("Playwright is not installed.\nRun: pip install playwright && playwright install chromium")
            return

        try:
            with sync_playwright() as pw:
                self.status_update.emit("Launching browser…")
                context = pw.chromium.launch_persistent_context(
                    WHATSAPP_PROFILE_DIR,
                    headless=False,
                    accept_downloads=True,
                    args=[
                        "--no-sandbox",
                        "--start-minimized",
                        "--window-size=1100,780",
                    ],
                    viewport={"width": 1100, "height": 780},
                )
                page = context.pages[0] if context.pages else context.new_page()

                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
                self.status_update.emit("Waiting for WhatsApp Web to load…")

                if not self._wait_for_login(page, PWTimeout):
                    return

                self.logged_in.emit()
                self.status_update.emit("Logged in! Loading chats…")
                self.contacts_ready.emit(self._get_contacts(page))

                while not self.isInterruptionRequested():
                    if self._action == "list_contacts":
                        self.status_update.emit("Refreshing chat list…")
                        self.contacts_ready.emit(self._get_contacts(page))
                        self._action = "idle"
                        
                    elif self._action == "index_chat":
                        items = self._index_chat(page, self._target_chat_index, PWTimeout)
                        self.files_indexed.emit(items)
                        self._action = "idle"
                        
                    elif self._action == "download_items":
                        paths = self._download_items(page, self._items_to_download)
                        self.files_downloaded.emit(paths)
                        self._action = "idle"
                        
                    time.sleep(0.1)

        except Exception as e:
            self.error.emit(f"Browser crashed or closed: {str(e)}")

    # ------------------------------------------------------------------ #
    def _wait_for_login(self, page, PWTimeout):
        qr_emitted = False
        for _ in range(60):
            if self.isInterruptionRequested(): return False
            if page.query_selector("div[aria-label='Chat list']"):
                return True
            if not qr_emitted and page.query_selector("canvas[aria-label='Scan this QR code to link a device']"):
                self.login_required.emit()
                self.status_update.emit("📱 Please scan the QR code with your phone.")
                qr_emitted = True
            time.sleep(0.5)
        self.error.emit("Timed out waiting for WhatsApp login.")
        return False

    def _get_contacts(self, page):
        contacts = []
        try:
            items = page.query_selector_all("div[aria-label='Chat list'] > div")
            for i, item in enumerate(items):
                name_el = item.query_selector("span[dir='auto'][title]")
                if name_el:
                    name = name_el.get_attribute("title") or name_el.inner_text()
                    if name.strip():
                        contacts.append({"name": name.strip(), "index": i})
        except Exception:
            pass
        return contacts

    # ------------------------------------------------------------------ #
    # STEP 1: Indexing
    # ------------------------------------------------------------------ #
    def _index_chat(self, page, chat_index: int, PWTimeout):
        self.status_update.emit("Opening chat...")
        indexed_items = []
        
        try:
            items = page.query_selector_all("div[aria-label='Chat list'] > div")
            if chat_index >= len(items):
                self.error.emit("Chat not found.")
                return []

            items[chat_index].click()
            time.sleep(2.5)

            self.status_update.emit("Scrolling to load recent messages...")
            try:
                panel = page.query_selector("div[data-testid='conversation-panel-messages'], div[role='application']")
                if panel: panel.click(timeout=2000)
            except Exception: pass

            for _ in range(8):
                page.keyboard.press("PageUp")
                time.sleep(0.5)
            time.sleep(1)

            # --- Extract Date JS ---
            JS_EXTRACT_DATE = """el => {
                let curr = el;
                while(curr && curr.getAttribute('role') !== 'row') curr = curr.parentElement;
                if (!curr) return null;
                let pre = curr.getAttribute('data-pre-plain-text');
                if (pre) {
                    let m = pre.match(/\\[.*?, (.*?)\\]/);
                    if (m) return m[1];
                }
                let prev = curr.previousElementSibling;
                while (prev) {
                    if (prev.innerText && prev.innerText.length < 20) {
                        let text = prev.innerText.trim();
                        if (text === 'TODAY' || text === 'YESTERDAY' || text.match(/^\\d{2}\\/\\d{2}\\/\\d{4}$/) || text.match(/^[A-Za-z]+day$/)) return text;
                    }
                    prev = prev.previousElementSibling;
                }
                return null;
            }"""

            # 1. Index PDFs
            self.status_update.emit("Scanning for PDF documents...")
            pdf_locators = page.locator("div[role='button']").filter(has_text=re.compile(r"\.pdf", re.IGNORECASE))
            for i in range(pdf_locators.count()):
                try:
                    bubble = pdf_locators.nth(i)
                    item_id = f"printavvo_pdf_{i}"
                    bubble.evaluate(f"el => el.setAttribute('data-printavvo-id', '{item_id}')")
                    date_str = bubble.evaluate(JS_EXTRACT_DATE)
                    
                    # Extract filename (usually first line of text)
                    text = bubble.evaluate("el => el.innerText")
                    fname = text.split('\\n')[0] if text else "Unknown PDF"
                    
                    indexed_items.append({
                        "id": item_id,
                        "name": fname,
                        "type": "PDF",
                        "date_str": date_str,
                        "is_pdf": True
                    })
                except Exception as e:
                    print(f"Error indexing PDF {i}: {e}")

            # 2. Index Images
            self.status_update.emit("Scanning for images...")
            img_locators = page.locator("img[src^='blob:']")
            for i in range(img_locators.count()):
                try:
                    img_el = img_locators.nth(i)
                    item_id = f"printavvo_img_{i}"
                    img_el.evaluate(f"el => el.setAttribute('data-printavvo-id', '{item_id}')")
                    date_str = img_el.evaluate(JS_EXTRACT_DATE)
                    
                    indexed_items.append({
                        "id": item_id,
                        "name": f"Image {i+1}",
                        "type": "JPEG",
                        "date_str": date_str,
                        "is_pdf": False
                    })
                except Exception as e:
                    print(f"Error indexing Image {i}: {e}")

        except Exception as e:
            self.error.emit(f"Failed to index chat: {str(e)}")

        self.status_update.emit(f"✅ Found {len(indexed_items)} files. Please select the ones you want to download.")
        return indexed_items

    # ------------------------------------------------------------------ #
    # STEP 2: Downloading
    # ------------------------------------------------------------------ #
    def _download_items(self, page, items: list):
        collected = []
        total = len(items)
        
        for i, item in enumerate(items):
            self.status_update.emit(f"Downloading {i+1}/{total}: {item['name']}...")
            try:
                el = page.locator(f"[data-printavvo-id='{item['id']}']")
                
                if item['is_pdf']:
                    # PDF click logic
                    dl_icon = el.locator("[data-icon*='download'], [aria-label*='Download']")
                    if dl_icon.count() > 0:
                        with page.expect_download(timeout=5000) as dl_info:
                            dl_icon.first.click(timeout=1000)
                        collected.append((dl_info.value, item['date_str']))
                    else:
                        el.click(timeout=2000)
                        time.sleep(1.5)
                        dl_btns = page.locator("[data-icon*='download'], [aria-label*='Download'], [aria-label*='unduh']")
                        if dl_btns.count() > 0:
                            with page.expect_download(timeout=5000) as dl_info:
                                dl_btns.first.click(timeout=2000)
                            collected.append((dl_info.value, item['date_str']))
                        page.keyboard.press("Escape")
                        time.sleep(0.8)
                else:
                    # Image click logic
                    el.click(timeout=2000)
                    time.sleep(1.2)
                    dl_btns = page.locator("[data-icon*='download'], [aria-label*='Download'], [aria-label*='unduh']")
                    if dl_btns.count() > 0:
                        with page.expect_download(timeout=5000) as dl_info:
                            dl_btns.first.click(timeout=2000)
                        collected.append((dl_info.value, item['date_str']))
                    page.keyboard.press("Escape")
                    time.sleep(0.8)
                    
            except Exception as e:
                print(f"Failed to download {item['name']}: {e}")
                try: page.keyboard.press("Escape") 
                except: pass

        # Save downloaded files
        self.status_update.emit("Saving files...")
        saved_paths = []
        seen_names = set()
        seen_hashes = set()
        allowed = ('.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')

        for dl, date_str in collected:
            try:
                fname = dl.suggested_filename or f"wa_file_{int(time.time())}"
                if not any(fname.lower().endswith(ext) for ext in allowed):
                    continue
                    
                dest = os.path.join(DOWNLOAD_TEMP_DIR, fname)
                
                dl_path = dl.path()
                if not dl_path: continue
                    
                with open(dl_path, 'rb') as f:
                    file_hash = hashlib.md5(f.read()).hexdigest()
                    
                if file_hash in seen_hashes:
                    continue
                seen_hashes.add(file_hash)
                
                base, ext = os.path.splitext(fname)
                counter = 1
                while dest in seen_names or os.path.exists(dest):
                    dest = os.path.join(DOWNLOAD_TEMP_DIR, f"{base}_{counter}{ext}")
                    counter += 1
                
                dl.save_as(dest)
                seen_names.add(dest)
                saved_paths.append(dest)
                
                # Apply date
                if date_str:
                    try:
                        parsed_date = None
                        ds = date_str.upper().strip()
                        today = datetime.now()
                        if ds == "TODAY":
                            parsed_date = today
                        elif ds == "YESTERDAY":
                            parsed_date = today - timedelta(days=1)
                        elif re.match(r"^\d{2}/\d{2}/\d{4}$", ds):
                            parts = ds.split('/')
                            parsed_date = datetime(int(parts[2]), int(parts[1]), int(parts[0]), 12, 0, 0)
                            
                        if parsed_date:
                            ts = parsed_date.timestamp()
                            os.utime(dest, (ts, ts))
                    except: pass
                
            except Exception as e:
                print(f"Failed to save download: {e}")

        return saved_paths
