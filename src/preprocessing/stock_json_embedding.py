import json
import re
from pathlib import Path
from typing import Dict, List

from sentence_transformers import SentenceTransformer
from tqdm import tqdm


BASE_DIR = Path(__file__).resolve().parents[2]
INPUT_DIR = BASE_DIR / "data" / "processed" / "json" / "stock"
OUTPUT_DIR = BASE_DIR / "data" / "processed" / "embeddings" / "stock"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = OUTPUT_DIR / "stock_embeddings.jsonl"

MODEL_NAME = "BAAI/bge-m3"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
BATCH_SIZE = 32


def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def split_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> List[str]:
    text = clean_text(text)
    if not text:
        return []

    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = start + chunk_size
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= text_length:
            break

        start += chunk_size - chunk_overlap

    return chunks


def load_json_files(input_dir: Path) -> List[Path]:
    json_files = list(input_dir.glob("*.json"))
    if not json_files:
        raise FileNotFoundError(f"{input_dir} 폴더에 JSON 파일이 없습니다.")
    return json_files


def build_chunk_records(document_data: Dict) -> List[Dict]:
    chunk_records = []

    document_uuid = document_data.get("document_uuid", "")
    company = document_data.get("company", "")
    document_title = document_data.get("document_title", "")
    sector = document_data.get("sector", "stock")
    file_type = document_data.get("file_type", "pdf")

    for page in document_data.get("pages", []):
        page_number = page.get("page_number", 0)
        page_text = page.get("text", "")

        text_chunks = split_text(page_text)

        for chunk_index, chunk_text in enumerate(text_chunks, start=1):
            chunk_id = f"{document_uuid}_p{page_number}_c{chunk_index}"

            chunk_records.append(
                {
                    "chunk_id": chunk_id,
                    "document_uuid": document_uuid,
                    "company": company,
                    "document_title": document_title,
                    "page_number": page_number,
                    "chunk_index": chunk_index,
                    "text": chunk_text,
                    "sector": sector,
                    "file_type": file_type,
                }
            )

    return chunk_records


def batch_iter(items: List[Dict], batch_size: int) -> List[List[Dict]]:
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]


def main() -> None:
    json_files = load_json_files(INPUT_DIR)

    all_chunks: List[Dict] = []

    for json_file in json_files:
        print(f"청킹 중: {json_file.name}")

        with open(json_file, "r", encoding="utf-8") as f:
            document_data = json.load(f)

        chunk_records = build_chunk_records(document_data)
        all_chunks.extend(chunk_records)

    if not all_chunks:
        raise ValueError("생성된 chunk가 없습니다.")

    print(f"[+] 총 chunk 수: {len(all_chunks)}")

    model = SentenceTransformer(MODEL_NAME)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as outfile:
        for batch in tqdm(batch_iter(all_chunks, BATCH_SIZE), desc="임베딩 생성 중"):
            texts = [chunk["text"] for chunk in batch]

            embeddings = model.encode(
                texts,
                batch_size=BATCH_SIZE,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )

            for chunk, embedding in zip(batch, embeddings):
                record = {
                    "chunk_id": chunk["chunk_id"],
                    "text": chunk["text"],
                    "embedding": embedding.tolist(),
                    "metadata": {
                        "document_uuid": chunk["document_uuid"],
                        "company": chunk["company"],
                        "document_title": chunk["document_title"],
                        "page_number": chunk["page_number"],
                        "chunk_index": chunk["chunk_index"],
                        "sector": chunk["sector"],
                        "file_type": chunk["file_type"],
                    },
                }

                outfile.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"[+] 임베딩 저장 완료: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()