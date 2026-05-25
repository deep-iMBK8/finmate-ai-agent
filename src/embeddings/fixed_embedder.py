import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv

# 1. 환경 변수 및 GCP 프로젝트 주입
load_dotenv()
PROJECT_ID = os.getenv("PROJECT_ID")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
LOCATION = "asia-northeast3"  # 서울 리전

os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GCP_PROJECT"] = PROJECT_ID
os.environ["CLOUDSDK_CORE_PROJECT"] = PROJECT_ID

import chromadb
from google.cloud import aiplatform
from google.oauth2.credentials import Credentials

print("🔄 구글 클라우드 보안 토큰 인증 및 Vertex AI 초기화 중...")
credentials = Credentials(token=ACCESS_TOKEN).with_quota_project(PROJECT_ID)
aiplatform.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)

from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

print("🤖 구글 Vertex AI 금융/다국어 특화 임베딩 모델 로드 완료.")
model = TextEmbeddingModel.from_pretrained("text-multilingual-embedding-002")

# 2. 크로마 DB 설정 (코사인 유사도 메트릭 강제 지정)
CHROMA_DIR = "./data/vectordb/financial_chroma"
CHUNKS_DIR = "./data/chunks/chunking"

chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

# 🌟 중요: 챗봇의 검색 정확도를 극대화하기 위해 코사인 유사도(cosine) 공간 설정
collection = chroma_client.get_or_create_collection(
    name="financial_documents",
    metadata={"hnsw:space": "cosine"} 
)

# 3. 중복 방지 스킵 로직
existing_ids = set()
if collection.count() > 0:
    existing = collection.get(include=[])
    existing_ids = set(existing["ids"])
    print(f"📊 현재 ChromaDB에 매핑된 기존 청크: {len(existing_ids)}개 (중복 빌드 스킵 활성화)")

# 4. 파일 검색 및 융합 파이프라인 가동
if not os.path.exists(CHUNKS_DIR):
    os.makedirs(CHUNKS_DIR)

json_files = [f for f in os.listdir(CHUNKS_DIR) if f.endswith(".json")]
print(f"📂 총 {len(json_files)}개의 금융 청크 파일을 탐색했습니다.")

BATCH_SIZE = 50 

for index, file_name in enumerate(json_files, 1):
    path = os.path.join(CHUNKS_DIR, file_name)
    
    with open(path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
        
    # 🌟 [포맷 변경 대응] 데이터에 고유 ID가 없으므로 metadata 정보를 조합해 결정적(Deterministic) 고유 ID 동적 주입
    for idx, c in enumerate(chunks):
        meta_obj = c.get("metadata", {})
        doc_uuid = meta_obj.get("document_uuid", "unknown")
        page_num = meta_obj.get("page_number", 0)
        # 기존 스킵/중복방지 로직과 100% 호환되도록 임시 고유 ID 생성
        c["generated_id"] = f"emb_{doc_uuid}_p{page_num}_{idx}"
        
    # 새로 정의된 generated_id 기준으로 기존 DB 내부 데이터 검색 및 중복 패스
    new_chunks = [c for c in chunks if c["generated_id"] not in existing_ids]
    
    if not new_chunks:
        print(f"➔ [{index}/{len(json_files)}] {file_name}: 새로운 청크가 없어 패스합니다.")
        continue
        
    print(f"🚀 [{index}/{len(json_files)}] {file_name} 처리 시작 (대상 청크: {len(new_chunks)}개)")
    
    # 5. 배치 연산 + 크로마 직통 적재 루프
    for i in range(0, len(new_chunks), BATCH_SIZE):
        batch = new_chunks[i : i + BATCH_SIZE]
        
        # 🌟 [키 이름 변경] c["chunk_text"] -> c["page_content"]로 매핑 변경
        embedding_inputs = [
            TextEmbeddingInput(
                text=f"[페이지 {c.get('metadata', {}).get('page_number', 1)}] {c['page_content']}",
                task_type="RETRIEVAL_DOCUMENT",
            )
            for c in batch
        ]
        
        # [기능 유지] 최대 3회 재시도 가드레일 로직
        embeddings_data = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                embeddings_data = model.get_embeddings(embedding_inputs)
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"⚠️ API 통신 지연 발생 ({attempt + 1}/{max_retries}차 재시도 중... 5초 대기): {e}")
                    time.sleep(5)
                else:
                    print(f"❌ {max_retries}회 재시도 후 최종 실패. 현재 배치는 건너뜁니다.")
        
        if not embeddings_data:
            continue
            
        embeddings = [emb.values for emb in embeddings_data]
        
        # 🌟 동적 생성한 고유 ID와 새 텍스트 필드 바인딩
        ids = [c["generated_id"] for c in batch]
        documents = [c["page_content"] for c in batch]
        
        # 🌟 [포맷 변경 대응] 모든 메타데이터 접근을 c["metadata"] 내부 참조 구조로 전면 수정
        # 🌟 [오류 방지 고도화] 특정 회사명/산업군 기본값 제거 및 범용 '알수없음' 가드레일 적용
        metadatas = []
        for c in batch:
            meta_obj = c.get("metadata", {})
            meta = {
                "document_uuid": str(meta_obj.get("document_uuid") or ""),
                "company": str(meta_obj.get("company") or "알수없음"),        # 데이터에 지정된 회사명을 그대로 쓰되, 없으면 안전하게 '알수없음'
                "document_type": str(meta_obj.get("document_type") or "알수없음"),  # 특정 확장자(PDF 등) 오염 방지
                "document_date": str(meta_obj.get("document_date") or ""),
                "sector": str(meta_obj.get("sector") or "알수없음"),          # 특정 산업군(보험 등) 오염 방지
                "chunk_type": str(meta_obj.get("chunk_type") or "text"),
                "page_number": int(meta_obj.get("page_number")) if meta_obj.get("page_number") is not None else -1
            }
            cleaned_meta = {k: (v if v is not None else "") for k, v in meta.items()}
            metadatas.append(cleaned_meta)
            
        # [기능 유지] ChromaDB 컬렉션에 바로 벌크 인서트
        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )
        
        time.sleep(0.1)
        
    print(f"✨ {file_name} -> ChromaDB 적재 완료!")

# (코드 맨 마지막 줄, 들여쓰기 없이 추가)
print("\n🎉 모든 금융 문서의 벡터 정보가 안전하게 ChromaDB에 성공적으로 빌드되었습니다!")