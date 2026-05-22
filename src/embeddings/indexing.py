import json
import os

import chromadb
from sentence_transformers import SentenceTransformer

from src.config.paths import CHROMA_DIR, CHUNKS_DIR

# Chroma client 생성
client = chromadb.PersistentClient(path=CHROMA_DIR)

collection = client.get_or_create_collection(name="financial_documents")

# 임베딩 모델
model = SentenceTransformer("BAAI/bge-m3")

# chunk 파일 순회 

for filename in os.listdir(CHUNKS_DIR):
    if not filename.endswith(".json"):
        continue

    path = os.path.join(CHUNKS_DIR, filename)

    with open(path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    for chunk in chunks:
        chunk_text = chunk["chunk_text"]

        # embedding 생성
        embedding = model.encode(chunk_text, normalize_embeddings=True).tolist()

        # chroma 저장
        collection.add(
            ids=[chunk["chunk_id"]],
            embeddings=[embedding],
            documents=[chunk_text],
            metadatas=[{
                "document_uuid": chunk["document_uuid"],
                "company": chunk["company"],
                "document_type": chunk["document_type"],
                "document_date": chunk["document_date"],
                "sector": chunk["sector"],
                "chunk_type": chunk["chunk_type"],
                "page_number": chunk.get("page_number", -1)
            }]
        )

    print(f"{filename} 저장 완료")

print("ChromaDB 저장 완료")