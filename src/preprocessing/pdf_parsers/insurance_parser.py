# TODO:
# 리턴 구조 (document_data) 분리

import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import fitz
import pdfplumber

from src.config.paths import PROCESSED_JSON_DIR
from src.utils.docs_helpers import safe_filename

os.makedirs(PROCESSED_JSON_DIR, exist_ok=True)

def extract_insurance_pdf(pdf_path: Path, metadata: dict = None) -> dict:
    """
    단일 보험 PDF 파일을 입력받아 PyMuPDF와 pdfplumber를 활용해 
    텍스트 및 표를 로컬에서 파싱하고 구조화된 dict를 반환
    """
    if metadata is None:
        metadata = {}

    pdf_path = Path(pdf_path)
    filename = pdf_path.name
    clean_filename = pdf_path.stem

    # 메타데이터 할당 (라우터 입력값 우선, 없으면 파일명 기반 추출)
    report_name = metadata.get("document_title") or filename
    document_uuid = str(uuid.uuid4())

    print(f"\n'[insurance] {filename}' 변환 시작...")

    try:
        # 사용된 파싱 기술 엔진 기록 초기화
        processing_engine = ["PyMuPDF"]

        # -------------------------------------------------
        # 1. PyMuPDF 문서 열기 (텍스트 및 메타 탐색용)
        # -------------------------------------------------
        doc = fitz.open(pdf_path)

        # 회사명 추출 (우선 metadata 확인 후 없으면 텍스트 내 키워드 스캐닝)
        company = metadata.get("company") or ""
        
        if not company:
            company_keywords = [
                "삼성화재", "DB손해보험", "현대해상", "KB손해보험", "메리츠화재",
                "한화손해보험", "롯데손해보험", "흥국화재", "교보생명", "신한라이프", "ABL생명",
            ]

            for page in doc:
                page_text = page.get_text()
                for keyword in company_keywords:
                    if keyword in page_text:
                        company = keyword
                        break
                if company:
                    break

        # 회사명 추출 안 됐을 때
        if not company:
            company = clean_filename

        pages_count = len(doc)

        # 리턴할 데이터 구조
        document_data = {
            "document_uuid": document_uuid,
            "sector": "insurance",
            "document_date": metadata.get("document_date") or "",
            "document_type": metadata.get("document_type") or "PDF",
            "company": company,
            "document_title": report_name,
            "created_at": datetime.now().isoformat(),
            "file_type": "pdf",
            "processing_engine": processing_engine,
            "pages_count": pages_count,
            "pages": [],
        }

        # -------------------------------------------------
        # 2. pdfplumber 문서 열기 (정교한 표 격리 추출용)
        # -------------------------------------------------
        with pdfplumber.open(pdf_path) as plumber_pdf:
            processing_engine.append("pdfplumber")

            for page_idx, page in enumerate(doc, start=1):
                plumber_page = plumber_pdf.pages[page_idx]

                # 페이지 소제목 (일반 PDF 텍스트 레이아웃 특성상 null 처리)
                sub_title = ""

                # TEXT 추출 (PyMuPDF) 및 공백 압축 정제
                text = page.get_text()
                text = re.sub(r"\s+", " ", text).strip()

                # TABLE 추출 (pdfplumber)
                table_list = []
                extracted_tables = plumber_page.extract_tables() or []

                for table_idx, table in enumerate(extracted_tables):
                    table_rows = []
                    for row in table:
                        # 결측치(None) 방어 코드 및 문자열 공백 좌우 정제
                        cleaned_row = [
                            str(cell).strip() if cell is not None else "" for cell in row
                        ]
                        if cleaned_row:
                            table_rows.append(cleaned_row)

                    table_list.append({
                        "table_id": f"{document_uuid}_p{page_idx}_tbl{table_idx}",
                        "rows": table_rows,
                    })

                # IMAGE 추출 (물리 저장은 하지 않고 메타데이터 참조 구조만 매핑)
                image_list = []
                image_infos = page.get_images(full=True) or []

                for img_idx, img in enumerate(image_infos):
                    # 물리 이미지 저장 로직 구현 시 활용할 수 있도록 가상 경로 포맷 구성
                    img_ext = img[1] if len(img) > 1 else "png"
                    img_name = f"{document_uuid}_p{page_idx+1}_img{img_idx}.{img_ext}"
                    
                    image_info = {
                        "image_id": f"{document_uuid}_p{page_idx}_img{img_idx}",
                        "src": f"data/processed/images/{img_name}", 
                        "alt": "PDF 내 추출된 이미지 객체",
                    }
                    image_list.append(image_info)

                # 페이지 레벨 단위 데이터 조립
                page_data = {
                    "page_id": f"{document_uuid}_p{page_idx+1}",
                    "page_number": page_idx + 1,
                    "subtitle": sub_title,
                    "text": text if text else "",
                    "tables": table_list if table_list else [],
                    "images": image_list if image_list else [],
                }
                document_data["pages"].append(page_data)

        # 빈 페이지인 경우
        if not document_data["pages"]:
            print(f"  [-] [{filename}] 페이지 내부 정보 수집에 실패했습니다.")
            return {}

        # 로컬에 파일 저장
        safe_company = safe_filename(company)
        file_path = os.path.join(PROCESSED_JSON_DIR, f"{safe_company}_{document_uuid}.json")

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(document_data, f, ensure_ascii=False, indent=2)

        print(f"JSON 구조화 파일 저장 성공: {file_path}")
        
        # 라우터 반환값 제공
        return document_data

    except Exception as e:
        print(f"Error: {filename} 파싱 중 오류 발생: {e}")
        return {}