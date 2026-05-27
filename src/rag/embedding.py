from __future__ import annotations

import os
import time

from dotenv import load_dotenv

from src.config.paths import CHROMA_DIR

try:
    from mysql import db_store
except Exception:
    db_store = None


COLLECTION_NAME = "financial_documents"
EMBEDDING_MODEL = "text-multilingual-embedding-002"
EMBEDDING_LOCATION = "asia-northeast3"
BATCH_SIZE = 30

_embedding_model = None
_collection = None


def _get_project_id() -> str | None:
    return os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")


def _init_vertex() -> None:
    from google.cloud import aiplatform
    from google.oauth2.credentials import Credentials

    project_id = _get_project_id()
    if not project_id:
        raise RuntimeError("PROJECT_ID 또는 GOOGLE_CLOUD_PROJECT 환경변수가 필요합니다.")

    access_token = os.getenv("ACCESS_TOKEN")
    if access_token:
        credentials = Credentials(token=access_token).with_quota_project(project_id)
        aiplatform.init(
            project=project_id,
            location=EMBEDDING_LOCATION,
            credentials=credentials,
        )
    else:
        aiplatform.init(project=project_id, location=EMBEDDING_LOCATION)


def get_embedding_model():
    from vertexai.language_models import TextEmbeddingModel

    global _embedding_model
    if _embedding_model is None:
        load_dotenv()
        _init_vertex()
        _embedding_model = TextEmbeddingModel.from_pretrained(EMBEDDING_MODEL)
    return _embedding_model


def get_collection():
    import chromadb

    global _collection
    if _collection is None:
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _chunk_chroma_id(chunk: dict, fallback_index: int) -> str:
    metadata = chunk.get("metadata") or {}
    document_uuid = metadata.get("document_uuid", "unknown")
    page_number = metadata.get("page_number", 0)
    return chunk.get("chunk_id") or f"emb_{document_uuid}_p{page_number}_{fallback_index}"


def embed_query(text: str) -> list[float]:
    from vertexai.language_models import TextEmbeddingInput

    model = get_embedding_model()
    embedding = model.get_embeddings(
        [TextEmbeddingInput(text=text, task_type="RETRIEVAL_QUERY")]
    )[0]
    return embedding.values


def embed_and_store_chunks(chunks: list[dict]) -> int:
    from vertexai.language_models import TextEmbeddingInput

    if not chunks:
        return 0

    model = get_embedding_model()
    collection = get_collection()

    existing_ids = set()
    if collection.count() > 0:
        existing_ids = set(collection.get(include=[]).get("ids", []))

    prepared_chunks = []
    for index, chunk in enumerate(chunks):
        chroma_id = _chunk_chroma_id(chunk, index)
        if chroma_id in existing_ids:
            continue
        copied = dict(chunk)
        copied["generated_id"] = chroma_id
        prepared_chunks.append(copied)

    stored_count = 0
    for start in range(0, len(prepared_chunks), BATCH_SIZE):
        batch = prepared_chunks[start : start + BATCH_SIZE]
        embedding_inputs = [
            TextEmbeddingInput(
                text=(
                    f"[페이지 {chunk.get('metadata', {}).get('page_number', 1)}] "
                    f"{chunk['chunk']}"
                ),
                task_type="RETRIEVAL_DOCUMENT",
            )
            for chunk in batch
        ]

        embeddings_data = None
        for attempt in range(3):
            try:
                embeddings_data = model.get_embeddings(embedding_inputs)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5)

        embeddings = [embedding.values for embedding in embeddings_data]
        ids = [chunk["generated_id"] for chunk in batch]
        documents = [chunk["chunk"] for chunk in batch]
        metadatas = []

        for chunk in batch:
            metadata = chunk.get("metadata") or {}
            metadatas.append(
                {
                    "chunk_id": str(chunk.get("chunk_id") or chunk.get("generated_id")),
                    "document_id": str(metadata.get("document_uuid")),
                    "document_uuid": str(metadata.get("document_uuid")),
                    "company": str(metadata.get("company") or "알수없음"),
                    "document_type": str(metadata.get("document_type") or "알수없음"),
                    "document_date": str(metadata.get("document_date") or ""),
                    "sector": str(metadata.get("sector") or "알수없음"),
                    "chunk_type": str(metadata.get("chunk_type") or "text"),
                    "page_number": (
                        int(metadata.get("page_number"))
                        if metadata.get("page_number") is not None
                        else -1
                    ),
                }
            )

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        stored_count += len(batch)

        if db_store and db_store.mysql_enabled():
            db_store.mark_chunks_embedded(
                chunks=batch,
                chroma_ids=ids,
                collection_name=COLLECTION_NAME,
                embedding_model=EMBEDDING_MODEL,
                embedding_dimension=len(embeddings[0]) if embeddings else None,
            )

    return stored_count
