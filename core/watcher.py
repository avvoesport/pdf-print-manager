import os
import time
import zipfile
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QThread

class DownloadsWatcher(QThread):
    files_found = Signal(list) # Emits list of file paths added

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = True
        self.enabled = False
        self.downloads_dir = Path(os.path.expanduser("~/Downloads"))
        self.processed_files = set()
        self._initialized = False

    def run(self):
        # Pre-populate processed files on background thread (avoids UI freeze)
        if not self._initialized:
            if self.downloads_dir.exists():
                try:
                    for f in self.downloads_dir.iterdir():
                        if f.is_file():
                            self.processed_files.add(f.name)
                except Exception:
                    pass
            self._initialized = True
        
        while self.running:
            if self.enabled:
                try:
                    self.check_downloads()
                except Exception as e:
                    print(f"Watcher error: {e}")
            time.sleep(2)

    def check_downloads(self):
        if not self.downloads_dir.exists():
            return
            
        new_pdfs = []
        for f in self.downloads_dir.iterdir():
            if not f.is_file():
                continue
                
            if f.name in self.processed_files:
                continue
                
            # Ignore active downloads
            if f.name.endswith('.crdownload') or f.name.endswith('.part') or f.name.endswith('.tmp'):
                continue

            if f.suffix.lower() == '.pdf':
                # Wait for file to settle (size not changing)
                try:
                    size1 = f.stat().st_size
                    time.sleep(0.5)
                    size2 = f.stat().st_size
                    if size1 != size2 or size1 == 0:
                        continue
                except Exception:
                    continue
                
                new_pdfs.append(str(f))
                self.processed_files.add(f.name)
                
            elif f.suffix.lower() == '.zip' and 'whatsapp' in f.name.lower():
                # Wait for file to settle
                try:
                    size1 = f.stat().st_size
                    time.sleep(0.5)
                    size2 = f.stat().st_size
                    if size1 != size2 or size1 == 0:
                        continue
                except Exception:
                    continue
                    
                # Extract zip
                extracted_pdfs = self.extract_whatsapp_zip(f)
                new_pdfs.extend(extracted_pdfs)
                self.processed_files.add(f.name)
            else:
                # Mark other files as processed so we don't keep checking them
                self.processed_files.add(f.name)
                
        if new_pdfs:
            self.files_found.emit(new_pdfs)

    def extract_whatsapp_zip(self, zip_path):
        pdfs = []
        extract_dir = self.downloads_dir / f"WhatsApp_Extracted_{int(time.time())}"
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Only extract pdfs
                pdf_infos = [info for info in zip_ref.infolist() if info.filename.lower().endswith('.pdf')]
                if pdf_infos:
                    extract_dir.mkdir(exist_ok=True)
                    for info in pdf_infos:
                        zip_ref.extract(info, extract_dir)
                        pdfs.append(str(extract_dir / info.filename))
        except Exception as e:
            print(f"Error extracting zip: {e}")
            
        return pdfs
        
    def stop(self):
        self.running = False
        self.wait()
