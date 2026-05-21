import json
import os
import re
import time
import uuid
from datetime import datetime

import fitz
import pdfplumber

# =====================================================
# PDF 폴더 경로
# =====================================================

PDF_DIR = "./data/raw/pdf/insurance"
DOWNLOAD_DIR = "./data/processed/json"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# =====================================================
# PDF 파일 리스트
# =====================================================

pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]

# =====================================================
# PDF 반복 처리
# =====================================================

for pdf_file in pdf_files:

    pdf_path = os.path.join(PDF_DIR, pdf_file)

    report_name = pdf_file
    rcept_no = "LOCAL_PDF"

    # document uuid 생성
    document_uuid = str(uuid.uuid4())

    print(f"\n[문서 수집] {report_name}")

    try:

        # 사용된 엔진 저장용
        processing_engine = []

        # -------------------------------------------------
        # PyMuPDF 문서 열기
        # -------------------------------------------------

        doc = fitz.open(pdf_path)

        # 사용 엔진 기록
        processing_engine.append("PyMuPDF")

        # =====================================================
        # 회사명 추출
        # =====================================================

        company = ""

        company_keywords = [
            "삼성화재",
            "DB손해보험",
            "현대해상",
            "KB손해보험",
            "메리츠화재",
            "한화손해보험",
            "롯데손해보험",
            "흥국화재",
            "교보생명",
            "신한라이프",
            "ABL생명",
        ]

        for page in doc:

            page_text = page.get_text()

            for keyword in company_keywords:

                if keyword in page_text:

                    company = keyword
                    break

            if company:
                break

        # 못 찾으면 파일명 사용
        if not company:
            company = os.path.splitext(pdf_file)[0]

        # 전체 페이지 수
        pages_count = len(doc)

        # =====================================================
        # 문서 메타데이터
        # =====================================================

        document_data = {
            "document_uuid": document_uuid,
            "sector": "insurance",
            "document_date": "",
            "document_type": "PDF",
            "company": company,
            "document_title": report_name,
            "created_at": datetime.now().isoformat(),
            "file_type": "pdf",
            "processing_engine": processing_engine,
            "pages_count": pages_count,
            "pages": [],
        }

        # -------------------------------------------------
        # pdfplumber 문서 열기 (table 추출용)
        # -------------------------------------------------

        with pdfplumber.open(pdf_path) as plumber_pdf:

            # 사용 엔진 기록
            processing_engine.append("pdfplumber")

            for page_idx in range(len(doc)):

                page = doc[page_idx]
                plumber_page = plumber_pdf.pages[page_idx]

                # =====================================================
                # 페이지 제목 (PDF는 subtitle이 없음)
                # =====================================================

                sub_title = ""

                # =====================================================
                # TEXT 추출
                # =====================================================

                text = page.get_text()

                # 공백 정리
                text = re.sub(r"\s+", " ", text).strip()

                # =====================================================
                # TABLE 추출
                # =====================================================

                table_list = []

                extracted_tables = plumber_page.extract_tables()

                for table_idx, table in enumerate(extracted_tables, start=1):

                    table_rows = []

                    for row in table:

                        cleaned_row = [
                            str(cell).strip() if cell else "" for cell in row
                        ]

                        if cleaned_row:
                            table_rows.append(cleaned_row)

                    table_list.append(
                        {
                            "table_id": f"{document_uuid}_p{page_idx+1}_tbl{table_idx}",
                            "table_index": table_idx,
                            "rows": table_rows,
                        }
                    )

                # =====================================================
                # IMAGE 추출
                # =====================================================

                image_list = []

                image_infos = page.get_images(full=True)

                for img_idx, img in enumerate(image_infos, start=1):

                    xref = img[0]

                    image_info = {
                        "image_id": f"{document_uuid}_p{page_idx+1}_img{img_idx}",
                        "xref": xref,
                    }

                    image_list.append(image_info)

                # =====================================================
                # 페이지 단위 저장
                # =====================================================

                page_data = {
                    "page_id": f"{document_uuid}_p{page_idx+1}",
                    "page_number": page_idx + 1,
                    "subtitle": sub_title,
                    "text": text if text else "",
                    "tables": table_list if table_list else [],
                    "images": image_list if image_list else [],
                }

                document_data["pages"].append(page_data)

    except Exception as e:

        print(f"[-] PDF 문서 추출 중 오류 발생: {e}")
        continue

    # =====================================================
    # 빈 페이지 체크
    # =====================================================

    if not document_data["pages"]:

        print("[-] 페이지 데이터가 비어있습니다.")
        continue

    # =====================================================
    # 파일명 안전 처리
    # =====================================================

    safe_company_name = re.sub(r'[\\/*?:"<>|]', "", company)

    # =====================================================
    # JSON 저장
    # =====================================================

    file_path = os.path.join(DOWNLOAD_DIR, f"{safe_company_name}_{document_uuid}.json")

    with open(file_path, "w", encoding="utf-8") as f:

        json.dump(document_data, f, ensure_ascii=False, indent=2)

    print(f"[+] JSON 저장 완료: {file_path}")

    # 요청 속도 조절
    time.sleep(0.1)
