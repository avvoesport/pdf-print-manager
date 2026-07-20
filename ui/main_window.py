from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                               QPushButton, QSplitter, QGroupBox, QComboBox, 
                               QSpinBox, QDoubleSpinBox, QRadioButton, QLineEdit, QLabel, 
                               QFileDialog, QProgressBar, QMessageBox, QHeaderView,
                               QMenu, QApplication, QCheckBox)
from PySide6.QtCore import Qt, QSortFilterProxyModel, QUrl, QThread, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtPrintSupport import QPrinterInfo, QPrinter
import qdarktheme
import datetime
import os
import json
import urllib.request
import urllib.error
import webbrowser

from core.pdf_manager import PDFManager
from core.database import DatabaseManager
from core.print_worker import PrintWorker
from core.watcher import DownloadsWatcher
from utils.settings import AppSettings
from ui.components import FileTableView, FileTableModel, PreviewWidget, ComboBoxDelegate

VERSION = "v1.1.0"


class PrinterInfoWorker(QThread):
    """Background worker to fetch printer paper sources without blocking the UI."""
    sources_ready = Signal(list)  # emits list of (name, id) tuples

    def __init__(self, printer_name):
        super().__init__()
        self.printer_name = printer_name

    def run(self):
        try:
            from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
            info = QPrinterInfo.printerInfo(self.printer_name)
            p = QPrinter(info)
            sources = p.supportedPaperSources()

            native_trays = {}
            try:
                from utils.win_printer import get_printer_trays
                trays = get_printer_trays(self.printer_name)
                for t in trays:
                    native_trays[t["id"]] = t["name"]
            except Exception:
                pass

            result = []
            if not sources:
                result.append(("Auto", 6))
            else:
                for s in sources:
                    val = s.value
                    name = native_trays.get(val)
                    if not name:
                        name = str(s).split('.')[-1].replace('>', '')
                        if name.isdigit():
                            num = int(name)
                            if num == 257: name = "Tray 1"
                            elif num == 258: name = "Tray 2"
                            elif num == 259: name = "Tray 3"
                            elif num == 260: name = "Tray 4"
                            elif num == 261: name = "Bypass Tray"
                            elif num == 262: name = "Tray 6"
                            else: name = f"Tray {num - 256}"
                    result.append((name, val))
            self.sources_ready.emit(result)
        except Exception:
            self.sources_ready.emit([("Auto", 6)])


class AddFilesWorker(QThread):
    """Background worker to analyze and add PDF files without blocking the UI."""
    done = Signal()

    def __init__(self, pdf_manager, paths):
        super().__init__()
        self.pdf_manager = pdf_manager
        self.paths = paths

    def run(self):
        for path in self.paths:
            if os.path.isdir(path):
                self.pdf_manager.add_folder(path)
            elif path.lower().endswith('.pdf'):
                self.pdf_manager.add_file(path)
        self.done.emit()


class RefreshWorker(QThread):
    """Background worker to re-analyze all loaded PDF files."""
    done = Signal()

    def __init__(self, files):
        super().__init__()
        self.files = files

    def run(self):
        for f in self.files:
            f._analyze()
            if f.status == "Completed" or f.status.startswith("Failed"):
                f.status = "Ready" if f.pages > 0 else "Failed"
        self.done.emit()


class UpdateCheckWorker(QThread):
    """Background worker to check GitHub for a new release."""
    update_ready = Signal(str)   # latest_version string
    up_to_date = Signal(str)     # current version string
    check_failed = Signal(str, int)  # error message, http code (0 if not http)

    def __init__(self, version):
        super().__init__()
        self.version = version

    def run(self):
        try:
            import urllib.request, urllib.error, json
            url = "https://api.github.com/repos/avvoesport/pdf-print-manager/releases/latest"
            req = urllib.request.Request(url, headers={'User-Agent': 'PDF-Print-Manager-Updater'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
            latest = data.get("tag_name", "")
            if latest and latest != self.version:
                self.update_ready.emit(latest)
            else:
                self.up_to_date.emit(self.version)
        except urllib.error.HTTPError as e:
            self.check_failed.emit(str(e), e.code)
        except Exception as e:
            self.check_failed.emit(str(e), 0)

class FileFilterProxyModel(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.filter_today = False
        self.today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
    def filterAcceptsRow(self, source_row, source_parent):
        # Text filter
        model = self.sourceModel()
        idx_name = model.index(source_row, 1, source_parent)
        name = model.data(idx_name, Qt.DisplayRole)
        
        text_match = True
        if self.filterRegularExpression().pattern():
            text_match = self.filterRegularExpression().match(name).hasMatch()
            
        # Date filter
        date_match = True
        if self.filter_today:
            idx_date = model.index(source_row, 2, source_parent)
            date_str = model.data(idx_date, Qt.DisplayRole)
            if not date_str.startswith(self.today_str):
                date_match = False
                
        return text_match and date_match

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Print Manager")
        self.resize(1000, 700)
        
        self.settings = AppSettings()
        self.db = DatabaseManager()
        self.pdf_manager = PDFManager()
        
        geom = self.settings.get_window_geometry()
        if geom:
            self.restoreGeometry(geom)
            
        self.setup_ui()
        self.load_settings_to_ui()
        
        self.print_worker = None

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)
        
        # Left Panel (File List)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        # Toolbar
        toolbar_layout = QHBoxLayout()
        btn_add_files = QPushButton("Add Files")
        btn_add_folder = QPushButton("Add Folder")
        btn_remove = QPushButton("Remove")
        btn_remove_all = QPushButton("Remove All")
        
        self.btn_theme = QPushButton("Toggle Theme")
        self.check_today = QCheckBox("Today Only")
        self.check_auto_import = QCheckBox("Auto-Import Downloads")
        
        btn_select_all = QPushButton("Select All")
        btn_deselect_all = QPushButton("Deselect All")
        btn_refresh = QPushButton("Refresh Status")
        
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search files...")
        
        toolbar_layout.addWidget(btn_add_files)
        toolbar_layout.addWidget(btn_add_folder)
        toolbar_layout.addWidget(btn_remove)
        toolbar_layout.addWidget(btn_remove_all)
        toolbar_layout.addWidget(btn_select_all)
        toolbar_layout.addWidget(btn_deselect_all)
        toolbar_layout.addWidget(btn_refresh)
        
        self.btn_update = QPushButton("Check for Updates")
        self.btn_update.setStyleSheet("font-weight: bold; color: #ffffff; background-color: #007bff; border: none; padding: 5px 10px;")
        self.btn_update.clicked.connect(self.check_for_updates)
        toolbar_layout.addWidget(self.btn_update)
        
        toolbar_layout.addWidget(self.btn_theme)
        toolbar_layout.addWidget(self.check_today)
        toolbar_layout.addWidget(self.check_auto_import)
        toolbar_layout.addWidget(self.search_bar)
        left_layout.addLayout(toolbar_layout)
        
        # Table
        self.table_view = FileTableView()
        self.table_model = FileTableModel(self.pdf_manager)
        
        # Proxy Model for Sorting and Filtering
        self.proxy_model = FileFilterProxyModel()
        self.proxy_model.setSourceModel(self.table_model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)
        
        self.table_view.setModel(self.proxy_model)
        self.table_view.setSortingEnabled(True)
        self.table_view.sortByColumn(1, Qt.AscendingOrder)
        self.table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        
        # Adjust columns
        header = self.table_view.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in range(2, 8):
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
            
        # Set Duplex delegate (Column 6)
        duplex_delegate = ComboBoxDelegate(self.table_view, ["Global", "Single Side", "Duplex (Auto)", "Flip on Long Edge", "Flip on Short Edge"])
        self.table_view.setItemDelegateForColumn(6, duplex_delegate)
            
        left_layout.addWidget(self.table_view)
        
        # Right Panel (Settings & Preview)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # Settings Group
        settings_group = QGroupBox("Print Settings")
        settings_layout = QVBoxLayout(settings_group)
        
        # Printer selection
        printers_layout = QHBoxLayout()
        printers_layout.addWidget(QLabel("Printer:"))
        self.combo_printers = QComboBox()
        self.populate_printers()
        printers_layout.addWidget(self.combo_printers)
        settings_layout.addLayout(printers_layout)
        
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("Paper Source:"))
        self.combo_paper_source = QComboBox()
        source_layout.addWidget(self.combo_paper_source)
        settings_layout.addLayout(source_layout)
        
        # Color
        color_layout = QHBoxLayout()
        color_layout.addWidget(QLabel("Color:"))
        self.radio_bw = QRadioButton("Black & White")
        self.radio_color = QRadioButton("Color")
        color_layout.addWidget(self.radio_bw)
        color_layout.addWidget(self.radio_color)
        settings_layout.addLayout(color_layout)
        
        # Duplex
        duplex_layout = QHBoxLayout()
        duplex_layout.addWidget(QLabel("Duplex (Global):"))
        self.combo_duplex = QComboBox()
        self.combo_duplex.addItems(["Single Side", "Duplex (Auto)", "Flip on Long Edge", "Flip on Short Edge"])
        duplex_layout.addWidget(self.combo_duplex)
        settings_layout.addLayout(duplex_layout)
        
        # Paper & Orientation
        po_layout = QHBoxLayout()
        po_layout.addWidget(QLabel("Size:"))
        self.combo_paper = QComboBox()
        self.combo_paper.addItems(["A4", "A3", "Letter", "Legal", "Custom"])
        po_layout.addWidget(self.combo_paper)
        
        self.spin_custom_w = QDoubleSpinBox()
        self.spin_custom_w.setRange(1.0, 2000.0)
        self.spin_custom_w.setSuffix(" mm")
        self.spin_custom_w.setVisible(False)
        po_layout.addWidget(self.spin_custom_w)
        
        self.spin_custom_h = QDoubleSpinBox()
        self.spin_custom_h.setRange(1.0, 2000.0)
        self.spin_custom_h.setSuffix(" mm")
        self.spin_custom_h.setVisible(False)
        po_layout.addWidget(self.spin_custom_h)
        
        po_layout.addWidget(QLabel("Orient:"))
        self.combo_orient = QComboBox()
        self.combo_orient.addItems(["Auto", "Portrait", "Landscape"])
        po_layout.addWidget(self.combo_orient)
        settings_layout.addLayout(po_layout)
        
        # Scaling & Layout
        os_layout = QHBoxLayout()
        os_layout.addWidget(QLabel("Scaling:"))
        self.combo_scaling = QComboBox()
        self.combo_scaling.addItems(["Fit", "Actual Size", "Shrink Oversized"])
        os_layout.addWidget(self.combo_scaling)
        
        layout_mode_layout = QHBoxLayout()
        layout_mode_layout.addWidget(QLabel("Layout:"))
        self.combo_layout = QComboBox()
        self.combo_layout.addItems(["Normal", "Booklet", "2-Up", "4-Up", "6-Up", "9-Up"])
        layout_mode_layout.addWidget(self.combo_layout)
        settings_layout.addLayout(os_layout)
        settings_layout.addLayout(layout_mode_layout)
        
        # Copies
        cp_layout = QHBoxLayout()
        cp_layout.addWidget(QLabel("Copies (Global):"))
        self.spin_copies = QSpinBox()
        self.spin_copies.setMinimum(1)
        self.spin_copies.setMaximum(999)
        cp_layout.addWidget(self.spin_copies)
        settings_layout.addLayout(cp_layout)
        
        right_layout.addWidget(settings_group)
        
        # Preview
        self.preview_widget = PreviewWidget()
        right_layout.addWidget(self.preview_widget)
        
        # Status / Print Bottom
        bottom_layout = QVBoxLayout()
        self.lbl_stats = QLabel("Estimated Files: 0, Pages: 0")
        bottom_layout.addWidget(self.lbl_stats)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        bottom_layout.addWidget(self.progress_bar)
        
        self.lbl_status = QLabel("Ready")
        bottom_layout.addWidget(self.lbl_status)
        
        print_btn_layout = QHBoxLayout()
        self.btn_print = QPushButton("Print Selected")
        self.btn_print.setMinimumHeight(40)
        self.btn_print.setStyleSheet("font-weight: bold; font-size: 14px;")
        print_btn_layout.addStretch()
        print_btn_layout.addWidget(self.btn_print)
        bottom_layout.addLayout(print_btn_layout)
        
        right_layout.addLayout(bottom_layout)
        
        # Add to splitter
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        
        # Signals
        btn_add_files.clicked.connect(self.on_add_files)
        btn_add_folder.clicked.connect(self.on_add_folder)
        btn_remove.clicked.connect(self.on_remove_selected)
        btn_remove_all.clicked.connect(self.on_remove_all)
        btn_select_all.clicked.connect(self.on_select_all)
        btn_deselect_all.clicked.connect(self.on_deselect_all)
        btn_refresh.clicked.connect(self.on_refresh)
        self.btn_theme.clicked.connect(self.on_toggle_theme)
        self.btn_print.clicked.connect(self.on_print_clicked)
        self.search_bar.textChanged.connect(self.proxy_model.setFilterWildcard)
        self.check_today.stateChanged.connect(self.on_today_toggled)
        self.check_auto_import.stateChanged.connect(self.on_auto_import_toggled)
        
        # Start Watcher
        self.watcher = DownloadsWatcher(self)
        self.watcher.files_found.connect(self.on_auto_imported_files)
        self.watcher.start()
        
        self.table_view.selectionModel().selectionChanged.connect(self.on_table_selection_changed)
        self.table_view.customContextMenuRequested.connect(self.on_context_menu)
        self.table_view.filesDropped.connect(self.on_files_dropped)
        self.combo_paper.currentIndexChanged.connect(self.on_paper_changed)
        self.combo_printers.currentIndexChanged.connect(self.on_printer_changed)
        self.table_model.dataChanged.connect(self.update_stats)
        

    def populate_printers(self):
        for printer in QPrinterInfo.availablePrinters():
            self.combo_printers.addItem(printer.printerName())

    def populate_paper_sources(self):
        """Initial synchronous populate (used at startup)."""
        # Trigger async load
        self.on_printer_changed()

    def on_paper_changed(self):
        is_custom = self.combo_paper.currentText() == "Custom"
        self.spin_custom_w.setVisible(is_custom)
        self.spin_custom_h.setVisible(is_custom)

    def on_printer_changed(self):
        """Load paper sources in background so UI doesn't freeze."""
        printer_name = self.combo_printers.currentText()
        if not printer_name:
            return
        self.combo_paper_source.clear()
        self.combo_paper_source.addItem("Loading...", None)
        self.combo_paper_source.setEnabled(False)

        self._printer_info_worker = PrinterInfoWorker(printer_name)
        self._printer_info_worker.sources_ready.connect(self._on_sources_ready)
        self._printer_info_worker.start()

    def _on_sources_ready(self, sources):
        """Called from background thread result — safe to update UI."""
        self.combo_paper_source.clear()
        for name, val in sources:
            self.combo_paper_source.addItem(name, val)
        self.combo_paper_source.setEnabled(True)
        # Restore saved paper source if pending
        pending = getattr(self, '_pending_paper_source_id', None)
        if pending is not None:
            idx = self.combo_paper_source.findData(pending)
            if idx >= 0:
                self.combo_paper_source.setCurrentIndex(idx)
            self._pending_paper_source_id = None

    def load_settings_to_ui(self):
        printer = self.settings.get_printer_name()
        if printer:
            idx = self.combo_printers.findText(printer)
            if idx >= 0: self.combo_printers.setCurrentIndex(idx)
            
        color = self.settings.get_color_mode()
        if color == "Color": self.radio_color.setChecked(True)
        else: self.radio_bw.setChecked(True)
        
        self.combo_duplex.setCurrentText(self.settings.get_duplex_mode())
        self.combo_paper.setCurrentText(self.settings.get_paper_size())
        self.spin_custom_w.setValue(self.settings.get_custom_width())
        self.spin_custom_h.setValue(self.settings.get_custom_height())
        self.on_paper_changed()
        self.combo_orient.setCurrentText(self.settings.get_orientation())
        self.combo_scaling.setCurrentText(self.settings.get_scaling())
        self.combo_layout.setCurrentText(self.settings.get_layout_mode())
        self.spin_copies.setValue(self.settings.get_copies())
        
        self.populate_paper_sources()
        # Paper source is restored asynchronously after sources load
        self._pending_paper_source_id = self.settings.get_paper_source_id()
        
        # Apply Theme
        theme = self.settings.get_theme()
        if theme not in ["light", "dark"]:
            theme = "light"
        QApplication.instance().setStyleSheet(qdarktheme.load_stylesheet(theme))

        # Auto import
        auto_import = self.settings.get_auto_import()
        self.check_auto_import.setChecked(auto_import)
        if hasattr(self, 'watcher'):
            self.watcher.enabled = auto_import

    def save_settings_from_ui(self):
        self.settings.set_printer_name(self.combo_printers.currentText())
        self.settings.set_color_mode("Color" if self.radio_color.isChecked() else "Black & White")
        self.settings.set_duplex_mode(self.combo_duplex.currentText())
        self.settings.set_paper_size(self.combo_paper.currentText())
        self.settings.set_custom_width(self.spin_custom_w.value())
        self.settings.set_custom_height(self.spin_custom_h.value())
        
        val = self.combo_paper_source.currentData()
        if val is not None:
            self.settings.set_paper_source_id(val)
            
        self.settings.set_orientation(self.combo_orient.currentText())
        self.settings.set_scaling(self.combo_scaling.currentText())
        self.settings.set_layout_mode(self.combo_layout.currentText())
        self.settings.set_copies(self.spin_copies.value())

    def on_toggle_theme(self):
        current_theme = self.settings.get_theme()
        new_theme = "dark" if current_theme == "light" else "light"
        self.settings.set_theme(new_theme)
        QApplication.instance().setStyleSheet(qdarktheme.load_stylesheet(new_theme))

    def on_today_toggled(self, state):
        self.proxy_model.filter_today = (state == Qt.Checked.value)
        self.proxy_model.invalidateFilter()

    def on_select_all(self):
        for row in range(self.proxy_model.rowCount()):
            idx = self.proxy_model.index(row, 0)
            self.proxy_model.setData(idx, Qt.Checked.value, Qt.CheckStateRole)
            
    def on_deselect_all(self):
        for row in range(self.proxy_model.rowCount()):
            idx = self.proxy_model.index(row, 0)
            self.proxy_model.setData(idx, Qt.Unchecked.value, Qt.CheckStateRole)

    def on_refresh(self):
        self.lbl_status.setText("Refreshing files...")
        self._refresh_worker = RefreshWorker(list(self.pdf_manager.files))
        self._refresh_worker.done.connect(self._on_refresh_done)
        self._refresh_worker.start()

    def _on_refresh_done(self):
        self.table_model.refresh()
        self.update_stats()
        self.lbl_status.setText("Ready")

    def on_add_files(self):
        last_folder = self.settings.get_last_folder()
        files, _ = QFileDialog.getOpenFileNames(self, "Select PDFs", last_folder, "PDF Files (*.pdf)")
        if files:
            self.settings.set_last_folder(os.path.dirname(files[0]))
            self.add_paths(files)

    def on_add_folder(self):
        last_folder = self.settings.get_last_folder()
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", last_folder)
        if folder:
            self.settings.set_last_folder(folder)
            self.add_paths([folder])
            
    def on_files_dropped(self, paths):
        self.add_paths(paths)

    def add_paths(self, paths):
        """Add files in background to avoid freezing the UI."""
        self.lbl_status.setText("Adding files...")
        self._add_worker = AddFilesWorker(self.pdf_manager, paths)
        self._add_worker.done.connect(self._on_add_done)
        self._add_worker.start()

    def _on_add_done(self):
        self.table_model.refresh()
        self.update_stats()
        self.lbl_status.setText("Ready")

    def on_remove_selected(self):
        indexes = self.table_view.selectionModel().selectedRows()
        # Convert proxy indexes to source indexes
        source_indexes = [self.proxy_model.mapToSource(i) for i in indexes]
        for index in sorted(source_indexes, key=lambda x: x.row(), reverse=True):
            self.pdf_manager.remove_file(index.row())
        self.table_model.refresh()
        self.update_stats()
        self.preview_widget.set_file(None)

    def on_remove_all(self):
        self.pdf_manager.remove_all()
        self.table_model.refresh()
        self.update_stats()
        self.preview_widget.set_file(None)

    def on_table_selection_changed(self):
        indexes = self.table_view.selectionModel().selectedRows()
        if indexes:
            proxy_idx = indexes[0]
            source_idx = self.proxy_model.mapToSource(proxy_idx)
            pdf_file = self.pdf_manager.get_file(source_idx.row())
            self.preview_widget.set_file(pdf_file)

    def on_context_menu(self, pos):
        indexes = self.table_view.selectionModel().selectedRows()
        if not indexes:
            return
            
        menu = QMenu()
        open_action = menu.addAction("Open File")
        folder_action = menu.addAction("Open Folder")
        menu.addSeparator()
        remove_action = menu.addAction("Remove")
        
        action = menu.exec_(self.table_view.viewport().mapToGlobal(pos))
        
        if action == open_action:
            for idx in indexes:
                source_idx = self.proxy_model.mapToSource(idx)
                f = self.pdf_manager.get_file(source_idx.row())
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(f.filepath)))
        elif action == folder_action:
            source_idx = self.proxy_model.mapToSource(indexes[0])
            f = self.pdf_manager.get_file(source_idx.row())
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(f.filepath.parent)))
        elif action == remove_action:
            self.on_remove_selected()

    def update_stats(self):
        selected_files = 0
        total_pages = 0
        for f in self.pdf_manager.files:
            if f.is_selected:
                selected_files += 1
                total_pages += f.pages * f.copies
        self.lbl_stats.setText(f"Estimated Files: {selected_files}, Pages: {total_pages}")

    def on_print_clicked(self):
        if self.btn_print.text() == "Cancel Print":
            if self.print_worker:
                self.print_worker.cancel()
                self.btn_print.setEnabled(False)
                self.lbl_status.setText("Cancelling...")
            return

        files_to_print = []
        for i, f in enumerate(self.pdf_manager.files):
            if f.is_selected and f.status != "Printing" and f.status != "Completed":
                files_to_print.append((i, f))
                
        if not files_to_print:
            QMessageBox.information(self, "Info", "No files selected or all selected files are already printed.")
            return
            
        self.save_settings_from_ui()
        
        # Prepare settings dict for worker
        settings_dict = {
            "printer": self.combo_printers.currentText(),
            "duplex": self.combo_duplex.currentText(),
            "color_mode": "Color" if self.radio_color.isChecked() else "Black & White",
            "paper_size": self.combo_paper.currentText(),
            "custom_width": self.spin_custom_w.value(),
            "custom_height": self.spin_custom_h.value(),
            "paper_source_id": self.combo_paper_source.currentData(),
            "orientation": self.combo_orient.currentText(),
            "scaling": self.combo_scaling.currentText(),
            "layout_mode": self.combo_layout.currentText(),
        }
        
        # Apply global copies if spinbox > 1 and per-file copies == 1
        # To avoid confusion, we'll set all selected file copies to spin_copies value if changed
        global_c = self.spin_copies.value()
        if global_c > 1:
            for i, f in files_to_print:
                if f.copies == 1: # only override if not customized per file
                    f.copies = global_c
            self.table_model.refresh()
            self.update_stats()
            
        self.btn_print.setText("Cancel Print")
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(len(files_to_print))
        
        self.print_worker = PrintWorker(files_to_print, settings_dict, self.db)
        self.print_worker.file_started.connect(self.on_print_file_started)
        self.print_worker.file_completed.connect(self.on_print_file_completed)
        self.print_worker.file_failed.connect(self.on_print_file_failed)
        self.print_worker.progress_updated.connect(self.on_print_progress)
        self.print_worker.job_finished.connect(self.on_print_finished)
        self.print_worker.start()

    def on_print_file_started(self, filename, index):
        self.lbl_status.setText(f"Printing: {filename}")
        f = self.pdf_manager.get_file(index)
        if f: f.status = "Printing"
        self.table_model.dataChanged.emit(self.table_model.index(index, 5), self.table_model.index(index, 5))

    def on_print_file_completed(self, filename, index):
        f = self.pdf_manager.get_file(index)
        if f: f.status = "Completed"
        self.table_model.dataChanged.emit(self.table_model.index(index, 5), self.table_model.index(index, 5))

    def on_print_file_failed(self, filename, index, error):
        f = self.pdf_manager.get_file(index)
        if f: f.status = f"Failed: {error}"
        self.table_model.dataChanged.emit(self.table_model.index(index, 5), self.table_model.index(index, 5))

    def on_print_progress(self, current, total):
        self.progress_bar.setValue(current)
        self.lbl_status.setText(f"Printing {current} / {total}")

    def on_print_finished(self):
        self.btn_print.setText("Print Selected")
        self.btn_print.setEnabled(True)
        self.lbl_status.setText("Print Job Finished")
        self.print_worker = None

    def on_auto_import_toggled(self, state):
        enabled = (state == Qt.Checked.value)
        self.settings.set_auto_import(enabled)
        if hasattr(self, 'watcher'):
            self.watcher.enabled = enabled

    def on_auto_imported_files(self, paths):
        self.add_paths(paths)
        self.lbl_status.setText(f"Auto-imported {len(paths)} file(s) from Downloads.")

    def check_for_updates(self):
        self.btn_update.setEnabled(False)
        self.btn_update.setText("Checking...")
        self._update_worker = UpdateCheckWorker(VERSION)
        self._update_worker.update_ready.connect(self._on_update_available)
        self._update_worker.up_to_date.connect(self._on_up_to_date)
        self._update_worker.check_failed.connect(self._on_update_failed)
        self._update_worker.finished.connect(lambda: (
            self.btn_update.setEnabled(True),
            self.btn_update.setText("Check for Updates")
        ))
        self._update_worker.start()

    def _on_update_available(self, latest_version):
        reply = QMessageBox.question(
            self, 'Update Available',
            f"A new version ({latest_version}) is available!\nYou are currently running {VERSION}.\n\nDo you want to download the update?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            webbrowser.open("https://github.com/avvoesport/pdf-print-manager/releases/latest")

    def _on_up_to_date(self, version):
        QMessageBox.information(self, 'Up to Date', f"You are running the latest version ({version}).")

    def _on_update_failed(self, error_msg, code):
        if code == 404:
            QMessageBox.warning(self, 'Update Check Failed',
                "Could not find any releases.\n\nThis usually happens if:\n"
                "1. You haven't created a Release on GitHub yet.\n"
                "2. Your repository is Private (needs to be Public for the updater).")
        else:
            QMessageBox.warning(self, 'Update Check Failed', f"Could not check for updates:\n{error_msg}")

    def closeEvent(self, event):
        self.settings.save_window_geometry(self.saveGeometry())
        self.save_settings_from_ui()
        if hasattr(self, 'watcher'):
            self.watcher.stop()
        if self.print_worker and self.print_worker.isRunning():
            self.print_worker.cancel()
            self.print_worker.wait()
        super().closeEvent(event)
