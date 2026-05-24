# TODO:
# 유틸 함수 분리 필요
# document_data 분리 필요
# 개행문자 \n 처리 필요
# "metadata" 키 있음 - 구조 맞추기. 넣을 건지 뺄 건지

import io
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text_to_fp
from pdfminer.layout import LAParams

from src.config.paths import PROCESSED_JSON_DIR

os.makedirs(PROCESSED_JSON_DIR, exist_ok=True)

# ==================================================
# 기본 유틸 함수
# ==================================================
def clean_text(text: str) -> str:
    text = text or ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def safe_filename(text: str) -> str:
    text = str(text)
    text = re.sub(r'[\\/*?:"<>|]', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")


# ==================================================
# HTML / PDF 파싱 핵심 엔진 함수
# ==================================================
def pdf_to_html(pdf_path: str) -> str:
    output = io.StringIO()
    with open(pdf_path, "rb") as pdf_file:
        extract_text_to_fp(
            pdf_file,
            output,
            laparams=LAParams(),
            output_type="html",
            codec=None,
        )
    return output.getvalue()


def extract_html_tables(soup: BeautifulSoup, document_uuid: str, page_number: int):
    tables = []
    for table_index, table in enumerate(soup.find_all("table"), start=1):
        rows = []
        for tr in table.find_all("tr"):
            cells = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in tr.find_all(["td", "th"])
            ]
            if cells and any(cells):
                rows.append(cells)
        if rows:
            tables.append(
                {
                    "table_id": f"{document_uuid}_p{page_number}_tbl{table_index}",
                    "table_index": table_index,
                    "rows": rows,
                }
            )
    return tables


def remove_unnecessary_tags(soup: BeautifulSoup):
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup


def extract_text_without_tables(soup: BeautifulSoup):
    copied_soup = BeautifulSoup(str(soup), "html.parser")
    for table in copied_soup.find_all("table"):
        table.decompose()
    text = copied_soup.get_text(separator="\n", strip=True)
    return clean_text(text)


def split_text_pages_from_html(html_content: str, title: str, document_uuid: str):
    soup = BeautifulSoup(html_content, "html.parser")
    soup = remove_unnecessary_tags(soup)

    page_candidates = []
    for div in soup.find_all("div"):
        style = div.get("style", "")
        class_name = " ".join(div.get("class", []))

        if "page" in class_name.lower() or "position:relative" in style.replace(" ", "").lower():
            page_text = div.get_text(" ", strip=True)
            if page_text:
                page_candidates.append(div)

    if not page_candidates:
        page_candidates = [soup]

    pages = []
    for page_number, page_soup in enumerate(page_candidates, start=1):
        page_tables = extract_html_tables(
            soup=page_soup,
            document_uuid=document_uuid,
            page_number=page_number,
        )
        page_text = extract_text_without_tables(page_soup)

        if not page_text and not page_tables:
            continue

        subtitle = infer_subtitle(page_text, fallback=title)
        pages.append(
            {
                "page_id": f"{document_uuid}_p{page_number}",
                "page_number": page_number,
                "subtitle": subtitle,
                "text": page_text,
                "tables": page_tables,
            }
        )

    if not pages:
        pages.append(
            {
                "page_id": f"{document_uuid}_p1",
                "page_number": 1,
                "subtitle": title,
                "text": "",
                "tables": [],
                "images": [], 
            }
        )
    return pages


def extract_pages_from_pdf(pdf_path: str, document_uuid: str):
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 파일이 없습니다: {pdf_path}")
    html_content = pdf_to_html(str(pdf_path))
    return split_text_pages_from_html(
        html_content=html_content,
        title=pdf_path.name,
        document_uuid=document_uuid,
    )


# ==================================================
# 추론 / 금융 메타데이터 추출 함수
# ==================================================
def pages_to_full_text(pages: list):
    text_parts = []
    for page in pages:
        if page.get("text"):
            text_parts.append(page["text"])
        for table in page.get("tables", []):
            for row in table.get("rows", []):
                text_parts.append(" | ".join(str(cell) for cell in row))
    return clean_text("\n\n".join(text_parts))


def extract_customer_name(text: str):
    patterns = [
        r"성\s*명\s*\(.*?\)\s*Name\s*\|\s*([^|\n]+)",
        r"고객\s*성명\s*([^\n|]+)",
        r"성\s*명\s*[:：]?\s*([^\n|]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def extract_checked_items(text: str):
    checked_items = {}
    for line in text.splitlines():
        if "[x]" not in line.lower():
            continue
        cleaned = line.strip().strip("|").strip()
        if cleaned:
            checked_items[f"item_{len(checked_items) + 1}"] = cleaned
    return checked_items


def infer_company(text: str, fallback: str = ""):
    companies = [
        "신한카드", "국민카드", "삼성카드", "현대카드", "롯데카드", "우리카드", "하나카드", "비씨카드",
        "신한은행", "국민은행", "우리은행", "하나은행", "농협은행", "기업은행"
    ]
    for company in companies:
        if company in text:
            return company
    if fallback:
        return fallback
    return next((line.strip() for line in text.splitlines() if line.strip()), "Unknown")


def infer_title(text: str, fallback: str):
    title_keywords = ["신청서", "투자설명서", "증권신고서", "약관", "동의서", "설명서", "이용안내"]
    for line in text.splitlines():
        line = line.strip()
        if any(keyword in line for keyword in title_keywords):
            return line
    return fallback


def infer_subtitle(text: str, fallback: str = ""):
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return fallback


def extract_key_terms(text: str):
    candidate_terms = [
        "예금자보호", "전자금융", "접근매체", "수수료", "금리", "이자", "자동이체", 
        "개인정보", "신용정보", "연회비", "이용약관", "철회권", "기한의이익"
    ]
    return [term for term in candidate_terms if term in text]

# ------------------
# 메인 함수
# ------------------
def extract_card_pdf(pdf_path: Path, metadata: dict = None) -> dict:
    """
    단일 카드 PDF 파일을 입력받아 pdfminer HTML 렌더링 기반으로 
    텍스트 및 표 데이터를 파싱하고 구조화된 딕셔너리 반환 및 저장
    """
    if metadata is None:
        metadata = {}

    pdf_path = Path(pdf_path)
    filename = pdf_path.name
    
    document_uuid = str(uuid.uuid4())
    print(f"\n[card] '{filename}' 변환 시작...")

    try:
        # 1. HTML 변환 엔진 가동 및 페이지/표 완전 분리 추출
        pages = extract_pages_from_pdf(str(pdf_path), document_uuid=document_uuid)
        full_text = pages_to_full_text(pages)

        # 2. 메타데이터 바인딩 (라우터 연동 규격 매핑)
        user_id = metadata.get("user_id") or pdf_path.stem
        document_date = metadata.get("document_date") or datetime.now().date().isoformat()
        
        final_title = metadata.get("document_title") or infer_title(full_text, fallback=pdf_path.stem)
        final_company = metadata.get("company") or infer_company(full_text, fallback="Unknown")
        final_document_type = metadata.get("document_type") or "약관/설명서"

        # 3. RAG용 데이터 스키마
        document_data = {
            "document_uuid": document_uuid,
            "user_id": user_id,
            "sector": "card", 
            "document_date": document_date,
            "document_type": final_document_type,
            "company": final_company,
            "document_title": final_title,
            "created_at": datetime.now().isoformat(),
            "file_type": "pdf",
            "processing_engine": ["pdfminer", "beautifulsoup"],
            "pages_count": len(pages),
            "pages": pages,
            "metadata": {
                "customer_name": extract_customer_name(full_text),
                "checked_items": extract_checked_items(full_text),
                "source_file": str(pdf_path),
                "key_terms": extract_key_terms(full_text),
            },
        }

        # 4. 파일시스템 안전 변환 및 물리 디렉토리 저장 조립
        safe_company_name = safe_filename(final_company)
        if not safe_company_name or safe_company_name == "_":
            safe_company_name = "unknown"

        # 최종 가이드 주소인 PROCESSED_JSON_DIR에 저장하도록 매핑
        json_filename = f"{safe_company_name}_{document_uuid}.json"
        json_path = PROCESSED_JSON_DIR / json_filename
        
        # 순수 텍스트 평문 백업 파일 매핑
        txt_filename = f"{safe_company_name}_{document_uuid}.txt"
        txt_path = PROCESSED_JSON_DIR / txt_filename

        # 파일 물리 쓰기 처리
        txt_path.write_text(full_text, encoding="utf-8")
        json_path.write_text(
            json.dumps(document_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"JSON 및 백업 TXT 저장 성공: {json_path}")

        return document_data

    except Exception as e:
        print(f"Error: [{filename}] 카드 PDF 문서 파싱 중 오류 발생: {e}")
        return {}