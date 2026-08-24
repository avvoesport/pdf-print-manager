import traceback
from PySide6.QtCore import QThread, Signal, Qt, QRectF
from PySide6.QtGui import QPainter, QImage, QPageSize, QPageLayout
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
import fitz

class PrintWorker(QThread):
    progress_updated = Signal(int, int) # current_file, total_files
    file_started = Signal(str, int) # filename, index
    file_completed = Signal(str, int)
    file_failed = Signal(str, int, str) # filename, index, error_msg
    job_finished = Signal()

    def __init__(self, files_to_print, settings, db_manager):
        super().__init__()
        self.files = files_to_print # list of (index, PDFFile)
        self.settings = settings
        self.db_manager = db_manager
        self.is_cancelled = False
        self.is_paused = False

    def generate_booklet_sequence(self, pages_to_print):
        total = len(pages_to_print)
        if total == 0:
            return []
            
        padded_total = (total + 3) // 4 * 4
        padded_pages = pages_to_print + [None] * (padded_total - total)
        
        sequence = []
        for i in range(padded_total // 4):
            sequence.append((padded_pages[padded_total - 1 - i*2], padded_pages[i*2]))
            sequence.append((padded_pages[i*2 + 1], padded_pages[padded_total - 2 - i*2]))
            
        return sequence

    def cancel(self):
        self.is_cancelled = True

    def parse_custom_pages(self, pages_str, max_pages):
        # e.g., "1-5, 7, 9"
        pages = set()
        parts = pages_str.split(',')
        for part in parts:
            part = part.strip()
            if not part: continue
            if '-' in part:
                try:
                    start, end = map(int, part.split('-'))
                    start = max(1, start)
                    end = min(max_pages, end)
                    if start <= end:
                        pages.update(range(start - 1, end))
                except ValueError:
                    pass
            else:
                try:
                    p = int(part)
                    if 1 <= p <= max_pages:
                        pages.add(p - 1)
                except ValueError:
                    pass
        return sorted(list(pages))

    def get_qpagesize(self, size_name):
        if size_name == "Custom":
            w = float(self.settings.get("custom_width", 210.0))
            h = float(self.settings.get("custom_height", 297.0))
            from PySide6.QtCore import QSizeF
            return QPageSize(QSizeF(w, h), QPageSize.Unit.Millimeter)
            
        sizes = {
            "A4": QPageSize.A4,
            "A3": QPageSize.A3,
            "B4": QPageSize.B4,
            "Letter": QPageSize.Letter,
            "Legal": QPageSize.Legal
        }
        return QPageSize(sizes.get(size_name, QPageSize.A4))

    def run(self):
        total_files = len(self.files)
        printer_name = self.settings.get("printer", "")
        if not printer_name:
            self.job_finished.emit()
            return

        # Apply global settings
        color_mode = self.settings.get("color_mode", "Color")
        duplex_mode = self.settings.get("duplex", "Single Side")

        paper_size_name = self.settings.get("paper_size", "A4")
        q_page_size = self.get_qpagesize(paper_size_name)
        paper_source_id = self.settings.get("paper_source_id", 6)
        
        copies_global = int(self.settings.get("copies", 1))

        orientation_mode = self.settings.get("orientation", "Auto")
        scaling_mode = self.settings.get("scaling", "Fit")
        layout_mode = self.settings.get("layout_mode", "Normal")

        for current_idx, (list_index, pdf_file) in enumerate(self.files):
            if self.is_cancelled:
                break
                
            self.file_started.emit(pdf_file.filename, list_index)
            copies = copies_global if copies_global > 1 else pdf_file.copies
            
            try:
                printer = QPrinter(QPrinterInfo.printerInfo(printer_name))
                printer.setResolution(300)
                printer.setCopyCount(copies)
                printer.setColorMode(QPrinter.Color if color_mode == "Color" else QPrinter.GrayScale)
                
                doc = fitz.open(pdf_file.filepath)
                current_duplex = duplex_mode if pdf_file.duplex == "Global" else pdf_file.duplex
                
                if current_duplex == "Duplex (Auto)":
                    is_landscape = False
                    if doc.page_count > 0:
                        first_page = doc[0]
                        if first_page.rect.width > first_page.rect.height:
                            is_landscape = True
                    
                    if is_landscape:
                        printer.setDuplex(QPrinter.DuplexShortSide)
                    else:
                        printer.setDuplex(QPrinter.DuplexLongSide)
                elif current_duplex == "Flip on Long Edge":
                    printer.setDuplex(QPrinter.DuplexLongSide)
                elif current_duplex == "Flip on Short Edge":
                    printer.setDuplex(QPrinter.DuplexShortSide)
                else:
                    printer.setDuplex(QPrinter.DuplexNone)


                pages_to_print = self.parse_custom_pages(pdf_file.custom_pages, doc.page_count)
                if not pages_to_print:
                    pages_to_print = list(range(doc.page_count))
                    
                if len(pages_to_print) == 0:
                    doc.close()
                    self.file_completed.emit(pdf_file.filename, list_index)
                    self.progress_updated.emit(current_idx + 1, total_files)
                    continue

                # We start the print job per file to ensure copies work correctly
                # and if one fails, others can continue.
                if orientation_mode == "Auto":
                    if doc.page_count > 0:
                        first_page = doc[0]
                        if first_page.rect.width > first_page.rect.height:
                            printer.setPageOrientation(QPageLayout.Landscape)
                        else:
                            printer.setPageOrientation(QPageLayout.Portrait)
                elif orientation_mode == "Landscape":
                    printer.setPageOrientation(QPageLayout.Landscape)
                else:
                    printer.setPageOrientation(QPageLayout.Portrait)
                            
                printer.setPageSize(q_page_size)

                sources = printer.supportedPaperSources()
                for s in sources:
                    if s.value == paper_source_id:
                        printer.setPaperSource(s)
                        break

                painter = QPainter()
                if not painter.begin(printer):
                    raise Exception("Could not start QPainter. Printer might be unavailable.")

                if layout_mode == "Booklet":
                    booklet_seq = self.generate_booklet_sequence(pages_to_print)
                    
                    rect = printer.pageRect(QPrinter.DevicePixel)
                    painter.save()
                    if rect.width() < rect.height():
                        logical_w = rect.height()
                        logical_h = rect.width()
                        painter.translate(rect.width(), 0)
                        painter.rotate(90)
                    else:
                        logical_w = rect.width()
                        logical_h = rect.height()
                    
                    for i, (left_idx, right_idx) in enumerate(booklet_seq):
                        if self.is_cancelled: break
                        
                        img_left = None
                        img_right = None
                        zoom = 300 / 72 
                        mat = fitz.Matrix(zoom, zoom)
                        
                        if left_idx is not None:
                            pix = doc[left_idx].get_pixmap(matrix=mat, alpha=False)
                            img_left = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                        if right_idx is not None:
                            pix = doc[right_idx].get_pixmap(matrix=mat, alpha=False)
                            img_right = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                            
                        w_left = img_left.width() if img_left else 0
                        w_right = img_right.width() if img_right else 0
                        h_left = img_left.height() if img_left else 0
                        h_right = img_right.height() if img_right else 0
                        
                        if w_left == 0 and w_right > 0:
                            w_left, h_left = w_right, h_right
                            img_left = QImage(w_left, h_left, QImage.Format_RGB888)
                            img_left.fill(Qt.white)
                        elif w_right == 0 and w_left > 0:
                            w_right, h_right = w_left, h_left
                            img_right = QImage(w_right, h_right, QImage.Format_RGB888)
                            img_right.fill(Qt.white)
                            
                        combined_w = w_left + w_right
                        combined_h = max(h_left, h_right)
                        
                        combined_img = QImage(combined_w, combined_h, QImage.Format_RGB888)
                        combined_img.fill(Qt.white)
                        
                        cmb_painter = QPainter(combined_img)
                        if img_left:
                            cmb_painter.drawImage(0, 0, img_left)
                        if img_right:
                            cmb_painter.drawImage(w_left, 0, img_right)
                        cmb_painter.end()
                        
                        if i > 0:
                            printer.newPage()
                            
                        img_rect = QRectF(combined_img.rect())
                        target_rect = QRectF(0, 0, logical_w, logical_h)
                        
                        scale_factor = min(target_rect.width() / img_rect.width(), target_rect.height() / img_rect.height())
                        new_width = img_rect.width() * scale_factor
                        new_height = img_rect.height() * scale_factor
                        draw_rect = QRectF(
                            target_rect.x() + (target_rect.width() - new_width) / 2,
                            target_rect.y() + (target_rect.height() - new_height) / 2,
                            new_width, new_height
                        )
                        painter.drawImage(draw_rect, combined_img)

                    painter.restore()

                elif layout_mode in ["2-Up", "4-Up", "6-Up", "9-Up"]:
                    n = int(layout_mode[0])
                    chunks = [pages_to_print[i:i + n] for i in range(0, len(pages_to_print), n)]
                    
                    rows, cols = 1, 1
                    if layout_mode == "2-Up": rows, cols = 1, 2
                    elif layout_mode == "4-Up": rows, cols = 2, 2
                    elif layout_mode == "6-Up": rows, cols = 2, 3
                    elif layout_mode == "9-Up": rows, cols = 3, 3
                        
                    for sheet_idx, chunk in enumerate(chunks):
                        if self.is_cancelled: break
                        
                        rect = printer.pageRect(QPrinter.DevicePixel)
                        
                        painter.save()
                        if layout_mode in ["2-Up", "6-Up"] and rect.width() < rect.height():
                            logical_w = rect.height()
                            logical_h = rect.width()
                            painter.translate(rect.width(), 0)
                            painter.rotate(90)
                        else:
                            logical_w = rect.width()
                            logical_h = rect.height()
                            
                        cell_w = logical_w / cols
                        cell_h = logical_h / rows
                        
                        for idx, page_num in enumerate(chunk):
                            r = idx // cols
                            c = idx % cols
                            
                            page = doc[page_num]
                            mat = fitz.Matrix(300/72, 300/72)
                            pix = page.get_pixmap(matrix=mat, alpha=False)
                            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                            
                            img_rect = QRectF(img.rect())
                            target_rect = QRectF(c * cell_w, r * cell_h, cell_w, cell_h)
                            
                            scale_factor = min(target_rect.width() / img_rect.width(), target_rect.height() / img_rect.height())
                            new_w = img_rect.width() * scale_factor
                            new_h = img_rect.height() * scale_factor
                            draw_rect = QRectF(
                                target_rect.x() + (target_rect.width() - new_w) / 2,
                                target_rect.y() + (target_rect.height() - new_h) / 2,
                                new_w, new_h
                            )
                            painter.drawImage(draw_rect, img)
                            
                        painter.restore()
                            
                        if sheet_idx < len(chunks) - 1:
                            printer.newPage()

                else:
                    for i, page_num in enumerate(pages_to_print):
                        if self.is_cancelled:
                            break

                        page = doc[page_num]
                        zoom = 300 / 72 
                        mat = fitz.Matrix(zoom, zoom)
                        pix = page.get_pixmap(matrix=mat, alpha=False)
                        
                        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                        
                        if i > 0:
                            printer.newPage()

                        rect = printer.pageRect(QPrinter.DevicePixel)
                        img_rect = QRectF(img.rect())
                        target_rect = QRectF(rect)
                        
                        if scaling_mode == "Fit":
                            scale_factor = min(target_rect.width() / img_rect.width(), target_rect.height() / img_rect.height())
                            new_width = img_rect.width() * scale_factor
                            new_height = img_rect.height() * scale_factor
                            draw_rect = QRectF(
                                target_rect.x() + (target_rect.width() - new_width) / 2,
                                target_rect.y() + (target_rect.height() - new_height) / 2,
                                new_width, new_height
                            )
                            painter.drawImage(draw_rect, img)
                        elif scaling_mode == "Shrink Oversized":
                            if img_rect.width() > target_rect.width() or img_rect.height() > target_rect.height():
                                scale_factor = min(target_rect.width() / img_rect.width(), target_rect.height() / img_rect.height())
                                new_width = img_rect.width() * scale_factor
                                new_height = img_rect.height() * scale_factor
                                draw_rect = QRectF(
                                    target_rect.x() + (target_rect.width() - new_width) / 2,
                                    target_rect.y() + (target_rect.height() - new_height) / 2,
                                    new_width, new_height
                                )
                                painter.drawImage(draw_rect, img)
                            else:
                                draw_rect = QRectF(
                                    target_rect.x() + (target_rect.width() - img_rect.width()) / 2,
                                    target_rect.y() + (target_rect.height() - img_rect.height()) / 2,
                                    img_rect.width(), img_rect.height()
                                )
                                painter.drawImage(draw_rect, img)
                        else: # Actual Size
                            draw_rect = QRectF(
                                target_rect.x() + (target_rect.width() - img_rect.width()) / 2,
                                target_rect.y() + (target_rect.height() - img_rect.height()) / 2,
                                img_rect.width(), img_rect.height()
                            )
                            painter.drawImage(draw_rect, img)
                            
                painter.end()
                doc.close()

                if not self.is_cancelled:
                    self.db_manager.log_print_job(printer_name, pdf_file.filename, len(pages_to_print), copies, "Success")
                    self.file_completed.emit(pdf_file.filename, list_index)

            except Exception as e:
                import traceback
                traceback.print_exc()
                self.db_manager.log_print_job(printer_name, pdf_file.filename, 0, 1, f"Failed: {str(e)}")
                self.file_failed.emit(pdf_file.filename, list_index, str(e))
            
            self.progress_updated.emit(current_idx + 1, total_files)

        self.job_finished.emit()
