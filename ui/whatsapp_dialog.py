from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QProgressBar, QMessageBox,
    QSizePolicy, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor

from core.whatsapp_scraper import WhatsAppScraper


class WhatsAppDialog(QDialog):
    """
    Dialog that lets the user pick a WhatsApp chat and import
    all PDFs / images from it into Printavvo.
    """
    files_imported = Signal(list)   # emits list of local file paths

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import from WhatsApp")
        self.setMinimumSize(520, 540)
        self.setModal(True)

        self._scraper = None
        self._contacts = []

        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # --- Header ---
        title = QLabel("📱  WhatsApp Import")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        # --- Status bar ---
        status_frame = QFrame()
        status_frame.setFrameShape(QFrame.StyledPanel)
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(8, 6, 8, 6)

        self.lbl_status = QLabel("Click 'Connect' to open WhatsApp Web.")
        self.lbl_status.setWordWrap(True)
        status_layout.addWidget(self.lbl_status)

        self.lbl_qr_hint = QLabel("")
        self.lbl_qr_hint.setWordWrap(True)
        self.lbl_qr_hint.setStyleSheet("color: #e67e00; font-weight: bold;")
        status_layout.addWidget(self.lbl_qr_hint)

        layout.addWidget(status_frame)

        # --- Contact list ---
        list_label = QLabel("Contacts / Groups:")
        list_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(list_label)

        self.list_contacts = QListWidget()
        self.list_contacts.setAlternatingRowColors(True)
        self.list_contacts.setMinimumHeight(220)
        self.list_contacts.itemSelectionChanged.connect(self._on_contact_selected)
        layout.addWidget(self.list_contacts)

        # --- Progress ---
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)    # indeterminate
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # --- Buttons ---
        btn_layout = QHBoxLayout()

        self.btn_connect = QPushButton("🔗  Connect WhatsApp")
        self.btn_connect.setMinimumHeight(36)
        self.btn_connect.setStyleSheet("font-weight: bold;")
        self.btn_connect.clicked.connect(self.on_connect)
        btn_layout.addWidget(self.btn_connect)

        self.btn_refresh = QPushButton("🔄  Refresh Chats")
        self.btn_refresh.setMinimumHeight(36)
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.clicked.connect(self.on_refresh_contacts)
        btn_layout.addWidget(self.btn_refresh)

        self.btn_scrape = QPushButton("⬇  Import Files from Selected Chat")
        self.btn_scrape.setMinimumHeight(36)
        self.btn_scrape.setEnabled(False)
        self.btn_scrape.setStyleSheet("font-weight: bold; background-color: #25D366; color: white; border: none;")
        self.btn_scrape.clicked.connect(self.on_scrape)
        btn_layout.addWidget(self.btn_scrape)

        layout.addLayout(btn_layout)

        # --- Close ---
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.on_close)
        layout.addWidget(self.btn_close)

    # ------------------------------------------------------------------ #
    # Slots / event handlers                                               #
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
            QMessageBox.warning(self, "Not Connected",
                "Please connect to WhatsApp first by clicking 'Connect WhatsApp'.")

    def on_close(self):
        if self._scraper and self._scraper.isRunning():
            self._scraper.requestInterruption()
            self._scraper.wait(3000)
        self.reject()

    def _on_contact_selected(self):
        has_selection = self.list_contacts.currentItem() is not None
        connected = self._scraper and self._scraper.isRunning()
        self.btn_scrape.setEnabled(has_selection and connected)

    def _on_status(self, msg: str):
        self.lbl_status.setText(msg)

    def _on_login_required(self):
        self.lbl_qr_hint.setText("📷  A browser window has opened — scan the QR code with your phone to log in.")

    def _on_logged_in(self):
        self.lbl_qr_hint.setText("✅  Logged in!")
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

        if not paths:
            QMessageBox.information(self, "No Files Found",
                "No PDF or image files were found in this chat.\n"
                "Make sure media has been sent and try scrolling up in the chat first.")
            self.btn_scrape.setEnabled(True)
            return

        # Only accept PDFs and common image types
        allowed = ('.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp')
        valid = [p for p in paths if p.lower().endswith(allowed)]

        if valid:
            self.files_imported.emit(valid)
            QMessageBox.information(self, "Import Complete",
                f"{len(valid)} file(s) have been added to Printavvo!")
            self.accept()
        else:
            QMessageBox.warning(self, "No Printable Files",
                "Files were downloaded but none were printable PDFs or images.")
            self.btn_scrape.setEnabled(True)

    def _on_error(self, msg: str):
        self.progress.setVisible(False)
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("🔗  Connect WhatsApp")
        self.lbl_status.setText("Error — see details below.")
        QMessageBox.critical(self, "WhatsApp Error", msg)

    def closeEvent(self, event):
        self.on_close()
        event.accept()
