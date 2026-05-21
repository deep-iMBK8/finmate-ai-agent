# text-embedding-004는 global 미지원 → us-central1 사용

import argparse
import json
import os
import time
from pathlib import Path

import chromadb
from google import genai

BASE_DIR   = Path(__file__).resolve().parent.parent.parent
ENV_PATH   = BASE_DIR / ".env"
CHUNK_DIR  = BASE_DIR / "data" / "processed" / "image" / "chunks"
CHROMA_DIR = BASE_DIR / "data" / "vectordb" / "image_chroma"
COLLECTION = "image_docs"
EMBED_MODEL = "text-embedding-004"
BATCH_SIZE  = 10   # Vertex AI 임베딩 배치 크기


# ── 환경변수 로드 ──────────────────────────────────────────────────────────────

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


# ── 청크 로딩 ──────────────────────────────────────────────────────────────────

def load_all_chunks(chunk_dir: Path, limit: int = None) -> list[dict]:
    all_chunks = []
    json_files = sorted(chunk_dir.rglob("*.json"))
    if limit:
        json_files = json_files[:limit]
    for json_path in json_files:
        try:
            chunks = json.loads(json_path.read_text(encoding="utf-8"))
            all_chunks.extend(chunks)
        except Exception as e:
            print(f"  FAIL {json_path.name}: {e}")
    return all_chunks


# ── Vertex AI 임베딩 생성 ──────────────────────────────────────────────────────

def embed_texts(client: genai.Client, texts: list[str]) -> list[list[float]]:
    """텍스트 리스트 → 임베딩 벡터 리스트"""
    response = client.models.embed_content(
        model=EMBED_MODEL,
        contents=texts,
    )
    return [e.values for e in response.embeddings]


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="청크 → Vertex AI 임베딩 → ChromaDB 저장")
    parser.add_argument("--chunk-dir",  default=str(CHUNK_DIR))
    parser.add_argument("--chroma-dir", default=str(CHROMA_DIR))
    parser.add_argument("--limit",      type=int, default=None, help="처리할 청크 파일 수 제한")
    parser.add_argument("--reset",      action="store_true", help="기존 컬렉션 삭제 후 새로 생성")
    args = parser.parse_args()

    chunk_dir  = Path(args.chunk_dir)
    chroma_dir = Path(args.chroma_dir)

    # ── .env 로드 ──────────────────────────────────────────────────────────────
    load_dotenv(ENV_PATH)
    project  = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    if not project:
        raise RuntimeError(".env에 GOOGLE_CLOUD_PROJECT=프로젝트ID 를 넣어주세요.")

    # text-embedding-004는 global 미지원 → us-central1 사용
    if location == "global":
        location = "us-central1"

    print(f"Vertex AI 프로젝트: {project} / 리전: {location}")

    # ── Vertex AI 클라이언트 ───────────────────────────────────────────────────
    ai_client = genai.Client(vertexai=True, project=project, location=location)

    # ── 청크 로딩 ──────────────────────────────────────────────────────────────
    print("\n청크 로딩 중...")
    all_chunks = load_all_chunks(chunk_dir, limit=args.limit)
    print(f"총 청크 수: {len(all_chunks)}")

    if not all_chunks:
        print("청크가 없습니다. 먼저 image_chunker.py를 실행하세요.")
        return

    # ── ChromaDB 초기화 ────────────────────────────────────────────────────────
    print(f"\nChromaDB 초기화: {chroma_dir}")
    chroma_dir.mkdir(parents=True, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=str(chroma_dir))

    if args.reset:
        try:
            chroma_client.delete_collection(COLLECTION)
            print("기존 컬렉션 삭제됨")
        except Exception:
            pass

    try:
        collection = chroma_client.get_collection(COLLECTION)
        print(f"기존 컬렉션 사용 (현재 {collection.count()}개)")
    except Exception:
        collection = chroma_client.create_collection(COLLECTION)
        print("새 컬렉션 생성됨")

    # ── 중복 방지 ──────────────────────────────────────────────────────────────
    existing_ids = set()
    if collection.count() > 0:
        existing = collection.get(include=[])
        existing_ids = set(existing["ids"])
        print(f"이미 저장된 청크: {len(existing_ids)}개 (스킵)")

    new_chunks = [c for c in all_chunks if c["chunk_id"] not in existing_ids]
    print(f"새로 저장할 청크: {len(new_chunks)}개")

    if not new_chunks:
        print("추가할 청크 없음. 완료!")
        return

    # ── 배치 임베딩 + ChromaDB 저장 ───────────────────────────────────────────
    print("\n임베딩 생성 및 저장 중...")
    for i in range(0, len(new_chunks), BATCH_SIZE):
        batch = new_chunks[i: i + BATCH_SIZE]

        texts = [c["text"] for c in batch]
        ids   = [c["chunk_id"] for c in batch]
        metadatas = []

        for c in batch:
            meta = {
                k: (v if v is not None else "")
                for k, v in c["metadata"].items()
            }
            meta["chunk_type"]  = c.get("chunk_type", "text")
            meta["chunk_index"] = c.get("chunk_index", 0)
            metadatas.append(meta)

        try:
            embeddings = embed_texts(ai_client, texts)
        except Exception as e:
            print(f"  임베딩 실패 (재시도): {e}")
            time.sleep(5)
            embeddings = embed_texts(ai_client, texts)

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        print(f"  저장 {i + len(batch)}/{len(new_chunks)}")

    print(f"\n완료! ChromaDB 총 {collection.count()}개 청크 저장됨")
    print(f"저장 위치: {chroma_dir}")


if __name__ == "__main__":
    main()
