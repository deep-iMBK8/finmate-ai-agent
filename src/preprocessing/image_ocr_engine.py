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

        # 라우터 매핑용 섹터 영문명 자동 변환 사전 준비
        sector_mapping = {"은행": "bank", "카드": "card", "보험": "insurance", "투자": "stock"}
        user_sector_kr = metadata.get("sector", "은행")
        default_sector_eng = sector_mapping.get(user_sector_kr, "bank")

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
          "sector": "입력된 데이터가 속한 금융 업권 코드. ('bank', 'card', 'insurance', 'stock' 중 알맞은 값)",
          "document_date": "문서 내 기재된 기준일자 혹은 발행날짜 YYYY-MM-DD 형식 (원문에서 식별 불가하면 null)",
          "document_type": "문서의 상세 대분류 유형 명사 ('상품설명서', '핵심설명서', '약관', '대출계약서', '가입제안서', '영수증', '명세서', '통장사본' 등)",
          "company": "문서에 명시된 금융회사명 또는 자산운용사명 (예: KB국민은행, 현대카드 등, 없으면 null)",
          "document_title": "문서 종류 또는 메인 대제목 (없으면 null)",
          "pages": [
            {{
              "page_number": 1,
              "subtitle": "페이지 소제목 또는 헤더 (없으면 null)",
              "text": "표 영역을 제외하고 정제된 일반 본문 줄글 중심 텍스트",
              "tables": [
                {{
                  "table_index": 0,
                  "rows": [
                    ["헤더1", "헤더2"],
                    ["값1", "값2"]
                  ]
                }}
              ]
            }}
          ]
        }}

        [참고 문맥 정보]
        - 원본 파일명: {image_path.name}
        - 권장 지정 섹터값: {default_sector_eng}

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
        
        try:
            ai_data = json.loads(json_text)
        except Exception:
            # 안전장치 예외 처리용 폴백(Fallback) 구조
            ai_data = {}

        # 기존 스키마 키 불일치 및 안전 바인딩 처리 개편
        sector_val = ai_data.get("sector") or default_sector_eng
        company_val = ai_data.get("company") or metadata.get("company") or "unknown"
        doc_title_val = ai_data.get("document_title") or metadata.get("document_title") or image_path.stem
        doc_date_val = ai_data.get("document_date") or None
        doc_type_val = ai_data.get("document_type") or "이미지 OCR"

        page_number = 1
        page_id = f"{document_uuid}_p{page_number}"

        # 1개의 단일 페이지만 존재하므로 안전하게 첫 번째 요소 추출
        ai_pages = ai_data.get("pages", [{}])
        first_page = ai_pages[0] if isinstance(ai_pages, list) and ai_pages else {}

        normalized_tables = []
        for idx, tbl in enumerate(first_page.get("tables", []), start=1):
            if isinstance(tbl, dict):
                rows_data = tbl.get("rows", [])
                cleaned_rows = []
                if isinstance(rows_data, list):
                    for row in rows_data:
                        if isinstance(row, list):
                            cleaned_rows.append([str(cell) if cell is not None else "" for cell in row])
                        else:
                            cleaned_rows.append([str(row)])
                
                normalized_tables.append({
                    "table_id": f"{page_id}_tbl{idx}",
                    "table_index": idx,
                    "rows": cleaned_rows
                })

        # NameError 대비 내부 리스트를 사전에 객체화
        pages_list = [
            {
                "page_id": page_id,
                "page_number": page_number,
                "subtitle": first_page.get("subtitle") or doc_title_val,
                "text": first_page.get("text") or "",
                "tables": normalized_tables,
            }
        ]

        # 정형화된 공통 순수 딕셔너리 데이터 구조 구축
        structured_document = {
            "document_uuid": document_uuid,
            "sector": sector_val,
            "document_date": doc_date_val,
            "document_type": doc_type_val,
            "company": company_val,
            "document_title": doc_title_val,
            "created_at": created_at,
            "file_type": file_type,
            "processing_engine": ["gemini-ocr"], 
            "pages_count": len(pages_list),
            "pages": pages_list,
            "metadata": {
                "source_file": str(image_path)
            }
        }
        return structured_document

    def save_outputs(self, image_path: Path, ocr_text: str, json_data: dict) -> tuple[Path, Path]:
        """3단계: 요구사항에 맞게 파일명 지정 및 경로 상수를 사용하여 물리 저장"""

        company_name = str(json_data.get("company", "unknown")).strip()
        if company_name.lower() == "null" or not company_name:
            company_name = "unknown"
            
        safe_company = safe_filename(company_name)
            
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