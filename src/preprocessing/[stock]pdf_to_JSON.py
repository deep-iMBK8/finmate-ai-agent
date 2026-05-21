from pathlib import Path

pdf_dir = Path("data")
pdf_files = list(pdf_dir.glob("*.pdf"))

if not pdf_files:
    raise FileNotFoundError("data 폴더에 PDF 파일이 없습니다.")

for pdf_path in pdf_files:
    print(f"처리 중: {pdf_path}")

import os
import re
import json
import time
import uuid
from datetime import datetime
from bs4 import BeautifulSoup

DOWNLOAD_DIR = "./processed/json/investment"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", "", name)
    return name if name else "unknown_company"


def normalize_date(date_value) -> str:
    if not date_value:
        return ""

    if isinstance(date_value, datetime):
        return date_value.strftime("%Y-%m-%d")

    date_str = str(date_value).strip()

    if re.fullmatch(r"\d{8}", date_str):
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return date_str

    if re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", date_str):
        return date_str.replace(".", "-")

    return date_str


def resolve_document_date(report: dict, filing_obj) -> str:
    candidates = [
        report.get("document_date"),
        report.get("rcept_dt"),
        getattr(filing_obj, "rcept_dt", None),
        getattr(filing_obj, "rcp_dt", None),
    ]

    for candidate in candidates:
        normalized = normalize_date(candidate)
        if normalized:
            return normalized

    return datetime.now().strftime("%Y-%m-%d")


def extract_single_report_to_json(report: dict) -> dict:
    corp_name = report["corp_name"]
    report_name = report["report_name"]
    rcept_no = report["rcept_no"]
    filing_obj = report["filing"]

    document_uuid = str(uuid.uuid4())
    created_at = datetime.now().isoformat()
    document_date = resolve_document_date(report, filing_obj)

    print(f"\n[문서 수집] {corp_name} | {report_name}")

    document_data = {
        "document_uuid": document_uuid,
        "sector": "investment",
        "document_date": document_date,
        "document_type": "투자설명서",
        "company": corp_name,
        "document_title": report_name,
        "created_at": created_at,
        "file_type": "html",
        "processing_engine": "beautifulsoup",
        "pages_count": 0,
        "pages": []
    }

    try:
        pages = filing_obj.pages

        for page_idx, page in enumerate(pages, start=1):
            sub_title = page.title if getattr(page, "title", None) else ""
            html_content = page.html if getattr(page, "html", None) else ""

            soup = BeautifulSoup(html_content, "html.parser")

            table_list = []
            for table_idx, table in enumerate(soup.find_all("table"), start=1):
                table_rows = []

                for row in table.find_all("tr"):
                    cells = [
                        clean_text(cell.get_text(strip=True))
                        for cell in row.find_all(["td", "th"])
                    ]
                    if any(cells):
                        table_rows.append(cells)

                table_list.append({
                    "table_id": f"{document_uuid}_p{page_idx}_tbl{table_idx}",
                    "table_index": table_idx,
                    "rows": table_rows
                })

            image_list = []
            for img_idx, img in enumerate(soup.find_all("img"), start=1):
                image_list.append({
                    "image_id": f"{document_uuid}_p{page_idx}_img{img_idx}",
                    "src": img.get("src", ""),
                    "alt": img.get("alt", "")
                })

            soup_for_text = BeautifulSoup(html_content, "html.parser")
            for table in soup_for_text.find_all("table"):
                table.decompose()

            text = soup_for_text.get_text(separator="\n", strip=True)
            text = clean_text(text)

            page_data = {
                "page_id": f"{document_uuid}_p{page_idx}",
                "page_number": page_idx,
                "subtitle": sub_title,
                "text": text,
                "tables": table_list,
                "images": image_list
            }

            document_data["pages"].append(page_data)

    except Exception as e:
        print(f"[-] {rcept_no} 문서 추출 중 오류 발생: {e}")
        return {}

    if not document_data["pages"]:
        print("[-] 페이지 데이터가 비어있습니다.")
        return {}

    document_data["pages_count"] = len(document_data["pages"])

    safe_corp_name = safe_filename(corp_name)
    file_path = os.path.join(DOWNLOAD_DIR, f"{safe_corp_name}_{document_uuid}.json")

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(document_data, f, ensure_ascii=False, indent=2)

    print(f"[+] JSON 저장 완료: {file_path}")
    return document_data

print(pdf_file)

with open(pdf_file, "rb") as f:
    header = f.read(100)

print(header)

import json
import uuid
import re
from google.colab import files
from datetime import datetime

document_data = {
    "document_uuid": str(uuid.uuid4()),
    "company": "미래에셋자산운용",
    "document_date": "2026-04-30",
    "created_at": datetime.now().isoformat(),
    "pages": []
}

safe_company = re.sub(r'[\\/*?:"<>|]', "", document_data["company"]).replace(" ", "")
file_name = f"{safe_company}_{document_data['document_uuid']}.json"

with open(file_name, "w", encoding="utf-8") as f:
    json.dump(document_data, f, ensure_ascii=False, indent=2)

files.download(file_name)

import json
import uuid
import re
from datetime import datetime
import pdfplumber
from google.colab import files

document_uuid = str(uuid.uuid4())
company = "미래에셋자산운용"
document_date = "2026-04-30"

document_data = {
    "document_uuid": document_uuid,
    "company": company,
    "document_date": document_date,
    "created_at": datetime.now().isoformat(),
    "pages": []
}

with pdfplumber.open(pdf_file) as pdf:
    for page_idx, page in enumerate(pdf.pages, start=1):
        text = page.extract_text() or ""

        page_data = {
            "page_id": f"{document_uuid}_p{page_idx}",
            "page_number": page_idx,
            "subtitle": "",
            "text": text,
            "tables": [],
            "images": []
        }

        document_data["pages"].append(page_data)

safe_company = re.sub(r'[\\/*?:"<>|]', "", company).replace(" ", "")
file_name = f"{safe_company}_{document_uuid}.json"

with open(file_name, "w", encoding="utf-8") as f:
    json.dump(document_data, f, ensure_ascii=False, indent=2)

files.download(file_name)