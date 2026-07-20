import os
import datetime
import fitz  # PyMuPDF
from pathlib import Path

class PDFFile:
    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.filename = self.filepath.name
        self.date_modified = None
        self.date_str = ""
        self.pages = 0
        self.custom_pages = ""
        self.paper_size = "Unknown"
        self.size_bytes = 0
        self.copies = 1
        self.duplex = "Global"
        self.status = "Ready"
        self.is_selected = True
        
        self._analyze()

    def _analyze(self):
        try:
            stat = self.filepath.stat()
            self.size_bytes = stat.st_size
            self.date_modified = datetime.datetime.fromtimestamp(stat.st_mtime)
            self.date_str = self.date_modified.strftime("%Y-%m-%d %H:%M")
            doc = fitz.open(self.filepath)
            self.pages = doc.page_count
            
            if self.pages == 1:
                self.custom_pages = "1"
            elif self.pages > 1:
                self.custom_pages = f"1-{self.pages}"
            else:
                self.custom_pages = ""
                
            if self.pages > 0:
                # Analyze first page size to determine paper format roughly
                page = doc[0]
                rect = page.rect
                width, height = rect.width, rect.height
                # convert points (1/72 inch) to mm roughly
                w_mm, h_mm = (width / 72.0) * 25.4, (height / 72.0) * 25.4
                # ensure width < height for comparison
                if w_mm > h_mm:
                    w_mm, h_mm = h_mm, w_mm
                
                # Check standard sizes roughly
                if abs(w_mm - 210) < 5 and abs(h_mm - 297) < 5:
                    self.paper_size = "A4"
                elif abs(w_mm - 297) < 5 and abs(h_mm - 420) < 5:
                    self.paper_size = "A3"
                elif abs(w_mm - 216) < 5 and abs(h_mm - 279) < 5:
                    self.paper_size = "Letter"
                elif abs(w_mm - 216) < 5 and abs(h_mm - 356) < 5:
                    self.paper_size = "Legal"
                elif abs(w_mm - 148) < 5 and abs(h_mm - 210) < 5:
                    self.paper_size = "A5"
                else:
                    self.paper_size = "Custom"
            doc.close()
        except Exception as e:
            self.status = "Failed"
            self.pages = 0
            self.paper_size = "Error"
            
    def get_size_str(self):
        kb = self.size_bytes / 1024
        if kb > 1024:
            mb = kb / 1024
            return f"{mb:.1f} MB"
        return f"{kb:.1f} KB"

class PDFManager:
    def __init__(self):
        self.files = []
        self.filepaths_set = set()

    def add_file(self, filepath):
        if filepath in self.filepaths_set:
            return False, "Duplicate"
        pdf = PDFFile(filepath)
        self.files.append(pdf)
        self.filepaths_set.add(filepath)
        return True, "Added"

    def add_folder(self, folder_path):
        added = 0
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith('.pdf'):
                    full_path = str(Path(root) / file)
                    success, _ = self.add_file(full_path)
                    if success:
                        added += 1
        return added

    def remove_file(self, index):
        if 0 <= index < len(self.files):
            file = self.files.pop(index)
            self.filepaths_set.remove(str(file.filepath))

    def remove_all(self):
        self.files.clear()
        self.filepaths_set.clear()

    def get_file(self, index):
        if 0 <= index < len(self.files):
            return self.files[index]
        return None
