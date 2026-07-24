import os
import time
from pathlib import Path
from datetime import datetime, date, timedelta
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QProgressBar, QSizePolicy,
    QFrame, QTabWidget, QWidget, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView, QSplitter
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor, QIcon

from core.whatsapp_scraper import WhatsAppScraper, DOWNLOAD_TEMP_DIR


class WhatsAppDialog(QDialog):
    """
    Dialog that lets the user:
    1. Connect to WhatsApp Web (browser runs minimized in the background)
    2. Pick a contact / group and import all PDFs + images
    3. Browse previously downloaded WhatsApp files organised by date
    """
    files_imported = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import from WhatsApp")
        self.setMinimumSize(620, 600)
        self.setModal(True)

        self._scraper = None
        self._contacts = []

        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        # Header
        title = QLabel("📱  WhatsApp Import")
        f = QFont(); f.setPointSize(14); f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        # Status bar
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.StyledPanel)
        sl = QVBoxLayout(status_frame); sl.setContentsMargins(8, 6, 8, 6)
        self.lbl_status = QLabel("Click 'Connect' to open WhatsApp Web (runs in background).")
        self.lbl_status.setWordWrap(True)
        sl.addWidget(self.lbl_status)
        self.lbl_qr_hint = QLabel("")
        self.lbl_qr_hint.setWordWrap(True)
        self.lbl_qr_hint.setStyleSheet("color: #e67e00; font-weight: bold;")
        sl.addWidget(self.lbl_qr_hint)
        layout.addWidget(status_frame)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Tabs: Import | Files History
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)

        # ---- Tab 1: Import ----
        import_tab = QWidget()
        import_layout = QVBoxLayout(import_tab)
        import_layout.setContentsMargins(0, 8, 0, 0)

        lbl_contacts = QLabel("Contacts / Groups:")
        lbl_contacts.setStyleSheet("font-weight: bold;")
        import_layout.addWidget(lbl_contacts)

        self.list_contacts = QListWidget()
        self.list_contacts.setAlternatingRowColors(True)
        self.list_contacts.setMinimumHeight(200)
        self.list_contacts.itemSelectionChanged.connect(self._on_contact_selected)
        import_layout.addWidget(self.list_contacts)

        self.tabs.addTab(import_tab, "📥 Import")

        # ---- Tab 2: Files History ----
        history_tab = QWidget()
        history_layout = QVBoxLayout(history_tab)
        history_layout.setContentsMargins(0, 8, 0, 0)

        lbl_hist = QLabel("Previously downloaded WhatsApp files:")
        lbl_hist.setStyleSheet("font-weight: bold;")
        history_layout.addWidget(lbl_hist)

        self.tree_files = QTreeWidget()
        self.tree_files.setHeaderLabels(["File", "Size", "Type"])
        self.tree_files.setColumnWidth(0, 280)
        self.tree_files.setColumnWidth(1, 70)
        self.tree_files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree_files.setAlternatingRowColors(True)
        history_layout.addWidget(self.tree_files)

        btn_hist_row = QHBoxLayout()
        self.btn_refresh_hist = QPushButton("🔄 Refresh")
        self.btn_refresh_hist.clicked.connect(self._load_file_history)
        btn_hist_row.addWidget(self.btn_refresh_hist)

        self.btn_print_selected = QPushButton("🖨  Print Selected Files")
        self.btn_print_selected.setStyleSheet(
            "font-weight:bold; background:#25D366; color:white; border:none; padding:5px 12px;")
        self.btn_print_selected.clicked.connect(self._on_print_history_selected)
        btn_hist_row.addWidget(self.btn_print_selected)
        history_layout.addLayout(btn_hist_row)

        self.tabs.addTab(history_tab, "📂 File History")

        # Buttons row
        btn_layout = QHBoxLayout()

        self.btn_connect = QPushButton("🔗  Connect WhatsApp")
        self.btn_connect.setMinimumHeight(34)
        self.btn_connect.setStyleSheet("font-weight: bold;")
        self.btn_connect.clicked.connect(self.on_connect)
        btn_layout.addWidget(self.btn_connect)

        self.btn_refresh = QPushButton("🔄  Refresh Chats")
        self.btn_refresh.setMinimumHeight(34)
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.clicked.connect(self.on_refresh_contacts)
        btn_layout.addWidget(self.btn_refresh)

        self.btn_scrape = QPushButton("⬇  Import Files from Selected Chat")
        self.btn_scrape.setMinimumHeight(34)
        self.btn_scrape.setEnabled(False)
        self.btn_scrape.setStyleSheet(
            "font-weight: bold; background-color: #25D366; color: white; border: none;")
        self.btn_scrape.clicked.connect(self.on_scrape)
        btn_layout.addWidget(self.btn_scrape)

        layout.addLayout(btn_layout)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.on_close)
        layout.addWidget(self.btn_close)

        # Load history on open
        self._load_file_history()

    # ------------------------------------------------------------------ #
    # File History
    # ------------------------------------------------------------------ #
    def _load_file_history(self):
        """Populate the tree widget with files grouped by Today / Yesterday / date."""
        self.tree_files.clear()
        if not os.path.isdir(DOWNLOAD_TEMP_DIR):
            return

        allowed = ('.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
        today = date.today()
        yesterday = today - timedelta(days=1)

        groups: dict[str, list] = {}  # label → list of (filepath, size, ext)
        for fname in sorted(os.listdir(DOWNLOAD_TEMP_DIR), reverse=True):
            fpath = os.path.join(DOWNLOAD_TEMP_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            if not fname.lower().endswith(allowed):
                continue
            mdate = date.fromtimestamp(os.path.getmtime(fpath))
            if mdate == today:
                label = "📅  Today"
            elif mdate == yesterday:
                label = "📅  Yesterday"
            else:
                label = f"📅  {mdate.strftime('%d %b %Y')}"
            size_kb = os.path.getsize(fpath) // 1024
            ext = os.path.splitext(fname)[1].upper().lstrip('.')
            groups.setdefault(label, []).append((fpath, size_kb, ext, fname))

        for label, files in groups.items():
            parent = QTreeWidgetItem(self.tree_files, [label, "", ""])
            parent.setExpanded(True)
            font = parent.font(0); font.setBold(True); parent.setFont(0, font)
            for fpath, size_kb, ext, fname in files:
                child = QTreeWidgetItem(parent, [fname, f"{size_kb} KB", ext])
                child.setData(0, Qt.UserRole, fpath)
                if ext == 'PDF':
                    child.setForeground(2, QColor("#e74c3c"))
                else:
                    child.setForeground(2, QColor("#2980b9"))

    def _on_print_history_selected(self):
        """Import selected files from history into Printavvo."""
        paths = []
        for item in self.tree_files.selectedItems():
            fpath = item.data(0, Qt.UserRole)
            if fpath and os.path.isfile(fpath):
                paths.append(fpath)
        if paths:
            self.files_imported.emit(paths)
            self.lbl_status.setText(f"✅ {len(paths)} file(s) sent to Printavvo!")
            self.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------------ #
    # WhatsApp Connect / Scrape
    # ------------------------------------------------------------------ #
    def on_connect(self):
        self.btn_connect.setEnabled(False)
        self.btn_connect.setText("Connecting…")
        self.progress.setVisible(True)
        self.list_contacts.clear()
        self._contacts = []

        self._scraper = WhatsAppScraper()
        self._scraper.status_update.connect(self._on_status)
        self._scraper.login_required.connect(self._on_login_required)
        self._scraper.logged_in.connect(self._on_logged_in)
        self._scraper.contacts_ready.connect(self._on_contacts_ready)
        self._scraper.files_scraped.connect(self._on_files_scraped)
        self._scraper.error.connect(self._on_error)
        self._scraper.start()

    def on_refresh_contacts(self):
        if self._scraper and self._scraper.isRunning():
            self._scraper.request_contacts()
            self.list_contacts.clear()
            self._contacts = []
            self.lbl_status.setText("Refreshing chat list…")
            self.progress.setVisible(True)

    def on_scrape(self):
        selected = self.list_contacts.currentItem()
        if not selected:
            return
        idx = selected.data(Qt.UserRole)
        if self._scraper and self._scraper.isRunning():
            self.btn_scrape.setEnabled(False)
            self.btn_scrape.setText("Importing…")
            self.progress.setVisible(True)
            self.lbl_status.setText(f"Scraping: {selected.text()}…")
            self._scraper.request_scrape(idx)
        else:
            self.lbl_status.setText("Not connected — click 'Connect WhatsApp' first.")

    def on_close(self):
        if self._scraper and self._scraper.isRunning():
            self._scraper.requestInterruption()
            self._scraper.wait(3000)
        self.reject()

    def _on_contact_selected(self):
        has_sel = self.list_contacts.currentItem() is not None
        connected = self._scraper and self._scraper.isRunning()
        self.btn_scrape.setEnabled(has_sel and connected)

    def _on_status(self, msg: str):
        self.lbl_status.setText(msg)

    def _on_login_required(self):
        self.lbl_qr_hint.setText(
            "📷  WhatsApp is opening in a minimized window.\n"
            "Click the taskbar icon and scan the QR code with your phone."
        )

    def _on_logged_in(self):
        self.lbl_qr_hint.setText("✅  Logged in! Browser is running in the background.")
        self.btn_refresh.setEnabled(True)

    def _on_contacts_ready(self, contacts: list):
        self.progress.setVisible(False)
        self._contacts = contacts
        self.list_contacts.clear()
        for c in contacts:
            item = QListWidgetItem(c["name"])
            item.setData(Qt.UserRole, c["index"])
            self.list_contacts.addItem(item)
        self.btn_connect.setText("🔗  Reconnect")
        self.btn_connect.setEnabled(True)
        self.btn_refresh.setEnabled(True)

    def _on_files_scraped(self, paths: list):
        self.progress.setVisible(False)
        self.btn_scrape.setText("⬇  Import Files from Selected Chat")
        self.btn_scrape.setEnabled(True)

        if not paths:
            self.lbl_status.setText(
                "No files found in this chat. "
                "The chat may have no media, or files may already be downloaded."
            )
            return

        allowed = ('.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
        valid = [p for p in paths if p.lower().endswith(allowed)]

        if valid:
            self.files_imported.emit(valid)
            self.lbl_status.setText(
                f"✅ {len(valid)} file(s) added to Printavvo! "
                "Select another chat or check File History tab."
            )
            self._load_file_history()   # refresh history tab
            self.tabs.setTabText(1, f"📂 File History ({len(valid)} new)")
        else:
            self.lbl_status.setText("Files captured but none were printable PDFs or images.")

    def _on_error(self, msg: str):
        self.progress.setVisible(False)
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("🔗  Connect WhatsApp")
        self.lbl_status.setText(f"Error: {msg[:80]}")

    def closeEvent(self, event):
        self.on_close()
        event.accept()
