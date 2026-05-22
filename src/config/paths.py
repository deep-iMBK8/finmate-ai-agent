from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

DATA_DIR = BASE_DIR / "data"

CHROMA_DIR = BASE_DIR / "chroma_db"

CHUNKS_DIR = DATA_DIR / "chunks"

EMBEDDINGS_DIR = DATA_DIR / "embeddings"

PROCESSED_DIR = DATA_DIR / "processed"
JSON_DIR = PROCESSED_DIR / "json"

RAW_DIR = DATA_DIR / "raw"
