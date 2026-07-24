"""
Utility to convert image files to PDF so they can be printed via Printavvo.
Uses PyMuPDF (fitz) which is already a project dependency.
"""
import os
import time
import fitz  # PyMuPDF


SUPPORTED_IMAGES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif')


def is_image(path: str) -> bool:
    return path.lower().endswith(SUPPORTED_IMAGES)


def convert_image_to_pdf(image_path: str, output_dir: str) -> str:
    """
    Convert a single image file to a PDF and save it next to the original.
    Returns the path to the created PDF.
    """
    base = os.path.splitext(os.path.basename(image_path))[0]
    out_path = os.path.join(output_dir, f"{base}.pdf")

    # Avoid overwriting if a file already exists
    if os.path.exists(out_path):
        stem = f"{base}_{int(time.time())}"
        out_path = os.path.join(output_dir, f"{stem}.pdf")

    # Open image via fitz and wrap in a PDF
    img_doc = fitz.open(image_path)
    pdf_bytes = img_doc.convert_to_pdf()
    img_doc.close()

    pdf_doc = fitz.open("pdf", pdf_bytes)
    pdf_doc.save(out_path)
    pdf_doc.close()

    return out_path
