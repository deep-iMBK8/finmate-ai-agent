import argparse
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text_to_fp
from pdfminer.layout import LAParams

DEFAULT_INPUT_DIR = "data/raw"
SUPPORTED_EXTENSIONS = {".pdf", ".html", ".htm", ".xml", ".txt"}
DEFAULT_OUTPUT_DIR = "data/processed/ocr_text"
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120


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


def table_to_text(rows):
    table_rows = []

    for row in rows:
        cells = []
        for cell in row:
            cell_text = clean_text(str(cell)) if cell is not None else "-"
            cells.append(cell_text if cell_text else "-")

        if any(cell != "-" for cell in cells):
            table_rows.append(" | ".join(cells))

    if not table_rows:
        return ""

    return "\n\n[----- TABLE START -----]\n" + "\n".join(table_rows) + "\n[----- TABLE END -----]\n\n"


def extract_text_from_html(html_content: str, title: str = "HTML 문서") -> str:
    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)

        table_text = table_to_text(rows)
        if table_text:
            table.replace_with(table_text)

    text = soup.get_text(separator="\n", strip=True)
    return f"=== {title} ===\n{clean_text(text)}\n"


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


def extract_text_from_pdf(pdf_path: str) -> str:
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 파일이 없습니다: {pdf_path}")

    html_content = pdf_to_html(str(pdf_path))
    return extract_text_from_html(html_content, title=pdf_path.name)

def collect_input_files(input_dir: str):
    input_path = Path(input_dir)

    if not input_path.exists():
        raise FileNotFoundError(f"입력 폴더가 없습니다: {input_dir}")

    files = [
        p for p in input_path.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    return sorted(files)


def extract_text_from_file(file_path: str) -> str:
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return extract_text_from_pdf(str(file_path))

    if suffix in {".html", ".htm", ".xml", ".txt"}:
        html_content = file_path.read_text(encoding="utf-8", errors="ignore")
        return extract_text_from_html(html_content, title=file_path.name)

    raise ValueError(f"지원하지 않는 파일 형식입니다: {suffix}")


def split_text_into_chunks(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP):
    clean = text.strip()
    if not clean:
        return []

    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap은 chunk_size보다 작아야 합니다.")

    chunks = []
    start = 0

    while start < len(clean):
        end = min(start + chunk_size, len(clean))
        chunk_text = clean[start:end].strip()

        if chunk_text:
            chunks.append({"chunk_id": len(chunks) + 1, "text": chunk_text})

        if end >= len(clean):
            break

        start = end - chunk_overlap

    return chunks


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
        "신한은행",
        "국민은행",
        "우리은행",
        "하나은행",
        "농협은행",
        "기업은행",
        "카카오뱅크",
        "토스뱅크",
        "미래에셋자산운용",
        "삼성자산운용",
        "KB자산운용",
        "신한자산운용",
        "한화자산운용",
    ]

    for company in companies:
        if company in text:
            return company

    if fallback:
        return fallback

    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def infer_title(text: str, fallback: str):
    title_keywords = ["신청서", "투자설명서", "증권신고서", "약관", "동의서", "설명서"]

    for line in text.splitlines():
        line = line.strip()
        if any(keyword in line for keyword in title_keywords):
            return line

    return fallback


def extract_key_terms(text: str):
    candidate_terms = [
        "예금자보호",
        "전자금융",
        "접근매체",
        "접근매체 양도 금지",
        "재예치",
        "입주자저축",
        "비과세",
        "수수료",
        "금리",
        "이자",
        "자동이체",
        "개인정보",
        "신용정보",
        "투자위험",
        "총보수",
        "집합투자",
        "증권신고서",
        "투자설명서",
    ]

    return [term for term in candidate_terms if term in text]


def build_rag_document(
    source_path: str,
    full_text: str,
    txt_path: str,
    document_id: str = None,
    user_id: str = None,
    document_sector: str = "bank",
    document_date: str = None,
    document_type: str = None,
    company: str = "",
    document_title: str = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    extra_metadata: dict = None,
):
    source_path = Path(source_path)
    document_id = document_id or source_path.stem
    user_id = user_id or document_id
    document_date = document_date or datetime.now().date().isoformat()
    final_title = document_title or infer_title(full_text, fallback=document_id)
    final_company = infer_company(full_text, fallback=company)
    final_document_type = document_type or final_title

    metadata = {
        "customer_name": extract_customer_name(full_text),
        "checked_items": extract_checked_items(full_text),
        "source_file": str(source_path),
        "source_txt": str(txt_path),
        "parser": "pdfminer_html_beautifulsoup" if source_path.suffix.lower() == ".pdf" else "beautifulsoup",
        "created_at": datetime.now().isoformat(),
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    return {
        "document_id": document_id,
        "user_id": user_id,
        "document_sector": document_sector,
        "document_date": document_date,
        "document_type": final_document_type,
        "company": final_company,
        "document_title": final_title,
        "full_text": full_text,
        "key_terms": extract_key_terms(full_text),
        "chunks": split_text_into_chunks(full_text, chunk_size, chunk_overlap),
        "metadata": metadata,
    }


def save_text_and_json(
    source_path: str,
    full_text: str,
    output_dir: str,
    document_id: str = None,
    user_id: str = None,
    document_sector: str = "bank",
    document_date: str = None,
    document_type: str = None,
    company: str = "",
    document_title: str = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    extra_metadata: dict = None,
):
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = safe_filename(document_id or source_path.stem)
    txt_path = output_dir / f"{base_name}.txt"
    json_path = output_dir / f"{base_name}.json"

    txt_path.write_text(full_text, encoding="utf-8")

    rag_document = build_rag_document(
        source_path=str(source_path),
        full_text=full_text,
        txt_path=str(txt_path),
        document_id=document_id,
        user_id=user_id,
        document_sector=document_sector,
        document_date=document_date,
        document_type=document_type,
        company=company,
        document_title=document_title,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        extra_metadata=extra_metadata,
    )

    json_path.write_text(
        json.dumps(rag_document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return txt_path, json_path


def save_parsed_file(
    file_path: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    user_id: str = None,
    document_sector: str = "bank",
    document_date: str = None,
    document_type: str = None,
    company: str = "",
    document_title: str = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
):
    full_text = extract_text_from_file(file_path)
    txt_path, json_path = save_text_and_json(
        source_path=file_path,
        full_text=full_text,
        output_dir=output_dir,
        user_id=user_id,
        document_sector=document_sector,
        document_date=document_date,
        document_type=document_type,
        company=company,
        document_title=document_title,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    print(full_text[:300].replace("\n", " ") + " ... (후략)")
    print(f"[+] TXT 저장 완료: {txt_path}")
    print(f"[+] JSON 저장 완료: {json_path}")
    return full_text, txt_path, json_path


def build_parser():
    parser = argparse.ArgumentParser(
        description="개인 PDF/HTML/XML/TXT 문서를 BeautifulSoup 기반으로 파싱해 RAG JSON/TXT로 저장합니다."
    )
    parser.add_argument(
    "--input-dir",
    default=DEFAULT_INPUT_DIR,
    help="파싱할 PDF/HTML/XML/TXT 파일들이 들어있는 폴더"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용 처리 개수 제한"
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="기존 결과가 있어도 다시 저장"
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--document-sector", default="bank")
    parser.add_argument("--document-date", default=None)
    parser.add_argument("--document-type", default=None)
    parser.add_argument("--company", default="")
    parser.add_argument("--document-title", default=None)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    input_files = collect_input_files(args.input_dir)

    if args.limit is not None:
        input_files = input_files[:args.limit]

    print("==============================")
    print("문서 파싱 시작")
    print("==============================")
    print(f"입력 폴더: {args.input_dir}")
    print(f"저장 폴더: {args.output_dir}")
    print(f"처리 대상 파일 수: {len(input_files)}")

    success_count = 0
    skip_count = 0
    fail_count = 0

    for idx, file_path in enumerate(input_files, start=1):
        base_name = safe_filename(file_path.stem)
        txt_path = Path(args.output_dir) / f"{base_name}.txt"
        json_path = Path(args.output_dir) / f"{base_name}.json"

        if not args.overwrite and txt_path.exists() and json_path.exists():
            print(f"[{idx}/{len(input_files)}] 건너뜀: {file_path}")
            skip_count += 1
            continue

        print(f"[{idx}/{len(input_files)}] 파싱 시작: {file_path}")

        try:
            save_parsed_file(
                file_path=str(file_path),
                output_dir=args.output_dir,
                user_id=args.user_id or file_path.stem,
                document_sector=args.document_sector,
                document_date=args.document_date,
                document_type=args.document_type,
                company=args.company,
                document_title=args.document_title,
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
            )

            success_count += 1

        except Exception as e:
            print(f"[실패] {file_path}")
            print(e)
            fail_count += 1

    print("\n==============================")
    print("문서 파싱 완료")
    print("==============================")
    print(f"성공: {success_count}")
    print(f"건너뜀: {skip_count}")
    print(f"실패: {fail_count}")
    print(f"저장 위치: {args.output_dir}")


if __name__ == "__main__":
    main()
