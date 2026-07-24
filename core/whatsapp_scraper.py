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

    def request_scrape(self, chat_name: str):
        self._target_chat_name = chat_name
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
                        items = self._index_chat(page, self._target_chat_name)
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
    def _index_chat(self, page, chat_name: str):
        self.status_update.emit(f"Opening chat: {chat_name}...")
        indexed_items = []
        
        try:
            # Click the chat by exact title
            chat_locator = page.locator(f"span[title='{chat_name}']").first
            if chat_locator.count() == 0:
                self.error.emit(f"Chat '{chat_name}' not found on screen.")
                return []

            chat_locator.click()
            time.sleep(2.5)

            self.status_update.emit("Scrolling to load recent messages...")
            try:
                panel = page.query_selector("div[data-testid='conversation-panel-messages'], div[role='application']")
                if panel: panel.click(timeout=2000)
            except Exception: pass

            for _ in range(8):
                page.keyboard.press("PageUp")
                time.sleep(0.5)
            time            # --- Extract DOM sequentially ---
            self.status_update.emit("Indexing chat messages (Texts, PDFs, Images)...")
            
            JS_INDEX_CHAT = """() => {
                let results = [];
                let rows = document.querySelectorAll('div[role="row"]');
                rows.forEach((row, index) => {
                    let item_id = "printavvo_msg_" + index;
                    row.setAttribute('data-printavvo-id', item_id);
                    
                    let dateStr = null;
                    let curr = row;
                    let pre = curr.getAttribute('data-pre-plain-text');
                    if (pre) {
                        let m = pre.match(/\\[.*?, (.*?)\\]/);
                        if (m) dateStr = m[1];
                    }
                    if (!dateStr) {
                        let prev = curr.previousElementSibling;
                        while (prev) {
                            if (prev.innerText && prev.innerText.length < 20) {
                                let text = prev.innerText.trim();
                                if (text === 'TODAY' || text === 'YESTERDAY' || text.match(/^\\d{2}\\/\\d{2}\\/\\d{4}$/) || text.match(/^[A-Za-z]+day$/)) {
                                    dateStr = text;
                                    break;
                                }
                            }
                            prev = prev.previousElementSibling;
                        }
                    }
                    
                    let rowText = row.innerText || "";
                    let timeStr = "";
                    let timeMatch = rowText.match(/\\d{1,2}:\\d{2}\\s?(?:am|pm|AM|PM)?/g);
                    if (timeMatch && timeMatch.length > 0) {
                        timeStr = timeMatch[timeMatch.length - 1];
                    }
                    
                    let item = { id: item_id, date_str: dateStr, time_str: timeStr };
                    
                    let pdfBtn = row.querySelector("div[role='button']");
                    let isPdf = pdfBtn && rowText.toLowerCase().includes('.pdf');
                    let img = row.querySelector("img[src^='blob:']");
                    
                    if (isPdf) {
                        item.type = 'PDF';
                        item.is_pdf = true;
                        item.is_file = true;
                        item.name = rowText.split('\\n')[0];
                        item.content = rowText;
                        results.push(item);
                    } else if (img) {
                        item.type = 'IMAGE';
                        item.is_pdf = false;
                        item.is_file = true;
                        item.name = 'Image';
                        item.content = rowText;
                        results.push(item);
                    } else {
                        let textSpan = row.querySelector("span.selectable-text");
                        if (textSpan) {
                            item.type = 'TEXT';
                            item.is_file = false;
                            item.is_pdf = false;
                            let text = textSpan.innerText;
                            item.name = text.length > 60 ? text.substring(0, 57) + '...' : text;
                            item.content = text;
                            results.push(item);
                        }
                    }
                });
                return results;
            }"""
            
            indexed_items = page.evaluate(JS_INDEX_CHAT)
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
            self.status_update.emit(f"Processing {i+1}/{total}: {item['name']}...")
            try:
                if not item.get('is_file'):
                    # TEXT MESSAGE: Generate PDF via Playwright
                    fname = f"WhatsApp_Text_{int(time.time())}_{i}.pdf"
                    dest = os.path.join(DOWNLOAD_TEMP_DIR, fname)
                    content = item.get('content', '')
                    date_str = item.get('date_str', 'Unknown Date')
                    html = f"<html><body style='font-family: sans-serif; padding: 40px; font-size: 18px;'><h3>WhatsApp Message - {date_str}</h3><p style='white-space: pre-wrap;'>{content}</p></body></html>"
                    
                    new_page = page.context.new_page()
                    new_page.set_content(html)
                    new_page.pdf(path=dest)
                    new_page.close()
                    
                    saved_paths.append(dest)
                    continue

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
