import os
import time
import shutil
import tempfile
from pathlib import Path
from PySide6.QtCore import QThread, Signal


WHATSAPP_PROFILE_DIR = str(Path.home() / ".printavvo_wa_session")
DOWNLOAD_TEMP_DIR = str(Path.home() / ".printavvo_wa_downloads")


class WhatsAppScraper(QThread):
    """
    Playwright-based WhatsApp Web scraper.
    Runs entirely in a background thread so the UI stays responsive.
    """
    status_update  = Signal(str)            # human-readable status text
    login_required = Signal()               # QR code is on screen
    logged_in      = Signal()               # successfully logged in
    contacts_ready = Signal(list)           # list of {"name": str, "element_index": int}
    files_scraped  = Signal(list)           # list of local file paths (str)
    error          = Signal(str)            # error message

    def __init__(self):
        super().__init__()
        self._browser  = None
        self._page     = None
        self._pw       = None
        self._action   = "login"            # "login" | "list_contacts" | "scrape"
        self._target_chat_index = None
        os.makedirs(WHATSAPP_PROFILE_DIR, exist_ok=True)
        os.makedirs(DOWNLOAD_TEMP_DIR, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Public control methods (called from UI thread)                       #
    # ------------------------------------------------------------------ #
    def request_contacts(self):
        self._action = "list_contacts"

    def request_scrape(self, chat_index: int):
        self._target_chat_index = chat_index
        self._action = "scrape"

    # ------------------------------------------------------------------ #
    # Thread entry point                                                   #
    # ------------------------------------------------------------------ #
    def run(self):
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            self.error.emit("Playwright is not installed.\nRun: pip install playwright && playwright install chromium")
            return

        try:
            with sync_playwright() as pw:
                self._pw = pw
                self.status_update.emit("Launching browser…")

                context = pw.chromium.launch_persistent_context(
                    WHATSAPP_PROFILE_DIR,
                    headless=False,
                    accept_downloads=True,
                    args=["--no-sandbox"],
                    viewport={"width": 1100, "height": 780},
                )
                page = context.pages[0] if context.pages else context.new_page()
                self._page = page

                page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
                self.status_update.emit("Waiting for WhatsApp Web to load…")

                # ---------- Wait for login ----------
                logged = self._wait_for_login(page, PWTimeout)
                if not logged:
                    context.close()
                    return

                self.logged_in.emit()
                self.status_update.emit("Logged in! Loading chats…")

                # ---------- Wait for chat list ----------
                try:
                    page.wait_for_selector("div[aria-label='Chat list']", timeout=20000)
                except PWTimeout:
                    self.error.emit("Chat list did not load. Please try again.")
                    context.close()
                    return

                # ---------- Initial contact load ----------
                contacts = self._get_contacts(page)
                self.contacts_ready.emit(contacts)
                self.status_update.emit(f"Found {len(contacts)} chats. Select one to import.")

                # ---------- Persistent event loop ----------
                # Browser stays open until the dialog is closed (interruption requested)
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
                            self.status_update.emit("No files found in this chat. Try another chat.")

                context.close()

        except Exception as e:
            self.error.emit(f"WhatsApp scraper error:\n{str(e)}")


    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #
    def _wait_for_login(self, page, PWTimeout):
        """Wait up to 120 seconds for the user to scan QR or auto-login."""
        qr_emitted = False
        for _ in range(240):               # 240 × 0.5 s = 120 s
            if self.isInterruptionRequested():
                return False
            # Check if already logged in (chat list visible)
            if page.query_selector("div[aria-label='Chat list']"):
                return True
            # Check if QR code is visible
            if not qr_emitted and page.query_selector("canvas[aria-label='Scan this QR code to link a device']"):
                self.login_required.emit()
                self.status_update.emit("📱 Please scan the QR code with your phone.")
                qr_emitted = True
            time.sleep(0.5)
        self.error.emit("Timed out waiting for WhatsApp login.")
        return False

    def _get_contacts(self, page):
        """Return list of chat names visible in the sidebar."""
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
        """Click the selected chat and download all PDFs and images."""
        downloaded = []
        try:
            items = page.query_selector_all("div[aria-label='Chat list'] > div")
            if chat_index >= len(items):
                self.error.emit("Chat not found. Please refresh the contact list.")
                return []

            items[chat_index].click()
            time.sleep(2)   # let chat load

            self.status_update.emit("Scrolling to load messages…")
            # Scroll up to load more history (up to ~10 times)
            msg_panel = page.query_selector("div[role='application']")
            for _ in range(10):
                if msg_panel:
                    msg_panel.evaluate("el => el.scrollTop = 0")
                    time.sleep(0.8)

            self.status_update.emit("Looking for files…")

            # -------- Download PDFs --------
            pdf_links = page.query_selector_all("a[href*='.pdf'], span[data-testid='document-thumb']")
            for el in pdf_links:
                try:
                    with page.expect_download(timeout=15000) as dl_info:
                        el.click()
                    dl = dl_info.value
                    dest = os.path.join(DOWNLOAD_TEMP_DIR, dl.suggested_filename or f"wa_doc_{int(time.time())}.pdf")
                    dl.save_as(dest)
                    downloaded.append(dest)
                    self.status_update.emit(f"Downloaded: {dl.suggested_filename}")
                except Exception:
                    pass

            # -------- Download images via download button --------
            img_buttons = page.query_selector_all("div[data-testid='media-download-button'], button[aria-label='Download']")
            for btn in img_buttons:
                try:
                    with page.expect_download(timeout=15000) as dl_info:
                        btn.click()
                    dl = dl_info.value
                    fname = dl.suggested_filename or f"wa_img_{int(time.time())}.jpg"
                    if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.pdf')):
                        dest = os.path.join(DOWNLOAD_TEMP_DIR, fname)
                        dl.save_as(dest)
                        downloaded.append(dest)
                        self.status_update.emit(f"Downloaded: {fname}")
                except Exception:
                    pass

        except Exception as e:
            self.error.emit(f"Error while scraping chat:\n{str(e)}")

        return downloaded
