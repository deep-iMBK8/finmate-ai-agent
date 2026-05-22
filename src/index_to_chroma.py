import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

try:
    from scripts.chunking import split_text_with_langchain
except ModuleNotFoundError:
    from chunking import split_text_with_langchain


DEFAULT_JSON_DIR = "data/processed/ocr_text"
DEFAULT_CHROMA_DIR = "data/chroma_db"
DEFAULT_COLLECTION_NAME = "financial_documents"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120


load_dotenv()


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise ValueError("GEMINI_API_KEY가 없습니다. .env 파일을 확인하세요.")

    return genai.Client(api_key=api_key)


def collect_json_files(json_dir: str):
    root = Path(json_dir)

    if not root.exists():
        raise FileNotFoundError(f"JSON 폴더가 없습니다: {json_dir}")

    return sorted(root.rglob("*.json"))


def load_rag_document(json_path: Path):
    with json_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def split_text_into_chunks(text: str, chunk_size: int, chunk_overlap: int):
    return split_text_with_langchain(text, chunk_size, chunk_overlap)


def read_legacy_text(document: dict, json_path: Path):
    evidence_items = document.get("source_evidence", [])

    if evidence_items and isinstance(evidence_items, list):
        source_txt = evidence_items[0].get("source_txt")
        if source_txt:
            source_txt_path = Path(source_txt)
            if source_txt_path.exists():
                return source_txt_path.read_text(encoding="utf-8")

    same_stem_txt = json_path.with_suffix(".txt")
    if same_stem_txt.exists():
        return same_stem_txt.read_text(encoding="utf-8")

    same_stem_md = json_path.with_suffix(".md")
    if same_stem_md.exists():
        return same_stem_md.read_text(encoding="utf-8")

    return ""


def normalize_document(document: dict, json_path: Path, chunk_size: int, chunk_overlap: int):
    if document.get("document_id") and document.get("chunks"):
        return document

    full_text = document.get("full_text") or read_legacy_text(document, json_path)
    document_id = document.get("document_id") or document.get("doc_id") or json_path.stem
    user_id = document.get("user_id") or document.get("customer_id") or document_id
    source_evidence = document.get("source_evidence", [])
    first_evidence = source_evidence[0] if source_evidence else {}

    return {
        "document_id": document_id,
        "user_id": user_id,
        "document_sector": document.get("document_sector") or document.get("sector") or "",
        "document_date": document.get("document_date") or "",
        "document_type": document.get("document_type") or document.get("doc_type") or "",
        "company": document.get("company") or "",
        "document_title": document.get("document_title") or document.get("title") or document_id,
        "full_text": full_text,
        "key_terms": document.get("key_terms", []),
        "chunks": split_text_into_chunks(
            text=full_text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        ),
        "metadata": {
            "customer_name": document.get("customer_name", ""),
            "checked_items": document.get("checked_items", {}),
            "source_image": first_evidence.get("source_image", ""),
            "source_txt": first_evidence.get("source_txt", str(json_path.with_suffix(".txt"))),
            "ocr_model": first_evidence.get("model", ""),
            "ocr_status": first_evidence.get("status", ""),
            "error_message": first_evidence.get("error_message"),
            "created_at": first_evidence.get("created_at", ""),
        },
    }


def normalize_metadata_value(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return json.dumps(value, ensure_ascii=False)


def build_chunk_metadata(document: dict, chunk: dict, json_path: Path):
    metadata = {
        "document_id": document.get("document_id", ""),
        "user_id": document.get("user_id", ""),
        "document_sector": document.get("document_sector", ""),
        "document_date": document.get("document_date", ""),
        "document_type": document.get("document_type", ""),
        "company": document.get("company", ""),
        "document_title": document.get("document_title", ""),
        "page_count": document.get("page_count", ""),
        "key_terms": document.get("key_terms", []),
        "chunk_id": chunk.get("chunk_id", ""),
        "page_number": chunk.get("page_number", ""),
        "json_path": str(json_path),
    }

    extra_metadata = document.get("metadata", {})
    if isinstance(extra_metadata, dict):
        for key, value in extra_metadata.items():
            metadata[f"meta_{key}"] = value

    return {
        key: normalize_metadata_value(value)
        for key, value in metadata.items()
    }


def embed_texts(client, texts, model_name: str, max_retries: int, retry_sleep: float):
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.embed_content(
                model=model_name,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                ),
            )
            return [embedding.values for embedding in response.embeddings]
        except Exception as error:
            if attempt >= max_retries:
                raise

            message = str(error)
            if "429" in message or "RESOURCE_EXHAUSTED" in message:
                print(f"Rate limit 감지. {retry_sleep}초 대기 후 재시도합니다. ({attempt}/{max_retries})")
                time.sleep(retry_sleep)
                continue

            raise


def get_existing_ids(collection):
    try:
        return set(collection.get(include=[])["ids"])
    except Exception:
        return set()


def main():
    parser = argparse.ArgumentParser(
        description="RAG JSON chunks를 Chroma Vector DB에 적재합니다."
    )
    parser.add_argument(
        "--json-dir",
        default=DEFAULT_JSON_DIR,
        help="gemini_ocr_images.py가 생성한 JSON 폴더"
    )
    parser.add_argument(
        "--document-id",
        default=None,
        help="특정 document_id만 인덱싱합니다."
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=None,
        help="테스트용으로 최대 N개 chunk만 인덱싱합니다."
    )
    parser.add_argument(
        "--chroma-dir",
        default=DEFAULT_CHROMA_DIR,
        help="Chroma DB 저장 폴더"
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION_NAME,
        help="Chroma collection 이름"
    )
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help="Gemini embedding 모델명"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Embedding/API 호출 배치 크기"
    )
    parser.add_argument(
        "--batch-sleep",
        type=float,
        default=2.0,
        help="Embedding 배치 사이 대기 시간(초)"
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Rate limit 발생 시 최대 재시도 횟수"
    )
    parser.add_argument(
        "--retry-sleep",
        type=float,
        default=15.0,
        help="Rate limit 발생 시 재시도 전 대기 시간(초)"
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="이미 Chroma에 있는 chunk도 다시 임베딩해서 덮어씁니다."
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="구형 JSON에 chunks가 없을 때 생성할 chunk 최대 글자 수"
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
        help="구형 JSON에 chunks가 없을 때 생성할 chunk 겹침 글자 수"
    )

    args = parser.parse_args()

    import chromadb

    json_files = collect_json_files(args.json_dir)
    gemini_client = get_gemini_client()
    chroma_client = chromadb.PersistentClient(path=args.chroma_dir)
    collection = chroma_client.get_or_create_collection(name=args.collection)
    existing_ids = set() if args.reindex else get_existing_ids(collection)

    ids = []
    texts = []
    metadatas = []
    skip_existing_count = 0

    for json_path in json_files:
        document = normalize_document(
            document=load_rag_document(json_path),
            json_path=json_path,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
        document_id = document.get("document_id")
        chunks = document.get("chunks", [])

        if args.document_id and document_id != args.document_id:
            continue

        if not document_id or not chunks:
            print(f"건너뜀: {json_path} - document_id 또는 검색할 텍스트/chunks 없음")
            continue

        for chunk in chunks:
            chunk_id = chunk.get("chunk_id")
            chunk_text = chunk.get("text", "").strip()

            if not chunk_id or not chunk_text:
                continue

            vector_id = f"{document_id}_chunk_{int(chunk_id):03d}"
            if vector_id in existing_ids:
                skip_existing_count += 1
                continue

            ids.append(vector_id)
            texts.append(chunk_text)
            metadatas.append(build_chunk_metadata(document, chunk, json_path))

            if args.max_chunks is not None and len(texts) >= args.max_chunks:
                break

        if args.max_chunks is not None and len(texts) >= args.max_chunks:
            break

    print("==============================")
    print("Chroma 인덱싱 시작")
    print("==============================")
    print(f"JSON 파일 수: {len(json_files)}")
    print(f"적재 대상 chunk 수: {len(texts)}")
    print(f"이미 존재해서 건너뛴 chunk 수: {skip_existing_count}")
    print(f"Chroma 저장 폴더: {args.chroma_dir}")
    print(f"Collection: {args.collection}")

    indexed_count = 0

    for start in range(0, len(texts), args.batch_size):
        end = start + args.batch_size
        batch_ids = ids[start:end]
        batch_texts = texts[start:end]
        batch_metadatas = metadatas[start:end]
        embeddings = embed_texts(
            client=gemini_client,
            texts=batch_texts,
            model_name=args.embedding_model,
            max_retries=args.max_retries,
            retry_sleep=args.retry_sleep,
        )

        collection.upsert(
            ids=batch_ids,
            documents=batch_texts,
            embeddings=embeddings,
            metadatas=batch_metadatas,
        )

        indexed_count += len(batch_texts)
        print(f"인덱싱 진행: {indexed_count}/{len(texts)}")

        if args.batch_sleep > 0 and indexed_count < len(texts):
            time.sleep(args.batch_sleep)

    print("\n==============================")
    print("Chroma 인덱싱 완료")
    print("==============================")
    print(f"총 적재 chunk 수: {indexed_count}")


if __name__ == "__main__":
    main()
