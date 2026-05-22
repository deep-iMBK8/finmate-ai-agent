import json
import os

import chromadb

# 1. 크로마 DB를 저장할 로컬 폴더 지정 및 클라이언트 생성
# 실행하고 나면 현재 폴더에 'my_chroma_db'라는 폴더가 자동으로 생깁니다.
db_path = "./data/my_chroma_db"
client = chromadb.PersistentClient(path=db_path)

# 2. 컬렉션(데이터를 담을 테이블) 생성 또는 가져오기
# vertex-multilingual-embedding-002 모델의 출력 차원 수는 '768차원'입니다.
collection_name = "financial_chatbot_docs"
collection = client.get_or_create_collection(name=collection_name)

# 3. 임베딩된 파일들이 있는 폴더 경로
embedding_dir = "./data/embeddings/insurance_embedding"
vertex_files = [
    f
    for f in os.listdir(embedding_dir)
    if f.endswith(".json") and f.startswith("vertex_")
]

print(
    f"📂 총 {len(vertex_files)}개의 임베딩 완료 파일을 찾았습니다. 크로마 DB 입력을 시작합니다."
)

# 4. 데이터 밀어넣기
for index, file_name in enumerate(vertex_files, 1):
    file_path = os.path.join(embedding_dir, file_name)

    with open(file_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    print(
        f"📥 [{index}/{len(vertex_files)}] {file_name} 입력을 준비 중... (청크 개수: {len(chunks)}개)"
    )

    # 크로마 DB에 넣기 위해 데이터를 리스트 형태로 분리
    ids = []
    embeddings = []
    documents = []
    metadatas = []

    for idx, item in enumerate(chunks):
        # 겹치지 않는 고유 ID 생성 (예: vertex_file_name_0, vertex_file_name_1 ...)
        unique_id = f"{file_name[:-5]}_{idx}"

        ids.append(unique_id)
        embeddings.append(item["vector"])  # 구글이 뽑아준 768차원 벡터
        documents.append(item["chunk_text"])  # 실제 본문 텍스트
        metadatas.append(
            {
                "source_file": file_name,
                "page_number": item["page_number"],  # 나중에 출처 표시용 메타데이터
            }
        )

    # 크로마 DB는 한 번에 대량으로 넣는 배치 입력(add)을 지원합니다.
    # 대용량 파일(1100개짜리 등)을 위해 안전하게 400개씩 쪼개서 넣습니다.
    batch_size = 400
    for i in range(0, len(ids), batch_size):
        collection.add(
            ids=ids[i : i + batch_size],
            embeddings=embeddings[i : i + batch_size],
            documents=documents[i : i + batch_size],
            metadatas=metadatas[i : i + batch_size],
        )

print(
    f"\n🎉 크로마 DB 구축 완벽 성공! 총 {collection.count()}개의 데이터가 저장되었습니다."
)
