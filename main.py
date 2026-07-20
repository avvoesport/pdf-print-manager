import sys
import qdarktheme
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Print Manager")
    app.setOrganizationName("Kaliber")
    app.setOrganizationDomain("kaliber.local")
    
    # Setup initial theme
    app.setStyleSheet(qdarktheme.load_stylesheet("light"))
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
