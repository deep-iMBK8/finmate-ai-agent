import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv

import chromadb
from google.cloud import aiplatform
from google.oauth2.credentials import Credentials

from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel
from src.config.paths import CHUNKS_DIR, CHROMA_DIR

# 1. 환경 변수 및 GCP 프로젝트 주입
load_dotenv()
PROJECT_ID = os.getenv("PROJECT_ID")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN") # 한 시간 마다 지속적으로 발급 받아야 함
LOCATION = "asia-northeast3"  

if not PROJECT_ID:
    raise ValueError("❌ 'PROJECT_ID' 또는 'GOOGLE_CLOUD_PROJECT' 값 넣어주세요")
if not ACCESS_TOKEN:
    raise ValueError("❌ 'ACCESS_TOKEN' 값 넣어주세요. 구글 클라우드 토큰을 발급 필요")

# 구글 라이브러리들이 내부적으로 프로젝트 ID를 찾을 때 쓰는 이름이 제각각이라 세 개 다 명시
# TODO: 키 네임 통일 필요
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GCP_PROJECT"] = PROJECT_ID
os.environ["CLOUDSDK_CORE_PROJECT"] = PROJECT_ID

print("구글 클라우드 보안 토큰 인증 및 Vertex AI 초기화 중")
credentials = Credentials(token=ACCESS_TOKEN).with_quota_project(PROJECT_ID)
aiplatform.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)

print("구글 Vertex AI 금융/다국어 특화 임베딩 모델 로드 완료.")
model = TextEmbeddingModel.from_pretrained("text-multilingual-embedding-002")

os.makedirs(CHUNKS_DIR, exist_ok=True)

# 2. 크로마 DB 전역 초기화
chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

# 챗봇의 검색 정확도 확인용
# 유클리드 거리 : 긴 청크에서는 거리 기반이라 벡터가 길게 뻗어나가 거리가 멀다고 오해
# 코사인 유사도 : 벡터의 길이는 무시하고 방향만 봐서 같은 주제를 향해 뻗어있다는 각도로 판단
collection = chroma_client.get_or_create_collection(
    name="financial_documents",
    metadata={"hnsw:space": "cosine"} 
)

BATCH_SIZE = 30 

# 청크 리스트를 받아 Vertex AI로 임베딩을 생성한 후 ChromaDB에 적재
def embed_and_store_chunks(chunks: list[dict]) -> int:
    if not chunks:
        print("적재할 청크 데이터가 없습니다.")
        return 0

    # 1. 청크별 고유 식별 ID 생성
    for idx, c in enumerate(chunks):
        meta_obj = c.get("metadata", {})
        doc_uuid = meta_obj.get("document_uuid", "unknown")
        page_num = meta_obj.get("page_number", 0)
        c["generated_id"] = f"emb_{doc_uuid}_p{page_num}_{idx}"

    # 2. 실시간성 최적화: 현재 문서(document_uuid)의 기존 적재 데이터만 타겟팅하여 중복 검사
    target_doc_uuid = chunks[0].get("metadata", {}).get("document_uuid", "unknown")
    existing_ids = set()
    
    if collection.count() > 0:
        # 해당 document_uuid를 가진 데이터만 선별 조회
        existing = collection.get(where={"document_uuid": target_doc_uuid}, include=[])
        existing_ids = set(existing["ids"])

    new_chunks = [c for c in chunks if c["generated_id"] not in existing_ids]
    
    if not new_chunks:
        print(f"문서({target_doc_uuid})의 새로운 청크가 없어 빌드를 스킵합니다.")
        return 0
        
    print(f"임베딩 및 벡터 적재 시작 (대상 청크: {len(new_chunks)}개)")
    
    # 3. 배치 연산으로 임베딩 생성 및 ChromaDB 적재
    for i in range(0, len(new_chunks), BATCH_SIZE):
        batch = new_chunks[i : i + BATCH_SIZE]
        
        embedding_inputs = [
            TextEmbeddingInput(
                text=f"[페이지 {c.get('metadata', {}).get('page_number', 1)}] {c['chunk']}",
                task_type="RETRIEVAL_DOCUMENT",
            )
            for c in batch
        ]
        
        # 최대 3회 재시도 가드레일 로직
        embeddings_data = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                embeddings_data = model.get_embeddings(embedding_inputs)
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"API 통신 지연 발생 ({attempt + 1}/{max_retries}차 재시도 중... 3초 대기): {e}")
                    time.sleep(3)
                else:
                    print(f"최종 실패. 현재 배치는 건너뜁니다.")
        
        if not embeddings_data:
            continue
            
        embeddings = [emb.values for emb in embeddings_data]
        ids = [c["generated_id"] for c in batch]
        documents = [c["chunk"] for c in batch]
        
        metadatas = []
        for c in batch:
            meta_obj = c.get("metadata", {})
            meta = {
                "document_uuid": str(meta_obj.get("document_uuid")),
                "company": str(meta_obj.get("company") or ""), 
                "document_type": str(meta_obj.get("document_type") or ""), 
                "document_date": str(meta_obj.get("document_date") or ""),
                "sector": str(meta_obj.get("sector") or ""), 
                "chunk_type": str(meta_obj.get("chunk_type") or "text"),
                "page_number": int(meta_obj.get("page_number")) if meta_obj.get("page_number") is not None else -1
            }
            cleaned_meta = {k: (v if v is not None else "") for k, v in meta.items()}
            metadatas.append(cleaned_meta)
            
        # ChromaDB 컬렉션에 벌크 인서트
        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )
        time.sleep(0.1)
        
    print(f"ChromaDB 적재 완료! (추가된 청크: {len(new_chunks)}개)")
    return len(new_chunks)


# 기존처럼 단독 스크립트로 실행할 때의 가이드라인 유지
if __name__ == "__main__":
    if not os.path.exists(CHUNKS_DIR):
        os.makedirs(CHUNKS_DIR)

    json_files = [f for f in os.listdir(CHUNKS_DIR) if f.endswith(".json")]
    print(f"단독 스크립트 모드: {len(json_files)}개의 청크 파일을 가동합니다.")

    for index, file_name in enumerate(json_files, 1):
        path = os.path.join(CHUNKS_DIR, file_name)
        with open(path, "r", encoding="utf-8") as f:
            file_chunks = json.load(f)
        
        print(f"[{index}/{len(json_files)}] {file_name} 처리 중...")
        embed_and_store_chunks(file_chunks)