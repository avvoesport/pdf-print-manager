import os
import time
from pathlib import Path
from PySide6.QtCore import QThread, Signal


WHATSAPP_PROFILE_DIR = str(Path.home() / ".printavvo_wa_session")
DOWNLOAD_TEMP_DIR = str(Path.home() / ".printavvo_wa_downloads")


class WhatsAppScraper(QThread):
    """
    Playwright-based WhatsApp Web scraper.
    Runs entirely in a background thread so the UI stays responsive.
    """
    status_update  = Signal(str)
    login_required = Signal()
    logged_in      = Signal()
    contacts_ready = Signal(list)
    files_scraped  = Signal(list)
    error          = Signal(str)

    def __init__(self):
        super().__init__()
        self._action = "login"
        self._target_chat_index = None
        os.makedirs(WHATSAPP_PROFILE_DIR, exist_ok=True)
        os.makedirs(DOWNLOAD_TEMP_DIR, exist_ok=True)

    def request_contacts(self):
        self._action = "list_contacts"

    def request_scrape(self, chat_index: int):
        self._target_chat_index = chat_index
        self._action = "scrape"

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
                        "--start-minimized",       # Best effort initial minimize
                        "--window-size=1100,780",
                    ],
                    viewport={"width": 1100, "height": 780},
                )
                page = context.pages[0] if context.pages else context.new_page()

                # Force minimize via Chrome DevTools Protocol (CDP) for Windows
                try:
                    client = context.new_cdp_session(page)
                    info = client.send("Browser.getWindowForTarget")
                    client.send("Browser.setWindowBounds", {
                        "windowId": info["windowId"],
                        "bounds": {"windowState": "minimized"}
                    })
                except Exception as e:
                    self.status_update.emit(f"Warning: Could not force minimize: {e}")

                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
                self.status_update.emit("Waiting for WhatsApp Web to load…")

                if not self._wait_for_login(page, PWTimeout):
                    context.close()
                    return

                self.logged_in.emit()
                self.status_update.emit("Logged in! Loading chats…")

                try:
                    page.wait_for_selector("div[aria-label='Chat list']", timeout=20000)
                except PWTimeout:
                    self.error.emit("Chat list did not load. Please try again.")
                    context.close()
                    return

                contacts = self._get_contacts(page)
                self.contacts_ready.emit(contacts)
                self.status_update.emit(f"Found {len(contacts)} chats. Select one to import.")

                self._action = "idle"
                while not self.isInterruptionRequested():
                    time.sleep(0.3)

                    if self._action == "list_contacts":
                        self._action = "idle"
                        self.status_update.emit("Refreshing chats…")
                        contacts = self._get_contacts(page)
                        self.contacts_ready.emit(contacts)
                        self.status_update.emit(f"Found {len(contacts)} chats. Select one to import.")

                    elif self._action == "scrape" and self._target_chat_index is not None:
                        idx = self._target_chat_index
                        self._target_chat_index = None
                        self._action = "idle"
                        files = self._scrape_chat(page, idx, PWTimeout)
                        self.files_scraped.emit(files)
                        if files:
                            self.status_update.emit(f"Done! {len(files)} file(s) imported.")
                        else:
                            self.status_update.emit("No files found. Try another chat.")

                context.close()

        except Exception as e:
            self.error.emit(f"WhatsApp scraper error:\n{str(e)}")

    # ------------------------------------------------------------------ #
    def _wait_for_login(self, page, PWTimeout):
        qr_emitted = False
        for _ in range(240):
            if self.isInterruptionRequested():
                return False
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

    def _scrape_chat(self, page, chat_index: int, PWTimeout):
        import re
        import hashlib
        from datetime import datetime

        log_path = os.path.join(DOWNLOAD_TEMP_DIR, "scraper_log.txt")
        def _log(msg):
            t = datetime.now().strftime("%H:%M:%S")
            line = f"[{t}] {msg}"
            print(line)
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except: pass
            self.status_update.emit(msg)

        _log(f"--- Starting scrape for chat index {chat_index} ---")
        collected = []

        def _on_download(dl):
            _log(f"Download triggered: {dl.suggested_filename}")
            collected.append(dl)

        page.on("download", _on_download)

        try:
            # 1. Click the chat
            items = page.query_selector_all("div[aria-label='Chat list'] > div")
            if chat_index >= len(items):
                _log("Error: Chat not found.")
                self.error.emit("Chat not found. Please refresh the contact list.")
                return []

            _log("Opening chat...")
            items[chat_index].click()
            time.sleep(2.5)

            # 2. Focus message panel and scroll up to load history
            _log("Scrolling to load recent messages...")
            try:
                panel = page.query_selector(
                    "div[data-testid='conversation-panel-messages'], div[role='application']"
                )
                if panel: panel.click(timeout=2000)
            except Exception:
                pass

            for _ in range(8):
                page.keyboard.press("PageUp")
                time.sleep(0.5)
            time.sleep(1)

            # 3. Download PDFs (Documents) sequentially
            _log("Scanning for PDF documents...")
            pdf_locators = page.locator("div[role='button']").filter(has_text=re.compile(r"\.pdf", re.IGNORECASE))
            pdf_count = pdf_locators.count()
            _log(f"Found {pdf_count} PDF document bubble(s).")
            
            for i in range(pdf_count):
                _log(f"Processing PDF {i+1} of {pdf_count}...")
                try:
                    bubble = pdf_locators.nth(i)
                    
                    # Look for explicit download icon inside the bubble
                    dl_icon = bubble.locator("[data-icon*='download'], [aria-label*='Download']")
                    if dl_icon.count() > 0:
                        dl_icon.first.click(timeout=1000)
                        _log("Clicked explicit download icon on PDF.")
                        time.sleep(1)
                        continue # Already downloaded directly, no viewer opened
                    
                    # Otherwise, click the bubble to open the PDF viewer
                    bubble.click(timeout=2000)
                    time.sleep(1.5)
                    
                    # Click download inside viewer
                    dl_btns = page.locator("[data-icon*='download'], [aria-label*='Download'], [aria-label*='unduh']")
                    if dl_btns.count() > 0:
                        dl_btns.first.click(timeout=2000)
                        _log("Clicked download button in PDF viewer.")
                        time.sleep(1)
                    
                    # Close viewer
                    page.keyboard.press("Escape")
                    time.sleep(0.8)
                except Exception as e:
                    _log(f"Error on PDF {i+1}: {str(e)}")
                    try: page.keyboard.press("Escape") 
                    except: pass

            # 4. Download images by opening image viewer
            _log("Scanning for images...")
            images = page.locator("img[src^='blob:']")
            img_count = images.count()
            _log(f"Found {img_count} image(s) in chat.")
            
            for i in range(img_count):
                _log(f"Processing image {i+1} of {img_count}...")
                try:
                    images.nth(i).click(timeout=2000) # Open image viewer
                    time.sleep(1.2)
                    
                    # Click the download button in the viewer
                    dl_btns = page.locator("[data-icon*='download'], [aria-label*='Download'], [aria-label*='unduh']")
                    if dl_btns.count() > 0:
                        dl_btns.first.click(timeout=2000)
                        _log("Clicked image download button.")
                        time.sleep(1)
                    else:
                        _log("No download button found for this image.")
                    
                    page.keyboard.press("Escape") # Close viewer
                    time.sleep(0.8)
                except Exception as e:
                    _log(f"Error on image {i+1}: {str(e)}")
                    try: page.keyboard.press("Escape") 
                    except: pass

            # 5. Fallback: click explicit download arrows
            _log("Checking for any missed explicit download buttons...")
            fallback_clicks = page.evaluate("""
                () => {
                    let clicked = 0;
                    document.querySelectorAll('[data-icon*="download"], [aria-label*="Download"]').forEach(el => {
                        try { el.click(); clicked++; } catch(e) {}
                    });
                    return clicked;
                }
            """)
            _log(f"Triggered {fallback_clicks} fallback download(s).")
            
            _log("Waiting 4 seconds for downloads to complete...")
            time.sleep(4)

        except Exception as e:
            _log(f"Fatal scrape error: {str(e)}")
            self.error.emit(f"Error while scraping:\n{str(e)}")
        finally:
            page.remove_listener("download", _on_download)

        # 6. Save all collected downloads, deduplicate by MD5 hash
        import hashlib
        allowed = ('.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
        saved = []
        seen_names = set()
        seen_hashes = set()

        for dl in collected:
            try:
                fname = dl.suggested_filename or f"wa_file_{int(time.time())}"
                if not any(fname.lower().endswith(ext) for ext in allowed):
                    continue

                # Give unique filename if collision
                base = fname
                counter = 1
                while fname in seen_names:
                    stem, ext = os.path.splitext(base)
                    fname = f"{stem}_{counter}{ext}"
                    counter += 1
                seen_names.add(fname)

                dest = os.path.join(DOWNLOAD_TEMP_DIR, fname)
                dl.save_as(dest)

                # Deduplicate by content hash — discard if we've seen this file before
                file_hash = hashlib.md5(open(dest, 'rb').read()).hexdigest()
                if file_hash in seen_hashes:
                    os.remove(dest)   # remove the duplicate
                    self.status_update.emit(f"Skipped duplicate: {fname}")
                    continue

                seen_hashes.add(file_hash)
                saved.append(dest)
                self.status_update.emit(f"Saved: {fname}")
            except Exception:
                pass

        return saved

