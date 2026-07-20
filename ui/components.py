from PySide6.QtWidgets import (QTableView, QAbstractItemView, QWidget, QVBoxLayout, 
                               QLabel, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QHeaderView, QPushButton, QStyledItemDelegate, QComboBox)
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, Signal, QThread, QUrl
from PySide6.QtGui import QColor, QPixmap, QImage, QDesktopServices
import fitz

class PreviewWorker(QThread):
    preview_ready = Signal(QImage, str) # image, info_text
    preview_failed = Signal(str) # error message
    
    def __init__(self, filepath, filename, doc_pages, doc_size):
        super().__init__()
        self.filepath = filepath
        self.filename = filename
        self.doc_pages = doc_pages
        self.doc_size = doc_size
        self.is_cancelled = False
        
    def cancel(self):
        self.is_cancelled = True
        
    def run(self):
        try:
            doc = fitz.open(self.filepath)
            if doc.page_count > 0:
                page = doc[0]
                mat = fitz.Matrix(1.5, 1.5)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                
                if self.is_cancelled:
                    doc.close()
                    return
                    
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                info_text = f"Preview: {self.filename} (Pages: {self.doc_pages}, Size: {self.doc_size})"
                self.preview_ready.emit(img, info_text)
            doc.close()
        except Exception as e:
            if not self.is_cancelled:
                self.preview_failed.emit(f"Error loading preview: {str(e)}")

class FileTableModel(QAbstractTableModel):
    def __init__(self, pdf_manager):
        super().__init__()
        self.pdf_manager = pdf_manager
        self.headers = ["Print", "File Name", "Date", "Pages", "Size", "Copies", "Duplex", "Status"]
        
    def rowCount(self, parent=QModelIndex()):
        return len(self.pdf_manager.files)
        
    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)
        
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
            
        file = self.pdf_manager.get_file(index.row())
        if not file: return None
        
        col = index.column()
        
        if role == Qt.DisplayRole or role == Qt.EditRole:
            if col == 1: return file.filename
            elif col == 2: return file.date_str
            elif col == 3: return file.custom_pages
            elif col == 4: return file.get_size_str()
            elif col == 5: return str(file.copies)
            elif col == 6: return file.duplex
            elif col == 7: return file.status
            
        elif role == Qt.CheckStateRole and col == 0:
            return Qt.Checked if file.is_selected else Qt.Unchecked
            
        elif role == Qt.ForegroundRole:
            if file.status.startswith("Failed"):
                return QColor("red")
            elif file.status == "Completed":
                return QColor("green")
            elif file.status == "Printing":
                return QColor("blue")
                
        elif role == Qt.TextAlignmentRole:
            if col in [3, 4, 5, 6, 7]:
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter
            
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid(): return False
        file = self.pdf_manager.get_file(index.row())
        col = index.column()
        
        if role == Qt.CheckStateRole and col == 0:
            file.is_selected = (value == Qt.Checked.value)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
            
        if role == Qt.EditRole:
            if col == 3:
                file.custom_pages = str(value)
                self.dataChanged.emit(index, index, [Qt.DisplayRole])
                return True
            elif col == 5:
                try:
                    v = int(value)
                    if v > 0:
                        file.copies = v
                        self.dataChanged.emit(index, index, [Qt.DisplayRole])
                        return True
                except ValueError:
                    pass
            elif col == 6:
                file.duplex = str(value)
                self.dataChanged.emit(index, index, [Qt.DisplayRole])
                return True
                
        return False

    def flags(self, index):
        flags = super().flags(index)
        if index.column() == 0:
            flags |= Qt.ItemIsUserCheckable
        elif index.column() in [3, 5, 6]:
            flags |= Qt.ItemIsEditable
        return flags

    def refresh(self):
        self.layoutAboutToBeChanged.emit()
        self.layoutChanged.emit()

class FileTableView(QTableView):
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            
    def dragMoveEvent(self, event):
        event.acceptProposedAction()
        
    def dropEvent(self, event):
        urls = event.mimeData().urls()
        paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
        event.acceptProposedAction()

class PreviewWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.info_label = QLabel("Select a file to preview")
        self.info_label.setAlignment(Qt.AlignCenter)
        
        self.view = QGraphicsView()
        self.scene = QGraphicsScene()
        self.view.setScene(self.scene)
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)
        
        self.btn_view = QPushButton("View File")
        self.btn_view.setEnabled(False)
        self.btn_view.clicked.connect(self.on_view_clicked)
        
        self.layout.addWidget(self.info_label)
        self.layout.addWidget(self.view)
        self.layout.addWidget(self.btn_view)
        
        self.current_filepath = None
        self.worker = None

    def on_view_clicked(self):
        if self.current_filepath and self.current_filepath.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_filepath)))
        
    def set_file(self, pdf_file):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            
        if not pdf_file or not pdf_file.filepath.exists():
            self.info_label.setText("No file selected or file not found")
            self.pixmap_item.setPixmap(QPixmap())
            self.current_filepath = None
            self.btn_view.setEnabled(False)
            return
            
        if self.current_filepath == pdf_file.filepath:
            return
            
        self.current_filepath = pdf_file.filepath
        self.btn_view.setEnabled(True)
        self.info_label.setText("Loading preview...")
        self.pixmap_item.setPixmap(QPixmap())
        
        self.worker = PreviewWorker(self.current_filepath, pdf_file.filename, pdf_file.pages, pdf_file.paper_size)
        self.worker.preview_ready.connect(self.on_preview_ready)
        self.worker.preview_failed.connect(self.on_preview_failed)
        self.worker.start()
        
    def on_preview_ready(self, img, info_text):
        pixmap = QPixmap.fromImage(img)
        self.pixmap_item.setPixmap(pixmap)
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        self.info_label.setText(info_text)
        
    def on_preview_failed(self, err_msg):
        self.info_label.setText(err_msg)
        self.pixmap_item.setPixmap(QPixmap())

class ComboBoxDelegate(QStyledItemDelegate):
    def __init__(self, parent=None, items=None):
        super().__init__(parent)
        self.items = items or []

    def createEditor(self, parent, option, index):
        editor = QComboBox(parent)
        editor.addItems(self.items)
        return editor

    def setEditorData(self, editor, index):
        value = index.model().data(index, Qt.EditRole)
        idx = editor.findText(value)
        if idx >= 0:
            editor.setCurrentIndex(idx)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)
