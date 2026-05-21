import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import pdfplumber


BASE_DIR = Path(__file__).resolve().parents[2]
PDF_DIR = BASE_DIR / "data" / "raw" / "pdf" / "stock"
OUTPUT_DIR = BASE_DIR / "data" / "processed" / "json" / "stock"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", "", name)
    return name if name else "unknown_company"


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
            cleaned_row = [clean_text(cell) for cell in row]
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


def extract_pdf_to_json(pdf_file: Path) -> dict:
    document_uuid = str(uuid.uuid4())
    company = infer_company_from_filename(pdf_file)
    document_title = pdf_file.stem
    document_date = infer_document_date(pdf_file)
    created_at = datetime.now().isoformat()

    document_data = {
        "document_uuid": document_uuid,
        "sector": "stock",
        "document_date": document_date,
        "document_type": "투자설명서",
        "company": company,
        "document_title": document_title,
        "created_at": created_at,
        "file_type": "pdf",
        "processing_engine": "pdfplumber",
        "pages_count": 0,
        "pages": [],
    }

    with open(pdf_file, "rb") as f:
        header = f.read(20)

    if not header.startswith(b"%PDF"):
        raise ValueError(f"PDF 헤더가 아닙니다: {pdf_file} | header={header!r}")

    with pdfplumber.open(pdf_file) as pdf:
        for page_idx, page in enumerate(pdf.pages, start=1):
            text = clean_text(page.extract_text() or "")
            tables = extract_tables_from_page(page, document_uuid, page_idx)

            page_data = {
                "page_id": f"{document_uuid}_p{page_idx}",
                "page_number": page_idx,
                "subtitle": "",
                "text": text,
                "tables": tables,
                "images": [],
            }

            document_data["pages"].append(page_data)

    document_data["pages_count"] = len(document_data["pages"])

    safe_company = safe_filename(company)
    output_path = OUTPUT_DIR / f"{safe_company}_{document_uuid}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(document_data, f, ensure_ascii=False, indent=2)

    print(f"[+] JSON 저장 완료: {output_path}")
    return document_data


def main() -> None:
    pdf_files = list(PDF_DIR.glob("*.pdf"))

    if not pdf_files:
        raise FileNotFoundError(f"{PDF_DIR} 폴더에 PDF 파일이 없습니다.")

    for pdf_file in pdf_files:
        print(f"처리 중: {pdf_file}")
        try:
            extract_pdf_to_json(pdf_file)
        except Exception as exc:
            print(f"[-] 처리 실패: {pdf_file.name} | {exc}")


if __name__ == "__main__":  
    main()
