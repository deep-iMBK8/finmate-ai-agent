import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List

import chromadb


BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_FILE = (
    BASE_DIR
    / "data"
    / "processed"
    / "embeddings"
    / "stock"
    / "stock_embeddings.jsonl"
)
DEFAULT_CHROMA_DIR = BASE_DIR / "data" / "vectordb" / "chroma" / "stock"
DEFAULT_COLLECTION_NAME = "stock_documents"


def load_embedding_records(input_file: Path) -> List[Dict]:
    if not input_file.exists():
        raise FileNotFoundError(f"Embedding file not found: {input_file}")

    records: List[Dict] = []
    with input_file.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            record = json.loads(stripped)
            validate_record(record, line_number)
            records.append(record)

    if not records:
        raise ValueError(f"No embedding records found in: {input_file}")

    return records


def validate_record(record: Dict, line_number: int) -> None:
    required = ("chunk_id", "text", "embedding", "metadata")
    missing = [field for field in required if field not in record]
    if missing:
        raise ValueError(
            f"Missing fields at line {line_number}: {', '.join(missing)}"
        )


def batch_iter(items: List[Dict], batch_size: int) -> Iterable[List[Dict]]:
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def upsert_to_chroma(
    records: List[Dict],
    chroma_dir: Path,
    collection_name: str,
    batch_size: int,
    reset: bool,
) -> int:
    chroma_dir.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(path=str(chroma_dir))

    if reset:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass

    collection = client.get_or_create_collection(name=collection_name)

    total = 0
    for batch in batch_iter(records, batch_size):
        ids = [record["chunk_id"] for record in batch]
        documents = [record["text"] for record in batch]
        embeddings = [record["embedding"] for record in batch]
        metadatas = [record["metadata"] for record in batch]

        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        total += len(batch)

    return total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load stock embedding JSONL into a persistent Chroma collection."
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=DEFAULT_INPUT_FILE,
        help="Path to embedding JSONL file.",
    )
    parser.add_argument(
        "--chroma-dir",
        type=Path,
        default=DEFAULT_CHROMA_DIR,
        help="Directory for persistent Chroma data.",
    )
    parser.add_argument(
        "--collection-name",
        default=DEFAULT_COLLECTION_NAME,
        help="Target Chroma collection name.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for Chroma upsert.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete and recreate the collection before loading.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_embedding_records(args.input_file)
    inserted = upsert_to_chroma(
        records=records,
        chroma_dir=args.chroma_dir,
        collection_name=args.collection_name,
        batch_size=args.batch_size,
        reset=args.reset,
    )

    print(f"[+] Chroma directory: {args.chroma_dir}")
    print(f"[+] Collection: {args.collection_name}")
    print(f"[+] Inserted records: {inserted}")


if __name__ == "__main__":
    main()
