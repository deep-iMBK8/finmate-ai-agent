from sentence_transformers import SentenceTransformer

model = SentenceTransformer(
    "BAAI/bge-m3"
)

embedding = model.encode(
    "안녕하세요"
)

print(len(embedding))