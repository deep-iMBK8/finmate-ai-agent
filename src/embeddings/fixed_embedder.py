import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv

import chromadb
from google.cloud import aiplatform
from google.oauth2.credentials import Credentials

from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

# 1. 환경 변수 및 GCP 프로젝트 주입
load_dotenv()
PROJECT_ID = os.getenv("PROJECT_ID")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN") # 한 시간 마다 지속적으로 발급 받아야 함
LOCATION = "asia-northeast3"  # 임베딩 모델의 지역 위치(한국-서울)

#구글 라이브러리들이 내부적으로 프로젝트 ID를 찾을 때 쓰는 이름이 제각각이라 세 개 다 명시
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GCP_PROJECT"] = PROJECT_ID
os.environ["CLOUDSDK_CORE_PROJECT"] = PROJECT_ID

print("구글 클라우드 보안 토큰 인증 및 Vertex AI 초기화 중")
credentials = Credentials(token=ACCESS_TOKEN).with_quota_project(PROJECT_ID)
aiplatform.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)

print("구글 Vertex AI 금융/다국어 특화 임베딩 모델 로드 완료.")
model = TextEmbeddingModel.from_pretrained("text-multilingual-embedding-002")

# 2. 크로마 DB 설정
# 크로마 db 저장경로 설정 및 청킹데이터 불러오는 경로 설정
CHROMA_DIR = "./data/vectordb/financial_chroma"
CHUNKS_DIR = "./data/chunks/chunking"

chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

#챗봇의 검색 정확도를 극대화하기 위해 코사인 유사도 설정
# 유클리드 거리 : 긴 청크에서는 거리 기반이라 벡터가 길게 뻗어나가 거리가 멀다고 오해
# 코사인 유사도 : 벡터의 길이는 무시하고 방향만 봐서 같은 주제를 향해 뻗어있다는 각도로 판단
collection = chroma_client.get_or_create_collection(
    name="financial_documents",
    metadata={"hnsw:space": "cosine"} 
)

# 3. 기존의 데이터가 있는데 새로운 데이터 추가할 때 중복을 방지하는 로직
existing_ids = set()
if collection.count() > 0:
    existing = collection.get(include=[])
    existing_ids = set(existing["ids"])
    print(f"현재 ChromaDB에 매핑된 기존 청크: {len(existing_ids)}개 (중복 빌드 스킵 활성화)")

# 4. 파일 검색 및 파이프라인 가동
if not os.path.exists(CHUNKS_DIR):
    os.makedirs(CHUNKS_DIR)

json_files = [f for f in os.listdir(CHUNKS_DIR) if f.endswith(".json")]
print(f"{len(json_files)}개의 금융 청크 파일을 탐색했습니다.")

BATCH_SIZE = 50 # 배치사이즈는 청크 데이터가 클수록 작게 조정(20000개 이상 반환 x) 25~30개

for index, file_name in enumerate(json_files, 1):
    path = os.path.join(CHUNKS_DIR, file_name)
    
    with open(path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
        
    # 청크마다 고유 ID 생성(식별)
    for idx, c in enumerate(chunks):
        meta_obj = c.get("metadata", {})
        doc_uuid = meta_obj.get("document_uuid", "unknown")
        page_num = meta_obj.get("page_number", 0)
        # 기존 스킵/중복방지 로직과 100% 호환되도록 임시 고유 ID 생성
        c["generated_id"] = f"emb_{doc_uuid}_p{page_num}_{idx}"
        
    # generated_id 기준으로 기존 DB 내부 데이터 검색 및 중복 패스
    new_chunks = [c for c in chunks if c["generated_id"] not in existing_ids]
    
    if not new_chunks:
        print(f"➔ [{index}/{len(json_files)}] {file_name}: 새로운 청크가 없어 패스합니다.")
        continue
        
    print(f"[{index}/{len(json_files)}] {file_name} 처리 시작 (대상 청크: {len(new_chunks)}개)")
    
    # 5. 배치 연산으로 임베딩 생성 및 ChromaDB 적재
    for i in range(0, len(new_chunks), BATCH_SIZE):
        batch = new_chunks[i : i + BATCH_SIZE]
        
        # c["chunk_text"] -> c["page_content"]로 매핑 변경(양식에 맞는 컬럼으로 변환)
        embedding_inputs = [
            TextEmbeddingInput(
                text=f"[페이지 {c.get('metadata', {}).get('page_number', 1)}] {c['page_content']}",
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
                    print(f"API 통신 지연 발생 ({attempt + 1}/{max_retries}차 재시도 중... 5초 대기): {e}")
                    time.sleep(5)
                else:
                    print(f"최종 실패. 현재 배치는 건너뜁니다.")
        
        if not embeddings_data:
            continue
            
        embeddings = [emb.values for emb in embeddings_data]
        
        # DB 적재를 위해 뭉쳐 있는 데이터에서 ID 리스트와 본문 리스트를 각각 분리 추출
        ids = [c["generated_id"] for c in batch]
        documents = [c["page_content"] for c in batch]
        
        # 특정 회사명/산업군 기본값 제거 및 범용 '알수없음' 가드레일 적용
        metadatas = []
        for c in batch:
            meta_obj = c.get("metadata", {})
            meta = {
                "document_uuid": str(meta_obj.get("document_uuid") or ""),
                "company": str(meta_obj.get("company") or "알수없음"), # 데이터에 지정된 회사명을 그대로 쓰되, 없으면 안전하게 '알수없음'
                "document_type": str(meta_obj.get("document_type") or "알수없음"), # 특정 확장자(PDF 등) 오염 방지
                "document_date": str(meta_obj.get("document_date") or ""),
                "sector": str(meta_obj.get("sector") or "알수없음"), # 특정 산업군(보험 등) 오염 방지
                "chunk_type": str(meta_obj.get("chunk_type") or "text"),
                "page_number": int(meta_obj.get("page_number")) if meta_obj.get("page_number") is not None else -1
            }
            cleaned_meta = {k: (v if v is not None else "") for k, v in meta.items()}
            metadatas.append(cleaned_meta)
            
        # ChromaDB 컬렉션에 바로 벌크 인서트
        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )
        
        time.sleep(0.1)
        
    print(f"{file_name} -> ChromaDB 적재 완료!")

# 6. 최종 완료
print("\n 모든 금융 문서의 벡터 정보가 안전하게 ChromaDB에 성공적으로 빌드되었습니다!")