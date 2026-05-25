import json
import mimetypes
import os
import uuid
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types

from src.config.paths import PROCESSED_JSON_DIR, PROCESSED_TXT_DIR
from src.utils.docs_helpers import safe_filename


class GeminiOCREngine:
    def __init__(self, project: str, location: str = "global"):
        # Vertex AI 클라이언트 초기화
        self.client = genai.Client(vertexai=True, project=project, location=location)

    def process_image(self, image_path: Path, metadata: dict, model_name: str = "gemini-2.5-flash") -> dict:
        # 1단계: 순수 텍스트 및 마크다운 표 OCR 추출
        raw_text = self.extract_raw_ocr_text(image_path, model_name)
        
        # 2단계: 1차 결과를 기반으로 계층형 JSON 스키마 빌드
        document_data = self.build_structured_json(image_path, raw_text, model_name, metadata)
        
        # 3단계: TXT 및 JSON 파일을 로컬에 저장
        self.save_outputs(image_path, raw_text, document_data)
        
        # 완성된 JSON 데이터 리턴
        return document_data

    def extract_raw_ocr_text(self, image_path: Path, model_name: str) -> str:
        """1단계: 이미지에서 1차 순수 텍스트 및 마크다운 표 OCR 추출"""
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        
        prompt = """
        너는 금융 문서 OCR 전문가이다.

        이미지에 있는 모든 텍스트를 빠짐없이 추출해라.

        규칙:
        1. 원문 순서를 최대한 유지해라.
        2. 표는 Markdown 표 형식으로 복원해라.
        3. 체크박스는 체크된 경우 [x], 체크되지 않은 경우 [ ] 로 표시해라.
        4. 금액, 날짜, 금리, 수수료, 계좌번호, 상품명 등 금융 핵심 정보는 절대 누락하지 마라.
        5. 알아보기 어려운 글자는 추측하지 말고 [인식불가]로 표시해라.
        6. 설명을 덧붙이지 말고, 추출된 문서 내용만 출력해라.
        """

        response = self.client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime_type),
                prompt
            ]
        )
        return (response.text or "").strip()

    def build_structured_json(self, image_path: Path, ocr_text: str, model_name: str, metadata: dict) -> dict:
        """2단계: 1차 OCR 결과를 바탕으로 완벽한 청킹/RAG용 계층 JSON 스키마를 빌드"""
        document_uuid = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        file_type = image_path.suffix.lstrip(".").lower()

        prompt = f"""
        당신은 금융 문서 OCR 결과를 청킹/RAG용 계층 구조 JSON으로 정리하는 데이터 정제 전문가입니다.
        아래 입력된 OCR 텍스트를 분석하여, 제공된 [출력 스키마] 규칙에 완벽히 맞는 JSON 데이터만 생성하세요.

        [중요 규칙]
        1. 지정한 스키마 구조 외에 설명문이나 다른 문장을 절대 포함하지 마세요. (순수 JSON만 출력)
        2. document_uuid, page_id, table_id는 상위 시스템에서 정의하므로 JSON 내에 임의의 해시값을 생성해 넣지 말고, 아래 지정한 형식을 엄격히 따르세요.
        3. 표 데이터는 오직 'tables' 항목 내부의 'rows' (2차원 리스트) 형태로만 격리해야 합니다. 
        4. 본문 내용인 'text' 항목에서는 마크다운 표 형태(| ... |)의 문자열 라인을 모두 제외한 일반 줄글 중심의 텍스트만 모아서 정제해 넣으세요.
        5. 문서 안에서 회사명, 보고서/문서 종류 이름, 자체 문서 일자(YYYY-MM-DD)를 파싱해 상위 필드에 바인딩하세요. 찾을 수 없다면 null로 비워두세요.
        6. 단일 이미지 파일 분석이므로 pages 리스트 안의 요소는 1개이며 page_number는 1입니다.

        [출력 스키마 예시]
        {{
          "corp_name": "문서에 명시된 금융회사명 또는 자산운용사명 (없으면 null)",
          "report_name": "문서 종류 또는 제목 (없으면 null)",
          "rcept_no": "접수 번호 등이 존재하면 기재 (없으면 null)",
          "document_date": "문서 내 기재된 날짜 YYYY-MM-DD 형식 (없으면 null)",
          "pages": [
            {{
              "page_number": 1,
              "subtitle": "페이지 소제목 또는 주요 헤더 (없으면 null)",
              "text": "표 영역을 제외하고 정제된 일반 본문 문장 중심 텍스트",
              "tables": [
                {
                  "table_index": 1,
                  "rows": [
                    ["헤더1", "헤더2"],
                    ["값1", "값2"]
                  ]
                }
              ],
              "images": [
                {
                    "image_index": 1, 
                    "src": "src", 
                    "alt": "이미지에 대한 설명"
                }
              ]
            }}
          ]
        }}

        [참고 문맥 정보]
        - 원본 파일명: {image_path.name}
        - 유저 입력 섹터: {metadata.get("sector", "bank")}

        [원본 OCR 텍스트]
        {ocr_text}
        """

        response = self.client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        
        # 텍스트 마크다운 태그 정제 및 파싱
        json_text = response.text.strip().removeprefix("```json").removesuffix("```").strip()
        ai_data = json.loads(json_text)

        # 시스템 ID 규칙 바인딩 및 정규화 구조 조립
        page_number = 1
        page_id = f"{document_uuid}_p{page_number}"

        normalized_tables = []
        for idx, tbl in enumerate(ai_data.get("pages", [{}])[0].get("tables", []), start=1):
            normalized_tables.append({
                "table_id": f"{page_id}_tbl{idx}",
                "table_index": idx,
                "rows": [[str(cell) if cell is not None else "" for cell in row] if isinstance(row, list) else [str(row)] for row in tbl.get("rows", [])]
            })

        structured_document = {
            "document_uuid": document_uuid,
            "corp_name": ai_data.get("corp_name") or metadata.get("company") or "null",
            "report_name": ai_data.get("report_name") or metadata.get("document_title") or image_path.stem,
            "rcept_no": ai_data.get("rcept_no") or "null",
            "created_at": created_at,
            "pages": [
                {
                    "page_id": page_id,
                    "page_number": page_number,
                    "subtitle": ai_data.get("pages", [{}])[0].get("subtitle") or ai_data.get("report_name") or image_path.stem,
                    "text": ai_data.get("pages", [{}])[0].get("text") or "",
                    "tables": normalized_tables,
                    "images": []
                }
            ]
        }
        return structured_document

    def save_outputs(self, image_path: Path, ocr_text: str, json_data: dict) -> tuple[Path, Path]:
        """3단계: 요구사항에 맞게 파일명 지정 및 경로 상수를 사용하여 물리 저장"""
        # 파일명 제어 규칙: 회사명_uuid.json (특수문자 안전 처리)
        corp_name = str(json_data.get("corp_name", "unknown")).strip()
        if corp_name.lower() == "null" or not corp_name:
            corp_name = "unknown"
            
        safe_company = safe_filename(corp_name)
            
        file_base_name = f"{safe_company}_{json_data['document_uuid']}"

        txt_output_path = PROCESSED_TXT_DIR / f"{image_path.stem}.txt"
        json_output_path = PROCESSED_JSON_DIR / f"{file_base_name}.json"
        txt_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.parent.mkdir(parents=True, exist_ok=True)

        # 저장 실행
        txt_output_path.write_text(ocr_text, encoding="utf-8")
        json_output_path.write_text(
            json.dumps(json_data, indent=2, ensure_ascii=False), 
            encoding="utf-8"
        )

        return txt_output_path, json_output_path

# def main():
#     # 1. 경로 보장 및 환경 변수 빌드
#     load_dotenv()

#     project = os.getenv("GOOGLE_CLOUD_PROJECT")
#     location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

#     if not project:
#         raise RuntimeError(".env 환경변수에 GOOGLE_CLOUD_PROJECT 설정을 확인하세요.")

#     # 2. 엔진 활성화
#     engine = GeminiOCREngine(project=project, location=location)

#     # 3. 로우 데이터 저장소 타겟팅 스캔
#     print(f"Target Scanning: {RAW_IMAGE_DIR}")
    
#     valid_extensions = {".jpg", ".jpeg", ".png", ".webp"}
#     for img_path in RAW_IMAGE_DIR.glob("*"):
#         if img_path.suffix.lower() not in valid_extensions:
#             continue

#         print(f"\n파일 처리 중...: {img_path.name}")
        
#         try:
#             # 기본 메타데이터 세팅 피딩 정보
#             feed_metadata = {
#                 "sector": "은행",
#                 "company": None, 
#                 "document_title": None
#             }

#             # 파이프라인 처리
#             raw_text = engine.extract_raw_ocr_text(img_path, "gemini-2.5-flash")
#             structured_data = engine.build_structured_json(img_path, raw_text, "gemini-2.5-flash", feed_metadata)

#             # 규칙 기반 파일명 매핑 저장
#             txt_file, json_file = engine.save_outputs(img_path, raw_text, structured_data)
            
#             print(f"[+] TXT 저장 완료 -> {txt_file.name}")
#             print(f"[+] JSON 저장 완료 -> {json_file.name}")

#         except Exception as err:
#             print(f"[에러] Process Aborted for {img_path.name}: {err}")

# if __name__ == "__main__":
#     main()

# class GeminiOCREngine:
#     def __init__(self, project, location="global"):
#         # Vertex AI 클라이언트 초기화
#         self.client = genai.Client(vertexai=True, project=project, location=location)

#     def extract_structured_data(self, image_path: Path, model_name: str, metadata: dict) -> dict:
#         # OCR하고 정해진 구조로 json 반환
#         prompt = """
#         너는 금융 문서 OCR 전문가이다.

#         이미지에 있는 모든 텍스트를 빠짐없이 추출해라.

#         규칙:
#         1. 원문 순서를 최대한 유지해라.
#         2. 표는 Markdown 표 형식으로 복원해라.
#         3. 체크박스는 체크된 경우 [x], 체크되지 않은 경우 [ ] 로 표시해라.
#         4. 금액, 날짜, 금리, 수수료, 계좌번호, 상품명 등 금융 핵심 정보는 절대 누락하지 마라.
#         5. 알아보기 어려운 글자는 추측하지 말고 [인식불가]로 표시해라.
#         6. 설명을 덧붙이지 말고, 추출된 문서 내용만 출력해라.
#         """
        
#         # 이미지 전송
#         response = self.client.models.generate_content(
#             model=model_name,
#             contents=[types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/jpeg"), prompt]
#         )
        
#         # 결과 파싱
#         raw_result = json.loads(response.text.replace("```json", "").replace("```", "").strip())
        
#         # 요청하신 스키마로 조립
#         page_id = str(uuid.uuid4())
#         structured_data = {
#             "document_uuid": str(uuid.uuid4()),
#             "user_id": metadata.get("user_id", "unknown"),
#             "sector": metadata.get("sector", "unknown"),
#             "document_date": metadata.get("document_date", datetime.now().strftime("%Y-%m-%d")),
#             "document_type": metadata.get("document_type", "unknown"),
#             "company": metadata.get("company", "unknown"),
#             "document_title": metadata.get("document_title", "unknown"),
#             "created_at": datetime.now().isoformat(),
#             "file_type": "image",
#             "processing_engine": model_name,
#             "pages_count": 1,
#             "pages": [
#                 {
#                     "page_id": page_id,
#                     "page_number": 1,
#                     "subtitle": metadata.get("document_title", "Untitled"),
#                     "text": raw_result["text"],
#                     "tables": raw_result["tables"],
#                 }
#             ],
#         }
#         return structured_data

#     def save_results(self, image_path: Path, data: dict):
#         base_name = image_path.stem
#         json_file = PROCESSED_JSON_DIR / f"{base_name}.json"
#         json_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
#         return json_file