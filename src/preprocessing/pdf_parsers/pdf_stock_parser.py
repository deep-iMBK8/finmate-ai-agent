# TODO:
# 유틸 함수 분리 필요
# 리턴 구조 (document_data) 분리 필요
# 표 구조 유지 안 됨 html parser 필요할듯
# "company":"TIGER미국S&P500배당귀족" 회사명 추출 제대로 안 됨

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

import pdfplumber

from src.config.paths import PROCESSED_JSON_DIR
from src.utils.docs_helpers import clean_text, safe_filename

PROCESSED_JSON_DIR.mkdir(parents=True, exist_ok=True)


def infer_company_from_filename(pdf_path: Path) -> str:
    stem = pdf_path.stem
    parts = stem.split("_")
    if len(parts) >= 2:
        return parts[1]
    return stem


def infer_document_date(pdf_path: Path) -> str:
    match = re.search(r"(20\d{2})[.\-_]?(0\d|1[0-2])[.\-_]?([0-2]\d|3[01])", pdf_path.stem)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    return datetime.now().strftime("%Y-%m-%d")


def extract_tables_from_page(page, document_uuid: str, page_idx: int) -> list:
    table_list = []
    extracted_tables = page.extract_tables() or []

    for table_idx, table in enumerate(extracted_tables, start=1):
        rows = []
        for row in table:
            cleaned_row = [clean_text(cell, keep_newlines=False) for cell in row]
            if any(cleaned_row):
                rows.append(cleaned_row)

        table_list.append(
            {
                "table_id": f"{document_uuid}_p{page_idx}_tbl{table_idx}",
                "table_index": table_idx,
                "rows": rows,
            }
        )
    return table_list


# ==================================================
# 메인 함수
# ==================================================
def extract_stock_pdf(pdf_path: Path, metadata: dict = None) -> dict:
    """
    단일 PDF 파일을 입력받아 pdfplumber를 활용해 
    텍스트 및 표 데이터를 파싱하고 구조화된 딕셔너리를 반환 및 저장
    """
    if metadata is None:
        metadata = {}

    pdf_path = Path(pdf_path)
    filename = pdf_path.name
    
    document_uuid = str(uuid.uuid4())
    print(f"\n'[stock] {filename}' 변환 시작...")

    try:
        # 1. 파일 유효성(PDF 바이너리 헤더) 검출 가드 코드
        with open(pdf_path, "rb") as f:
            header = f.read(20)
        if not header.startswith(b"%PDF"):
            raise ValueError(f"PDF 헤더가 유효하지 않습니다. header={header!r}")

        # 2. 메타데이터 결정 (우선순위: 외부 라우터 주입 metadata > 파일명/본문 추론)
        company = metadata.get("company") or infer_company_from_filename(pdf_path)
        document_title = metadata.get("document_title") or pdf_path.stem
        document_date = metadata.get("document_date") or infer_document_date(pdf_path)
        document_type = metadata.get("document_type") or "투자설명서"

        # 3. 데이터 적재 스키마 초기화
        document_data = {
            "document_uuid": document_uuid,
            "sector": "stock",
            "document_date": document_date,
            "document_type": document_type,
            "company": company,
            "document_title": document_title,
            "created_at": datetime.now().isoformat(),
            "file_type": "pdf",
            "processing_engine": "pdfplumber",
            "pages_count": 0,
            "pages": [],
        }

        # 4. pdfplumber 엔진 열기 및 페이지별 추출
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                text = clean_text(page.extract_text() or "", keep_newlines=False)
                tables = extract_tables_from_page(page, document_uuid, page_idx)

                page_data = {
                    "page_id": f"{document_uuid}_p{page_idx}",
                    "page_number": page_idx,
                    "subtitle": "",
                    "text": text,
                    "tables": tables,
                    "images": [],  # 일단 이미지 메타 구조 레이아웃만 유지
                }
                document_data["pages"].append(page_data)

        # 전체 페이지 카운트 업데이트
        document_data["pages_count"] = len(document_data["pages"])

        # 5. 파일시스템 안전 변환 및 물리 저장 디렉토리 결합
        safe_company = safe_filename(company)
        output_path = PROCESSED_JSON_DIR / f"{safe_company}_{document_uuid}.json"

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(document_data, f, ensure_ascii=False, indent=2)

        print(f"JSON 저장 성공: {output_path}")
        return document_data

    except Exception as e:
        print(f"Error: [{filename}] 파싱 중 오류 발생: {e}")
        return {}