from PySide6.QtCore import QSettings

class AppSettings:
    def __init__(self):
        self.settings = QSettings("Kaliber", "Printavvo")

    def get_last_folder(self):
        return self.settings.value("last_folder", "")

    def set_last_folder(self, path):
        self.settings.setValue("last_folder", path)

    def get_printer_name(self):
        return self.settings.value("printer_name", "")

    def set_printer_name(self, name):
        self.settings.setValue("printer_name", name)

    def get_paper_size(self):
        return self.settings.value("paper_size", "A4")

    def set_paper_size(self, size):
        self.settings.setValue("paper_size", size)

    def get_custom_width(self):
        return float(self.settings.value("custom_width", 210.0))

    def set_custom_width(self, w):
        self.settings.setValue("custom_width", float(w))

    def get_custom_height(self):
        return float(self.settings.value("custom_height", 297.0))

    def set_custom_height(self, h):
        self.settings.setValue("custom_height", float(h))

    def get_paper_source_id(self):
        return int(self.settings.value("paper_source_id", 6))
        
    def set_paper_source_id(self, source_id):
        self.settings.setValue("paper_source_id", int(source_id))

    def get_color_mode(self):
        return self.settings.value("color_mode", "Color")

    def set_color_mode(self, mode):
        self.settings.setValue("color_mode", mode)

    def get_duplex_mode(self):
        return self.settings.value("duplex_mode", "Single Side")

    def set_duplex_mode(self, mode):
        self.settings.setValue("duplex_mode", mode)

    def get_orientation(self):
        return self.settings.value("orientation", "Auto")

    def set_orientation(self, orientation):
        self.settings.setValue("orientation", orientation)

    def get_scaling(self):
        return self.settings.value("scaling", "Fit")

    def set_scaling(self, scaling):
        self.settings.setValue("scaling", scaling)

    def get_copies(self):
        return int(self.settings.value("copies", 1))

    def set_copies(self, copies):
        self.settings.setValue("copies", copies)

    def get_layout_mode(self):
        return self.settings.value("layout_mode", "Normal")

    def set_layout_mode(self, mode):
        self.settings.setValue("layout_mode", mode)

    def get_theme(self):
        return self.settings.value("theme", "light")

    def set_theme(self, theme):
        self.settings.setValue("theme", theme)

    def save_window_geometry(self, geometry):
        self.settings.setValue("geometry", geometry)

    def get_window_geometry(self):
        return self.settings.value("geometry")

    def get_auto_import(self):
        # Default to True since the user requested this feature to just work
        val = self.settings.value("auto_import", "true")
        return val.lower() == "true" if isinstance(val, str) else bool(val)

    def set_auto_import(self, enabled):
        self.settings.setValue("auto_import", enabled)
