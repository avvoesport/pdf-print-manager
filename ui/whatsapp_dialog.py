import os
import time
from pathlib import Path
from datetime import datetime, date, timedelta
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QProgressBar, QSizePolicy,
    QFrame, QTabWidget, QWidget, QTreeWidget, QTreeWidgetItem,
    QAbstractItemView
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor, QIcon

from core.whatsapp_scraper import WhatsAppScraper, DOWNLOAD_TEMP_DIR


class WhatsAppDialog(QDialog):
    """
    Dialog with a 3-step workflow:
    1. Select a Chat and Scan it
    2. View files found in that specific chat and send them to Printavvo
    3. View global file history
    """
    files_imported = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import from WhatsApp")
        self.setMinimumSize(680, 620)
        self.setModal(True)

        self._scraper = None
        self._contacts = []
        self._latest_scraped_files = [] # Keeps track of files from the last scan

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

        # Global Status bar
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

        # Tabs
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)

        # ==================================================================
        # TAB 1: Contacts (Step 1)
        # ==================================================================
        t1 = QWidget()
        l1 = QVBoxLayout(t1)
        l1.setContentsMargins(0, 8, 0, 0)

        lbl_c = QLabel("1. Select a Contact/Group to scan:")
        lbl_c.setStyleSheet("font-weight: bold;")
        l1.addWidget(lbl_c)

        self.list_contacts = QListWidget()
        self.list_contacts.setAlternatingRowColors(True)
        self.list_contacts.itemSelectionChanged.connect(self._on_contact_selected)
        l1.addWidget(self.list_contacts)

        # Tab 1 Buttons
        b1_layout = QHBoxLayout()
        self.btn_connect = QPushButton("🔗  Connect WhatsApp")
        self.btn_connect.setMinimumHeight(34)
        self.btn_connect.setStyleSheet("font-weight: bold;")
        self.btn_connect.clicked.connect(self.on_connect)
        b1_layout.addWidget(self.btn_connect)

        self.btn_refresh = QPushButton("🔄  Refresh Chats")
        self.btn_refresh.setMinimumHeight(34)
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.clicked.connect(self.on_refresh_contacts)
        b1_layout.addWidget(self.btn_refresh)

        self.btn_scan = QPushButton("🔍  Scan Chat for Files")
        self.btn_scan.setMinimumHeight(34)
        self.btn_scan.setEnabled(False)
        self.btn_scan.setStyleSheet("font-weight: bold; background-color: #2980b9; color: white; border: none;")
        self.btn_scan.clicked.connect(self.on_scrape)
        b1_layout.addWidget(self.btn_scan, stretch=1)

        l1.addLayout(b1_layout)
        self.tabs.addTab(t1, "💬 1. Chats")

        # ==================================================================
        # TAB 2: Scraped Files (Step 2)
        # ==================================================================
        t2 = QWidget()
        l2 = QVBoxLayout(t2)
        l2.setContentsMargins(0, 8, 0, 0)

        self.lbl_scraped = QLabel("2. Files found in the selected chat:")
        self.lbl_scraped.setStyleSheet("font-weight: bold;")
        l2.addWidget(self.lbl_scraped)

        self.tree_scraped = QTreeWidget()
        self.tree_scraped.setHeaderLabels(["File", "Size", "Type"])
        self.tree_scraped.setColumnWidth(0, 320)
        self.tree_scraped.setColumnWidth(1, 80)
        self.tree_scraped.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree_scraped.setAlternatingRowColors(True)
        l2.addWidget(self.tree_scraped)

        b2_layout = QHBoxLayout()
        self.btn_print_scraped = QPushButton("🖨  Download & Print Selected")
        self.btn_print_scraped.setMinimumHeight(38)
        self.btn_print_scraped.setStyleSheet("font-weight:bold; background:#25D366; color:white; border:none; padding:5px 12px;")
        self.btn_print_scraped.clicked.connect(self._on_print_scraped)
        self.btn_print_scraped.setEnabled(False)
        b2_layout.addStretch()
        b2_layout.addWidget(self.btn_print_scraped)
        l2.addLayout(b2_layout)
        self.tabs.addTab(t2, "📄 2. Select Files")

        # ==================================================================
        # TAB 3: Global History (Step 3)
        # ==================================================================
        t3 = QWidget()
        l3 = QVBoxLayout(t3)
        l3.setContentsMargins(0, 8, 0, 0)

        lbl_hist = QLabel("All previously downloaded WhatsApp files:")
        lbl_hist.setStyleSheet("font-weight: bold;")
        l3.addWidget(lbl_hist)

        self.tree_hist = QTreeWidget()
        self.tree_hist.setHeaderLabels(["File", "Size", "Type"])
        self.tree_hist.setColumnWidth(0, 320)
        self.tree_hist.setColumnWidth(1, 80)
        self.tree_hist.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree_hist.setAlternatingRowColors(True)
        l3.addWidget(self.tree_hist)

        b3_layout = QHBoxLayout()
        self.btn_refresh_hist = QPushButton("🔄 Refresh History")
        self.btn_refresh_hist.setMinimumHeight(34)
        self.btn_refresh_hist.clicked.connect(self._load_file_history)
        b3_layout.addWidget(self.btn_refresh_hist)

        b3_layout.addStretch()

        self.btn_print_hist = QPushButton("🖨  Send Selected to Printavvo")
        self.btn_print_hist.setMinimumHeight(34)
        self.btn_print_hist.setStyleSheet("font-weight:bold; background:#25D366; color:white; border:none; padding:5px 12px;")
        self.btn_print_hist.clicked.connect(self._on_print_hist)
        b3_layout.addWidget(self.btn_print_hist)

        l3.addLayout(b3_layout)
        self.tabs.addTab(t3, "📂 3. Global History")

        # Global Close
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.on_close)
        layout.addWidget(self.btn_close)

        # Init
        self._load_file_history()

    # ------------------------------------------------------------------ #
    # Data Population
    # ------------------------------------------------------------------ #
    def _populate_tree(self, tree: QTreeWidget, file_paths: list):
        """Helper to populate a tree grouped by date."""
        import re
        tree.clear()
        if not file_paths:
            return

        today = date.today()
        yesterday = today - timedelta(days=1)
        groups: dict[str, list] = {}

        for fpath in file_paths:
            if not os.path.isfile(fpath):
                continue
            fname = os.path.basename(fpath)
            size_kb = os.path.getsize(fpath) // 1024
            ext = os.path.splitext(fname)[1].upper().lstrip('.')
            
            # 1. Default to OS modification time (download time)
            mdate = date.fromtimestamp(os.path.getmtime(fpath))
            
            # 2. Try to parse the original WhatsApp conversation date from the filename
            # e.g., "WhatsApp Image 2026-07-10 at 15.05.12.jpeg"
            wa_match = re.search(r'(?:WhatsApp Image|WhatsApp Video|WhatsApp Document) (\d{4}-\d{2}-\d{2})', fname)
            if wa_match:
                try:
                    mdate = datetime.strptime(wa_match.group(1), "%Y-%m-%d").date()
                except ValueError:
                    pass
            
            if mdate == today:
                label = "📅  Today"
            elif mdate == yesterday:
                label = "📅  Yesterday"
            else:
                label = f"📅  {mdate.strftime('%d %b %Y')}"
                
            groups.setdefault(label, []).append((fpath, size_kb, ext, fname, mdate))

        # Sort groups logically (Today first, then newest to oldest)
        def sort_key(label):
            if "Today" in label: return 0
            if "Yesterday" in label: return 1
            # We want older dates to be sorted properly, but since the label is text "DD MMM YYYY", 
            # we should parse it back or just rely on the fact that we can store the max date in the group.
            return 2

        # A better approach to sort groups by date:
        # Create a list of (label, max_date_in_group)
        group_dates = {}
        for label, items in groups.items():
            if label == "📅  Today": group_dates[label] = today
            elif label == "📅  Yesterday": group_dates[label] = yesterday
            else: group_dates[label] = items[0][4] # mdate is at index 4

        # Sort labels by date descending
        sorted_labels = sorted(groups.keys(), key=lambda l: group_dates[l], reverse=True)

        for label in sorted_labels:
            files = groups[label]
            parent = QTreeWidgetItem(tree, [label, "", ""])
            parent.setExpanded(True)
            font = parent.font(0); font.setBold(True); parent.setFont(0, font)
            
            # Sort files inside group by filename / modified time descending
            files.sort(key=lambda x: os.path.getmtime(x[0]), reverse=True)
            
            for fpath, size_kb, ext, fname, _ in files:
                child = QTreeWidgetItem(parent, [fname, f"{size_kb} KB", ext])
                child.setData(0, Qt.UserRole, fpath)
                if ext == 'PDF':
                    child.setForeground(2, QColor("#e74c3c"))
                else:
                    child.setForeground(2, QColor("#2980b9"))

    def _populate_indexed_tree(self, items: list):
        """Populate Tab 2 with indexed items and checkboxes."""
        import re
        self.tree_scraped.clear()
        if not items:
            return

        today = date.today()
        yesterday = today - timedelta(days=1)
        groups = {}

        for item in items:
            mdate = today
            ds = item.get("date_str")
            if ds:
                ds = ds.upper().strip()
                if ds == "TODAY":
                    mdate = today
                elif ds == "YESTERDAY":
                    mdate = yesterday
                elif re.match(r"^\d{2}/\d{2}/\d{4}$", ds):
                    parts = ds.split('/')
                    mdate = date(int(parts[2]), int(parts[1]), int(parts[0]))
            
            if mdate == today:
                label = "📅  Today"
            elif mdate == yesterday:
                label = "📅  Yesterday"
            else:
                label = f"📅  {mdate.strftime('%d %b %Y')}"
                
            groups.setdefault(label, []).append((item, mdate))

        group_dates = {}
        for label, group_items in groups.items():
            if label == "📅  Today": group_dates[label] = today
            elif label == "📅  Yesterday": group_dates[label] = yesterday
            else: group_dates[label] = group_items[0][1]

        sorted_labels = sorted(groups.keys(), key=lambda l: group_dates[l], reverse=True)

        for label in sorted_labels:
            group_items = groups[label]
            parent = QTreeWidgetItem(self.tree_scraped, [label, "", ""])
            parent.setExpanded(True)
            font = parent.font(0); font.setBold(True); parent.setFont(0, font)
            
            for item, _ in group_items:
                child = QTreeWidgetItem(parent, [item["name"], "", item["type"]])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked)
                child.setData(0, Qt.UserRole, item)
                
                if item.get("is_pdf"):
                    child.setForeground(2, QColor("#e74c3c"))
                else:
                    child.setForeground(2, QColor("#2980b9"))

    def _load_file_history(self):
        """Populate the global history tree."""
        if not os.path.isdir(DOWNLOAD_TEMP_DIR):
            return
        allowed = ('.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')
        paths = [
            os.path.join(DOWNLOAD_TEMP_DIR, f) 
            for f in os.listdir(DOWNLOAD_TEMP_DIR) 
            if f.lower().endswith(allowed)
        ]
        self._populate_tree(self.tree_hist, paths)

    # ------------------------------------------------------------------ #
    # Printing Actions
    # ------------------------------------------------------------------ #
    def _get_selected_paths(self, tree: QTreeWidget):
        paths = []
        for item in tree.selectedItems():
            fpath = item.data(0, Qt.UserRole)
            if fpath and os.path.isfile(fpath):
                paths.append(fpath)
        return paths

    def _on_print_scraped(self):
        items_to_dl = []
        root = self.tree_scraped.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.checkState(0) == Qt.Checked:
                    item_data = child.data(0, Qt.UserRole)
                    if item_data:
                        items_to_dl.append(item_data)
        
        if items_to_dl:
            self.btn_print_scraped.setEnabled(False)
            self.btn_print_scraped.setText("Downloading...")
            self.progress.setVisible(True)
            self._scraper.request_download(items_to_dl)
        else:
            self.lbl_status.setText("No files selected!")

    def _on_print_hist(self):
        paths = self._get_selected_paths(self.tree_hist)
        if paths:
            self.files_imported.emit(paths)
            self.lbl_status.setText(f"✅ {len(paths)} file(s) sent to Printavvo from history!")
            self.accept()

    # ------------------------------------------------------------------ #
    # WhatsApp Scraper Control
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
        self._scraper.files_indexed.connect(self._on_files_indexed)
        self._scraper.files_downloaded.connect(self._on_files_downloaded)
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
        if self._scraper and self._scraper.isRunning():
            self.btn_scan.setEnabled(False)
            self.btn_scan.setText("Scanning…")
            self.progress.setVisible(True)
            
            chat_name = selected.text()
            self.lbl_status.setText(f"Scanning chat: {chat_name}…")
            self.lbl_scraped.setText(f"2. Files found in '{chat_name}':")
            
            self._scraper.request_scrape(chat_name)
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
        self.btn_scan.setEnabled(has_sel and connected)

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

    def _on_files_indexed(self, items: list):
        self.progress.setVisible(False)
        self.btn_scan.setText("🔍  Scan Chat for Files")
        self.btn_scan.setEnabled(True)

        if not items:
            self.lbl_status.setText("No printable files found in this chat.")
            self.tree_scraped.clear()
            self.btn_print_scraped.setEnabled(False)
            return

        self._populate_indexed_tree(items)
        self.btn_print_scraped.setEnabled(True)
        self.lbl_status.setText(f"✅ Found {len(items)} file(s). Please select what to download.")
        self.tabs.setCurrentIndex(1)
        self._load_file_history()

    def _on_files_downloaded(self, paths: list):
        self.progress.setVisible(False)
        self.btn_print_scraped.setText("🖨  Download & Print Selected")
        self.btn_print_scraped.setEnabled(True)
        
        if paths:
            self.files_imported.emit(paths)
            self.lbl_status.setText(f"✅ {len(paths)} file(s) downloaded and sent to Printavvo!")
            self._load_file_history()
            self.accept()
        else:
            self.lbl_status.setText("Failed to download any files.")

    def _on_error(self, msg: str):
        self.progress.setVisible(False)
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("🔗  Connect WhatsApp")
        self.btn_scan.setText("🔍  Scan Chat for Files")
        self.btn_scan.setEnabled(self.list_contacts.currentItem() is not None)
        self.lbl_status.setText(f"Error: {msg[:80]}")

    def closeEvent(self, event):
        self.on_close()
        event.accept()
