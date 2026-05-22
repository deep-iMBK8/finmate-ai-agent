import json
import os
import time

from dotenv import load_dotenv

# GCP 프로젝트 ID
load_dotenv()

PROJECT_ID = os.getenv("PROJECT_ID")
LOCATION = "asia-northeast3"

# 구글 콘솔 토큰
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")

# 라이브러리가 엉뚱한 기본 프로젝트(618104708054)를 찾아가지 못하도록
# 시스템 환경 변수로 진짜 내 프로젝트 ID를 강제 주입합니다.
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GCP_PROJECT"] = PROJECT_ID
os.environ["CLOUDSDK_CORE_PROJECT"] = PROJECT_ID

from google.cloud import aiplatform
from google.oauth2.credentials import Credentials

print("🔄 환경 변수 및 액세스 토큰으로 구글 클라우드 직통 연결 중...")

# 토큰과 쿼터 프로젝트를 2중으로 철저하게 묶어줍니다.
credentials = Credentials(token=ACCESS_TOKEN).with_quota_project(PROJECT_ID)

# Vertex AI 플랫폼 초기화
aiplatform.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)

# lazy loading 버그 방지를 위해 여기서 모듈을 불러옵니다.
from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

print("🤖 구글 Vertex AI 다국어 임베딩 모델 로드 중...")
model = TextEmbeddingModel.from_pretrained("text-multilingual-embedding-002")
print("✅ 모델 로드 성공! 연산을 시작합니다.")

# 3. 폴더 경로 설정
input_dir = "./data/chunks/chunking"
output_dir = "./data/embedding/insurance_embedding"
os.makedirs(output_dir, exist_ok=True)

json_files = [f for f in os.listdir(input_dir) if f.endswith(".json")]
print(f"📂 총 {len(json_files)}개의 파일을 검색했습니다. 크레딧 파워 가동!")

# 4. 파일별 순차 처리
for index, file_name in enumerate(json_files, 1):
    input_path = os.path.join(input_dir, file_name)
    output_path = os.path.join(output_dir, f"vertex_{file_name}")

    if os.path.exists(output_path):
        print(f"[{index}/{len(json_files)}] {file_name}은 이미 완료되어 건너뜁니다.")
        continue

    print(f"🚀 [{index}/{len(json_files)}] {file_name} 구글 클라우드 연산 시작...")
    start_time = time.time()

    with open(input_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    embedding_inputs = [
        TextEmbeddingInput(
            text=f"[페이지 {item['page_number']}] {item['chunk_text']}",
            task_type="RETRIEVAL_DOCUMENT",
        )
        for item in chunks
    ]

    chunk_size = 5
    all_vectors = []

    for i in range(0, len(embedding_inputs), chunk_size):
        batch = embedding_inputs[i : i + chunk_size]
        embeddings = model.get_embeddings(batch)
        all_vectors.extend([emb.values for emb in embeddings])
        time.sleep(0.2)

    for i, vector in enumerate(all_vectors):
        chunks[i]["vector"] = vector

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=4)

    end_time = time.time()
    print(f"✨ {file_name} 완료! (소요시간: {end_time - start_time:.2f}초)")

print("🎉 모든 파일의 구글 클라우드(Vertex AI) 임베딩이 안전하게 끝났습니다!")
