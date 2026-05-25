import os
from dotenv import load_dotenv
import chromadb
from google.cloud import aiplatform
from google.oauth2.credentials import Credentials
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

# 1. 환경 변수 및 GCP 프로젝트 주입
load_dotenv()
PROJECT_ID = os.getenv("PROJECT_ID")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN") # 한시간짜리 유효 코드 한 시간 지날 때 마다 계속 발급
LOCATION = "asia-northeast3"

#구글 라이브러리들이 내부적으로 프로젝트 ID를 찾을 때 쓰는 이름이 제각각이라 세 개 다 명시
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GCP_PROJECT"] = PROJECT_ID
os.environ["CLOUDSDK_CORE_PROJECT"] = PROJECT_ID

# 2. Vertex AI 초기화 및 임베딩 모델 로드
credentials = Credentials(token=ACCESS_TOKEN).with_quota_project(PROJECT_ID)
aiplatform.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)
model = TextEmbeddingModel.from_pretrained("text-multilingual-embedding-002")

# 3. 로컬 크로마 DB 연결 (적재했던 폴더 경로 지정)
CHROMA_DIR = "./data/vectordb/financial_chroma"
chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

# 적재했던 크로마DB 데이터 불러오기
collection = chroma_client.get_collection(name="financial_documents")
print(f"로컬 ChromaDB 연결 성공! (현재 저장된 총 청크 수: {collection.count()}개)\n")

# 4. 질문 생성
user_query = "미래에셋TIGER미국S&P500배당귀족증권상장지수투자신탁을 상품의 내용을 요약해줘"

print(f"질문: \"{user_query}\"")
print("질문을 벡터로 변환하여 로컬 DB에서 유사도 검색 중...")

# 5. 질문 임베딩 생성 (Task Type은 RETRIEVAL_QUERY로 설정 -> 이거 안하면 오류남)
query_input = TextEmbeddingInput(text=user_query, task_type="RETRIEVAL_QUERY") #중요!!
query_embedding = model.get_embeddings([query_input])[0].values

# 6. ChromaDB에서 가장 유사한 상위 3개 문서 검색(코사인 유사도 기반)
results = collection.query(
    query_embeddings=[query_embedding],
    n_results=3,  # 가장 연관성 높은 3개 청크 가져오기
    include=["documents", "metadatas", "distances"]
)

# 7. 검색 결과 출력
print("\n" + "="*60)
print("로컬 DB에서 찾아낸 가장 관련성 높은 문서 내용")
print("="*60)

if not results["documents"][0]:
    print("일치하는 정답 청크를 찾지 못했습니다. 데이터 적재를 먼저 확인해 주세요.")
else:
    for i in range(len(results["documents"][0])):
        doc = results["documents"][0][i]
        meta = results["metadatas"][0][i]
        distance = results["distances"][0][i]  # 코사인 거리가 작을수록(0에 가까울수록) 유사도가 높음
        
        print(f"\n[결과 {i+1}] (유사도 거리 점수: {distance:.4f})")
        print(f"회사명: {meta.get('company') or '알수없음'} | 페이지: {meta.get('page_number')}p")
        print(f"문서 고유 ID: {results['ids'][0][i]}")
        print("-" * 40)
        print(doc)
        print("-" * 40)

print("\n" + "="*60)