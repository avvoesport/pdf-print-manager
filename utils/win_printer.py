import ctypes
import sys
from ctypes.wintypes import DWORD, LPCWSTR, WORD

def get_printer_trays(printer_name):
    if sys.platform != 'win32':
        return []
    try:
        winspool = ctypes.WinDLL("winspool.drv")
        DeviceCapabilitiesW = winspool.DeviceCapabilitiesW
        DeviceCapabilitiesW.argtypes = [LPCWSTR, LPCWSTR, WORD, ctypes.c_void_p, ctypes.c_void_p]
        DeviceCapabilitiesW.restype = DWORD
        
        DC_BINS = 6
        DC_BINNAMES = 12
        
        num_bins = DeviceCapabilitiesW(printer_name, None, DC_BINS, None, None)
        if num_bins <= 0 or num_bins == 0xFFFFFFFF:
            return []
            
        bin_names_buffer = ctypes.create_string_buffer(24 * 2 * num_bins)
        DeviceCapabilitiesW(printer_name, None, DC_BINNAMES, bin_names_buffer, None)
        
        bin_ids_buffer = (ctypes.c_uint16 * num_bins)()
        DeviceCapabilitiesW(printer_name, None, DC_BINS, bin_ids_buffer, None)
        
        trays = []
        for i in range(num_bins):
            offset = i * 24 * 2
            raw_bytes = bin_names_buffer.raw[offset:offset+48]
            name = raw_bytes.decode('utf-16le').rstrip('\x00')
            trays.append({"id": bin_ids_buffer[i], "name": name})
        return trays
    except Exception:
        return []
