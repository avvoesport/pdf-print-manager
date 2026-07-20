import sqlite3
import os
import datetime
from pathlib import Path

class DatabaseManager:
    def __init__(self):
        # Store in %APPDATA%/PDFPrintManager
        app_data = os.getenv("APPDATA")
        if app_data:
            self.db_dir = Path(app_data) / "PDFPrintManager"
        else:
            self.db_dir = Path.home() / ".pdfprintmanager"
            
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.db_dir / "print_history.db"
        
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                printer TEXT,
                filename TEXT,
                pages INTEGER,
                copies INTEGER,
                result TEXT
            )
        ''')
        self.conn.commit()

    def log_print_job(self, printer, filename, pages, copies, result):
        cursor = self.conn.cursor()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT INTO history (timestamp, printer, filename, pages, copies, result)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (timestamp, printer, filename, pages, copies, result))
        self.conn.commit()

    def get_history(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM history ORDER BY timestamp DESC')
        return cursor.fetchall()
