import os
import glob
import json
import shutil
import torch  # ✅ 장치(GPU/Mac) 자동 감지를 위해 추가
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma


def clean_metadata(metadata: dict) -> dict:
    """Chroma가 허용하는 타입(str, int, float, bool)으로 정제"""
    cleaned = {}
    for k, v in metadata.items():
        if v is None:
            cleaned[k] = ""
        elif isinstance(v, list):
            cleaned[k] = ", ".join(str(i) for i in v)
        elif isinstance(v, dict):
            cleaned[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, (str, int, float, bool)):
            cleaned[k] = v
        else:
            cleaned[k] = str(v)
    return cleaned


def load_all_chunked_jsons(folder_path):
    search_pattern = os.path.join(folder_path, "*.json")
    json_files = glob.glob(search_pattern)

    if not json_files:
        print(f"❌ '{folder_path}' 폴더에 JSON 파일이 없습니다.")
        return []

    print(f"✅ 총 {len(json_files)}개의 JSON 파일을 찾았습니다.")
    all_documents = []

    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                chunks = json.load(f)
                for chunk in chunks:
                    doc = Document(
                        page_content=chunk["page_content"],
                        metadata=clean_metadata(chunk["metadata"])  # 타입 정제
                    )
                    all_documents.append(doc)
        except Exception as e:
            print(f"[오류] {os.path.basename(file_path)} 파일 읽기 실패: {e}")

    print(f"✅ 총 {len(all_documents)}개 청크 로딩 완료.")
    return all_documents


def build_vector_db(documents, persist_directory):
    print("\n🚀 BGE-M3 임베딩 모델 로드 중...")
    
    # 장치 자동 감지 (NVIDIA GPU -> cuda, Mac -> mps, 없으면 -> cpu)
    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"💡 사용 장치(Device): {device}")

    embedding_model = HuggingFaceEmbeddings(
        model_name="BAAI/bge-m3",
        model_kwargs={'device': device},  
        encode_kwargs={'normalize_embeddings': True}
    )

    BATCH_SIZE = 50
    vector_db = None

    print("\n📦 Chroma DB 적재 시작...")
    for i in range(0, len(documents), BATCH_SIZE):
        batch = documents[i : i + BATCH_SIZE]
        print(f"  [{min(i+BATCH_SIZE, len(documents))}/{len(documents)}] 배치 처리 중...")

        if vector_db is None:
            vector_db = Chroma.from_documents(
                documents=batch,
                embedding=embedding_model,
                persist_directory=persist_directory,
                collection_name="finmate_bank_docs",
                collection_metadata={"hnsw:space": "cosine"}  # BGE-M3 최적화
            )
        else:
            vector_db.add_documents(batch)

    print(f"\n🎉 DB 구축 완료! 저장 경로: {persist_directory}")
    return vector_db


if __name__ == "__main__":
    # ---------------------------------------------------------
    # 📌 경로 자동 설정 (src/preprocessing/ 폴더 기준)
    # ---------------------------------------------------------
    
    # 1. 현재 이 파이썬 파일이 있는 폴더 경로 알아내기 (.../src/preprocessing)
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
    
    # 2. 프로젝트 최상위 루트 폴더 계산 (두 단계 위로 이동: .../FINMATE-AI-AGENT)
    PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))

    # 3. 사진의 폴더 구조에 맞게 대상 JSON 경로 지정 (data/processed/chunking/json)
    TARGET_DIR = os.path.join(PROJECT_ROOT, "data", "processed", "chunking")
    
    # 4. ChromaDB를 저장할 경로 지정 (data/chroma_db)
    CHROMA_PERSIST_DIR = os.path.join(PROJECT_ROOT, "data", "chroma_db")

    print("=" * 50)
    print(f"📂 대상 JSON 폴더: {TARGET_DIR}")
    print(f"💾 DB 저장 폴더: {CHROMA_PERSIST_DIR}")
    print("=" * 50)

    # ---------------------------------------------------------
    # 📌 중복 적재 방지 및 실행
    # ---------------------------------------------------------
    if os.path.exists(CHROMA_PERSIST_DIR):
        print(f"\n⚠️ '{CHROMA_PERSIST_DIR}' 폴더가 이미 존재합니다.")
        answer = input("기존 DB를 삭제하고 새로 구축할까요? (y/n): ")
        if answer.lower() == 'y':
            shutil.rmtree(CHROMA_PERSIST_DIR)
            print("🗑️ 기존 DB 삭제 완료.\n")
        else:
            print("🛑 작업을 취소하고 종료합니다.")
            exit()

    # JSON 데이터 로드
    docs = load_all_chunked_jsons(TARGET_DIR)
    
    # DB 구축
    if docs:
        build_vector_db(docs, CHROMA_PERSIST_DIR)
    else:
        print("\n❌ 적재할 문서가 없습니다. 경로와 파일 상태를 확인해 주세요.")