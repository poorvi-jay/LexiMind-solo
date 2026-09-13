import cv2
import numpy as np
import pdfplumber
import asyncio
import threading
from io import BytesIO
from fastapi import HTTPException

# EasyOCR (+ torch) takes ~20-30s to import and load, so build the reader on
# the first OCR request instead of at startup — then reuse it forever.
_reader = None
_reader_lock = threading.Lock()


def get_reader():
    global _reader
    if _reader is None:
        with _reader_lock:
            if _reader is None:
                import easyocr
                _reader = easyocr.Reader(['en'], gpu=False)
    return _reader

def preprocess_image(img_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cleaned = cv2.fastNlMeansDenoising(thresh, h=10)
    return cleaned

def _extract_from_image_sync(img_bytes: bytes) -> str:
    cleaned = preprocess_image(img_bytes)
    results = get_reader().readtext(cleaned)
    text = " ".join([res[1] for res in results])
    if not text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text. Please try a clearer image or paste text manually.")
    return text

async def extract_from_image(img_bytes: bytes) -> str:
    # Run in thread pool — EasyOCR is CPU-blocking
    return await asyncio.to_thread(_extract_from_image_sync, img_bytes)

def _extract_from_pdf_sync(pdf_bytes: bytes):
    """Returns (page_texts, page_count) — one string per PDF page, in order,
    so the reader can show the document page by page."""
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            pages = len(pdf.pages)
            page_texts = [page.extract_text() or "" for page in pdf.pages]
            if any(t.strip() for t in page_texts):
                return page_texts, pages

            # Scanned PDF fallback — use pdf2image + EasyOCR
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(pdf_bytes)
            pages = len(images)
            page_texts = []
            for img in images:
                img_array = np.array(img)
                img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
                results = get_reader().readtext(img_bgr)
                page_texts.append(" ".join([r[1] for r in results]))
            return page_texts, pages

    except Exception as e:
        if "password" in str(e).lower():
            raise HTTPException(status_code=400, detail="This PDF is password-protected. Please unlock it and try again.")
        raise HTTPException(status_code=400, detail="Could not process PDF.")

async def extract_from_pdf(pdf_bytes: bytes):
    return await asyncio.to_thread(_extract_from_pdf_sync, pdf_bytes)