# TODO:
# 테이블 구조 보존되게 개선 필요
# 개행문자 \n 처리 필요
# subtitle 없으면 빈문자열로 처리
# 유틸 함수 모듈화 (파일이름 가공 코드 같은 거)
# 프롬프트 파일 분리
# 이미지 alt도 개선 필요 (llm에 이미지 넣어서 alt용 텍스트 추출해달라 하는 등의 방법)
# 이미지도 저장할 건지 논의 필요

import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

import fitz
from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.config.paths import PROCESSED_IMAGE_DIR, PROCESSED_JSON_DIR

# 환경변수 로드
load_dotenv()

google_api_key = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=google_api_key)

os.makedirs(PROCESSED_JSON_DIR, exist_ok=True)
os.makedirs(PROCESSED_IMAGE_DIR, exist_ok=True)

def extract_and_save_images(pdf_path: Path, doc_uuid: str, out_dir: Path) -> dict:
    # PDF에서 물리적인 이미지를 추출하여 로컬에 저장하고 이미지 리스트 반환
    doc = fitz.open(pdf_path)
    img_map = {}

    for p_idx in range(len(doc)):
        page = doc[p_idx]
        img_list = page.get_images(full=True)
        page_imgs = []

        for i_idx, img in enumerate(img_list, start=1):
            xref = img[0]
            base_img = doc.extract_image(xref)
            ext = base_img["ext"]
            img_bytes = base_img["image"]

            f_name = f"{doc_uuid}_p{p_idx + 1}_img{i_idx}.{ext}"
            f_path = os.path.join(out_dir, f_name)

            with open(f_path, "wb") as f:
                f.write(img_bytes)

            page_imgs.append(
                {
                    "image_id": f"{doc_uuid}_p{p_idx + 1}_img{i_idx}",
                    "src": f"data/processed/images/{f_name}",
                    "alt": "PDF 추출 이미지",
                }
            )
        img_map[p_idx + 1] = page_imgs
    return img_map

def extract_bank_pdf(pdf_path: Path, metadata: dict = None) -> dict:
    # PDF 파일을 입력받아 Gemini API로 처리하고 딕셔너리로 반환
    if metadata is None:
        metadata = {}

    pdf_path = Path(pdf_path)
    filename = pdf_path.name
    clean_filename = pdf_path.stem

    # 파일명 분석 (기본값 설정 및 유저 입력 metadata 우선 활용)
    parts = clean_filename.split("_")
    default_company = parts[0] if len(parts) > 0 else "Unknown"
    default_title = parts[1] if len(parts) > 1 else clean_filename

    company = metadata.get("company") or default_company
    document_title = metadata.get("document_title") or default_title

    document_uuid = str(uuid.uuid4())
    current_time = datetime.now().isoformat()

    print(f"\n'[bank] {filename}' 변환 시작...")

    prompt = f"""
    당신은 금융 문서를 분석하는 AI입니다.
    아래 스키마에 완벽하게 일치하도록 데이터를 추출하세요.

    [데이터 할당 규칙]
    1. "document_uuid": "{document_uuid}"
    2. "sector": "bank"
    3. "document_date": 문서 날짜를 "YYYY-MM-DD" 형식으로 기입 (본문에서 찾아 기입하되 없으면 오늘 날짜).
    4. "document_type": 문서 종류 (예: 약관, 설명서)
    5. "company": "{company}"
    6. "document_title": "{document_title}"
    7. "created_at": "{current_time}"
    8. "file_type": "pdf"
    9. "processing_engine": "gemini-3.1-flash-lite"
    10. "pages": 페이지별 배열
        - "page_id": "{document_uuid}_p" 뒤에 번호
        - "page_number": 현재 페이지 번호(정수)
        - "subtitle": 상단 소제목 (없으면 "")
        - "text": 표와 이미지를 제외한 모든 텍스트 원문
        - "tables": 표 데이터 배열
            - "table_id": "page_id_tbl" 뒤에 표 순번
            - "table_index": 페이지 내 표 순번(정수)
            - "rows": 표 내용을 2차원 배열로 분리

    [반드시 준수해야 할 JSON 스키마 구조]
    {{
        "document_uuid": "{document_uuid}",
        "sector": "bank",
        "document_date": "",
        "document_type": "",
        "company": "{company}",
        "document_title": "{document_title}",
        "created_at": "{current_time}",
        "file_type": "pdf",
        "processing_engine": "gemini-3.1-flash-lite",
        "pages_count": 1,
        "pages": [
            {{
                "page_id": "{document_uuid}_p1",
                "page_number": 1,
                "subtitle": "소제목",
                "text": "전체 텍스트...",
                "tables": []
            }}
        ]
    }}
    """

    max_retries = 3
    document_data = None
    
    # 안전한 파일 업로드를 위한 임시 파일 생성
    temp_filename = f"temp_upload_{uuid.uuid4().hex}.pdf"
    shutil.copy(pdf_path, temp_filename)

    for attempt in range(max_retries):
        try:
            pdf_file = client.files.upload(file=temp_filename)
            while pdf_file.state.name == "PROCESSING":
                time.sleep(3)
                pdf_file = client.files.get(name=pdf_file.name)

            response = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=[pdf_file, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )

            document_data = json.loads(response.text)

            # 날짜 포맷 정규화 (- 기호로 통일)
            if document_data.get("document_date"):
                document_data["document_date"] = (
                    document_data["document_date"]
                    .replace(".", "-")
                    .replace("/", "-")
                )

            client.files.delete(name=pdf_file.name)
            break

        except json.JSONDecodeError:
            print(f"  [{filename}] 오류: 유효한 JSON을 반환하지 않았습니다.")
            break
        except Exception as e:
            print(f"  [{filename}] 에러 발생 (시도 {attempt+1}/{max_retries}): {e}")
            try:
                if 'pdf_file' in locals():
                    client.files.delete(name=pdf_file.name)
            except Exception:
                pass
            if attempt < max_retries - 1:
                time.sleep(5)

    # 임시 파일 삭제
    if os.path.exists(temp_filename):
        os.remove(temp_filename)

    # 추출 실패 시 빈 딕셔너리 반환 (기존 continue 버그 수정)
    if not document_data:
        print(f"  [{filename}] 데이터 추출에 실패하여 처리를 중단합니다.")
        return {}

    # 물리 이미지 추출 실행 및 맵핑
    extracted_images = extract_and_save_images(pdf_path, document_uuid, PROCESSED_IMAGE_DIR)

    if "pages" in document_data:
        document_data["pages_count"] = len(document_data["pages"])
        for page_data in document_data["pages"]:
            p_num = page_data.get("page_number", 0)
            page_data["images"] = extracted_images.get(p_num, [])

    # 최종 저장 파일명 처리
    final_company = document_data.get("company", company)
    safe_company = re.sub(r'[\\/*?:"<>|]', "", final_company)

    json_filename = f"{safe_company}_{document_uuid}.json"
    file_path = os.path.join(PROCESSED_JSON_DIR, json_filename)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(document_data, f, ensure_ascii=False, indent=2)

    print(f"JSON 저장 성공: {json_filename}\n")

    return document_data

# def process_all_pdfs():

#     pdf_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith(".pdf")]

#     if not pdf_files:
#         print(f"'{INPUT_DIR}' 폴더에 PDF 파일이 없습니다.")
#         return

#     print(f"총 {len(pdf_files)}개의 PDF 파일을 찾았습니다.")
#     print("JSON 변환 및 이미지 추출을 시작합니다!\n")

#     for filename in pdf_files:
#         pdf_path = os.path.join(INPUT_DIR, filename)

#         clean_filename = filename.replace(".pdf", "").replace(".PDF", "")
#         parts = clean_filename.split("_")

#         company = parts[0] if len(parts) > 0 else "Unknown"
#         document_title = parts[1] if len(parts) > 1 else clean_filename

#         document_uuid = str(uuid.uuid4())
#         current_time = datetime.now().isoformat()

#         print(f"[처리 중] '{filename}' 분석 및 추출 중...")

#         prompt = f"""
#         당신은 금융 문서를 분석하는 AI입니다.
#         아래 스키마에 완벽하게 일치하도록 데이터를 추출하세요.

#         [데이터 할당 규칙]
#         1. "document_uuid": "{document_uuid}"
#         2. "sector": "bank"
#         3. "document_date": 문서 날짜를 "YYYY-MM-DD" 형식으로 기입.
#         4. "document_type": 문서 종류 (예: 약관, 설명서)
#         5. "company": "{company}"
#         6. "document_title": "{document_title}"
#         7. "created_at": "{current_time}"
#         8. "file_type": "pdf"
#         9. "processing_engine": "gemini-3.1-flash-lite"
#         10. "pages": 페이지별 배열
#             - "page_id": "{document_uuid}_p" 뒤에 번호
#             - "page_number": 현재 페이지 번호(정수)
#             - "subtitle": 상단 소제목 (없으면 "")
#             - "text": 표와 이미지를 제외한 모든 텍스트 원문
#             - "tables": 표 데이터 배열
#                 - "table_id": "page_id_tbl" 뒤에 표 순번
#                 - "table_index": 페이지 내 표 순번(정수)
#                 - "rows": 표 내용을 2차원 배열로 분리

#         [반드시 준수해야 할 JSON 스키마 구조]
#         {{
#           "document_uuid": "{document_uuid}",
#           "sector": "bank",
#           "document_date": "",
#           "document_type": "",
#           "company": "회사명",
#           "document_title": "문서 제목",
#           "created_at": "{current_time}",
#           "file_type": "pdf",
#           "processing_engine": "gemini-3.1-pro",
#           "pages_count": 1,
#           "pages": [
#             {{
#               "page_id": "{document_uuid}_p1",
#               "page_number": 1,
#               "subtitle": "소제목",
#               "text": "전체 텍스트...",
#               "tables": []
#             }}
#           ]
#         }}
#         """

#         max_retries = 3
#         document_data = None
#         temp_filename = f"temp_upload_{uuid.uuid4().hex}.pdf"
#         shutil.copy(pdf_path, temp_filename)

#         for attempt in range(max_retries):
#             try:
#                 pdf_file = client.files.upload(file=temp_filename)
#                 while pdf_file.state.name == "PROCESSING":
#                     time.sleep(3)
#                     pdf_file = client.files.get(name=pdf_file.name)

#                 response = client.models.generate_content(
#                     model="gemini-3.1-flash-lite",
#                     contents=[pdf_file, prompt],
#                     config=types.GenerateContentConfig(
#                         response_mime_type="application/json",
#                     ),
#                 )

#                 document_data = json.loads(response.text)

#                 if document_data.get("document_date"):
#                     document_data["document_date"] = (
#                         document_data["document_date"]
#                         .replace(".", "-")
#                         .replace("/", "-")
#                     )

#                 client.files.delete(name=pdf_file.name)
#                 break

#             except json.JSONDecodeError:
#                 print(f"  [{filename}] 오류: 유효한 JSON을 반환하지 않았습니다.")
#                 break
#             except Exception as e:
#                 print(f"  [{filename}] 에러 발생 (시도 {attempt+1}): {e}")
#                 try:
#                     if "pdf_file" in locals():
#                         client.files.delete(name=pdf_file.name)
#                 except Exception:
#                     pass
#                 if attempt < max_retries - 1:
#                     time.sleep(5)

#         if os.path.exists(temp_filename):
#             os.remove(temp_filename)

#         if not document_data:
#             continue

#         extracted_images = extract_and_save_images(pdf_path, document_uuid, PROCESSED_IMAGE_DIR)

#         if "pages" in document_data:
#             document_data["pages_count"] = len(document_data["pages"])
#             for page_data in document_data["pages"]:
#                 p_num = page_data.get("page_number", 0)
#                 page_data["images"] = extracted_images.get(p_num, [])

#         final_company = document_data.get("company", company)
#         safe_company = re.sub(r'[\\/*?:"<>|]', "", final_company)

#         json_filename = f"{safe_company}_{document_uuid}.json"
#         file_path = os.path.join(PROCESSED_JSON_DIR, json_filename)

#         with open(file_path, "w", encoding="utf-8") as f:
#             json.dump(document_data, f, ensure_ascii=False, indent=2)

#         print(f"  완료! -> JSON({json_filename}) 및 이미지 분리 저장 성공\n")

#     print("모든 PDF 파일 작업이 완료되었습니다!")


# if __name__ == "__main__":
#     process_all_pdfs()
