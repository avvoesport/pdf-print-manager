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
        Uses page.on('download') to capture every browser download event,
        then triggers downloads via JavaScript (no brittle CSS selectors needed).
        """
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

            for _ in range(10):
                page.keyboard.press("PageUp")
                time.sleep(0.5)
            time.sleep(1.5)

            # 3. Try opening the WhatsApp media/docs panel via chat header
            self.status_update.emit("Checking media gallery…")
            try:
                header = page.query_selector(
                    "header[data-testid='conversation-header'] span[dir='auto'], "
                    "header[data-testid='conversation-header']"
                )
                if header:
                    header.click()
                    time.sleep(1.5)

                    # Click "Media, links and docs" link if visible
                    for sel in [
                        "div[data-testid='all-media-link']",
                        "[data-testid='media-bar'] div",
                        "span[data-testid='media-section-header']",
                    ]:
                        el = page.query_selector(sel)
                        if el:
                            el.click()
                            time.sleep(1.5)
                            break

                    # Switch to Docs tab if present
                    for label in ["Docs", "Documents", "Files"]:
                        try:
                            tab = page.get_by_role("tab", name=label, exact=False)
                            if tab.count() > 0:
                                tab.first.click()
                                time.sleep(1)
                                break
                        except Exception:
                            pass
            except Exception:
                pass

            # 4. JavaScript: click every possible download trigger
            self.status_update.emit("Clicking download buttons…")
            _JS_CLICK_DOWNLOADS = """
                () => {
                    let clicked = 0;

                    // A: data-icon containing 'download'
                    document.querySelectorAll('[data-icon]').forEach(el => {
                        const v = (el.getAttribute('data-icon') || '').toLowerCase();
                        if (v.includes('download') || v === 'down' || v === 'arrow-down') {
                            try { el.click(); clicked++; } catch(e) {}
                        }
                    });

                    // B: aria-label containing 'download' or 'unduh' (Indonesian)
                    document.querySelectorAll('[aria-label]').forEach(el => {
                        const v = (el.getAttribute('aria-label') || '').toLowerCase();
                        if (v.includes('download') || v.includes('unduh')) {
                            try { el.click(); clicked++; } catch(e) {}
                        }
                    });

                    // C: data-testid containing 'download'
                    document.querySelectorAll('[data-testid]').forEach(el => {
                        const v = (el.getAttribute('data-testid') || '').toLowerCase();
                        if (v.includes('download')) {
                            try { el.click(); clicked++; } catch(e) {}
                        }
                    });

                    // D: <a> tags with blob or media hrefs
                    document.querySelectorAll('a[href^="blob:"], a[href*="media"]').forEach(el => {
                        try { el.click(); clicked++; } catch(e) {}
                    });

                    return clicked;
                }
            """
            n = page.evaluate(_JS_CLICK_DOWNLOADS)
            self.status_update.emit(f"Triggered {n} download element(s). Waiting…")
            time.sleep(4)

            # 5. Escape back to chat view and do a second pass
            try:
                page.keyboard.press("Escape")
                time.sleep(0.8)
            except Exception:
                pass

            n2 = page.evaluate(_JS_CLICK_DOWNLOADS)
            self.status_update.emit(f"Second pass: {n2} element(s). Waiting…")
            time.sleep(3)

        except Exception as e:
            self.error.emit(f"Error while scraping:\n{str(e)}")
        finally:
            page.remove_listener("download", _on_download)

        # 6. Save all collected downloads
        allowed = ('.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
        saved = []
        seen_names = set()
        for dl in collected:
            try:
                fname = dl.suggested_filename or f"wa_file_{int(time.time())}"
                if not any(fname.lower().endswith(ext) for ext in allowed):
                    continue
                base = fname
                counter = 1
                while fname in seen_names:
                    stem, ext = os.path.splitext(base)
                    fname = f"{stem}_{counter}{ext}"
                    counter += 1
                seen_names.add(fname)
                dest = os.path.join(DOWNLOAD_TEMP_DIR, fname)
                dl.save_as(dest)
                saved.append(dest)
                self.status_update.emit(f"Saved: {fname}")
            except Exception:
                pass

        return saved
