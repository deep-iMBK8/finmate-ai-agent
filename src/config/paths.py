from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

DATA_DIR = BASE_DIR / "data"

CHROMA_DIR = DATA_DIR / "chroma_db"
CHUNKS_DIR = DATA_DIR / "chunks"

PROCESSED_DIR = DATA_DIR / "processed"
PROCESSED_IMAGE_DIR = PROCESSED_DIR / "image"
PROCESSED_JSON_DIR = PROCESSED_DIR / "json"
PROCESSED_TXT_DIR = PROCESSED_DIR / "txt"

RAW_DIR = DATA_DIR / "raw"
RAW_IMAGE_DIR = RAW_DIR / "image"
RAW_PDF_DIR = RAW_DIR / "pdf"