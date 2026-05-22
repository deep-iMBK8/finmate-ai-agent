import chromadb
from sentence_transformers import SentenceTransformer

from src.config.paths import CHROMA_DIR

# Chroma client 생성
# client = chromadb.PersistentClient(path="../chroma_db")
client = chromadb.PersistentClient(path=CHROMA_DIR)

collection = client.get_or_create_collection(name="financial_documents")

# 임베딩 모델
model = SentenceTransformer("BAAI/bge-m3")

# 사용자 질문
query = "미래에셋 ETF 위험등급 알려줘"

# query embedding 생성
query_embedding = model.encode(query, normalize_embeddings=True).tolist()

# retrieval
results = collection.query(query_embeddings=[query_embedding], n_results=5)

# 결과 출력
documents = results["documents"][0]
metadatas = results["metadatas"][0]
distances = results["distances"][0]

print("질문: ", query)

for idx, (doc, meta, dist) in enumerate(zip(documents, metadatas, distances), start=1):
    print(f"\n===== 결과 {idx} =====")

    print(f"유사도 거리: {dist}")

    print(f"회사명: {meta['company']}")
    print(f"섹터: {meta['sector']}")
    print(f"문서종류: {meta['document_type']}")
    print(f"페이지: {meta['page_number']}")

    print("\n[Chunk]")
    print(doc[:500])
