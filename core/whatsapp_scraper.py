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
                        "--start-minimized",       # Run in background (minimized)
                        "--window-size=1100,780",
                    ],
                    viewport={"width": 1100, "height": 780},
                )
                page = context.pages[0] if context.pages else context.new_page()

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
        """
        Open the chat and download all PDFs / images.
        Uses page.on('download') to capture every browser download event.
        Directly clicks document bubbles and image thumbnails to trigger downloads.
        """
        import re
        collected = []

        def _on_download(dl):
            collected.append(dl)

        page.on("download", _on_download)

        try:
            # 1. Click the chat
            items = page.query_selector_all("div[aria-label='Chat list'] > div")
            if chat_index >= len(items):
                self.error.emit("Chat not found. Please refresh the contact list.")
                return []

            items[chat_index].click()
            time.sleep(2.5)

            # 2. Focus message panel and scroll up to load history
            self.status_update.emit("Scrolling to load messages…")
            try:
                panel = page.query_selector(
                    "div[data-testid='conversation-panel-messages'], "
                    "div[role='application']"
                )
                if panel:
                    panel.click()
            except Exception:
                pass

            for _ in range(12):
                page.keyboard.press("PageUp")
                time.sleep(0.5)
            time.sleep(1.5)

            # 3. Download PDFs (Documents)
            # Find document bubbles (they usually have role="button" and contain ".pdf")
            self.status_update.emit("Downloading PDFs...")
            pdf_locators = page.locator("div[role='button']").filter(has_text=re.compile(r"\.pdf", re.IGNORECASE))
            count = pdf_locators.count()
            for i in range(count):
                try:
                    pdf_locators.nth(i).click()
                    time.sleep(1)
                except Exception:
                    pass

            # 4. Download images by opening image viewer
            # Chat images have a 'blob:' src. Profile pics don't.
            self.status_update.emit("Downloading images...")
            images = page.locator("img[src^='blob:']")
            img_count = images.count()
            for i in range(img_count):
                try:
                    images.nth(i).click() # Open image viewer
                    time.sleep(1.5)
                    
                    # Click the download button in the viewer
                    dl_btns = page.locator("[data-icon*='download'], [aria-label*='Download'], [aria-label*='unduh']")
                    if dl_btns.count() > 0:
                        dl_btns.first.click()
                        time.sleep(1)
                    
                    page.keyboard.press("Escape") # Close viewer
                    time.sleep(0.8)
                except Exception:
                    try: 
                        page.keyboard.press("Escape")
                    except: 
                        pass

            # 5. Fallback: click any explicit download arrows not caught above
            self.status_update.emit("Checking for missed files...")
            page.evaluate("""
                () => {
                    document.querySelectorAll('[data-icon*="download"], [aria-label*="Download"]').forEach(el => {
                        try { el.click(); } catch(e) {}
                    });
                }
            """)
            time.sleep(4)

        except Exception as e:
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

