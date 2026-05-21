import argparse
import io
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text_to_fp
from pdfminer.layout import LAParams


DEFAULT_INPUT_DIR = "data/raw"
SUPPORTED_EXTENSIONS = {".pdf", ".html", ".htm", ".xml", ".txt"}
DEFAULT_OUTPUT_DIR = "data/processed/ocr_text"


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


def collect_input_files(input_dir: str):
    input_path = Path(input_dir)

    if not input_path.exists():
        raise FileNotFoundError(f"입력 폴더가 없습니다: {input_dir}")

    files = [
        p for p in input_path.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    return sorted(files)


# ==================================================
# HTML / PDF 파싱 함수
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
    """
    PDF를 pdfminer로 HTML 변환한 결과 또는 HTML/XML/TXT 내용을
    pages 구조로 변환한다.

    pdfminer HTML에는 page 단위 div가 명확하지 않을 수도 있으므로,
    1차: page 관련 div 탐색
    2차: 전체 문서를 1페이지로 처리
    """
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


def extract_pages_from_html_file(file_path: str, document_uuid: str):
    file_path = Path(file_path)
    html_content = file_path.read_text(encoding="utf-8", errors="ignore")

    return split_text_pages_from_html(
        html_content=html_content,
        title=file_path.name,
        document_uuid=document_uuid,
    )


def extract_pages_from_plain_text(file_path: str, document_uuid: str):
    file_path = Path(file_path)
    text = clean_text(file_path.read_text(encoding="utf-8", errors="ignore"))

    return [
        {
            "page_id": f"{document_uuid}_p1",
            "page_number": 1,
            "subtitle": infer_subtitle(text, fallback=file_path.name),
            "text": text,
            "tables": [],
        }
    ]


def extract_pages_from_file(file_path: str, document_uuid: str):
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return extract_pages_from_pdf(str(file_path), document_uuid=document_uuid)

    if suffix in {".html", ".htm", ".xml"}:
        return extract_pages_from_html_file(str(file_path), document_uuid=document_uuid)

    if suffix == ".txt":
        return extract_pages_from_plain_text(str(file_path), document_uuid=document_uuid)

    raise ValueError(f"지원하지 않는 파일 형식입니다: {suffix}")


# ==================================================
# 추론 / 메타데이터 추출 함수
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
    title_keywords = [
        "신청서",
        "투자설명서",
        "증권신고서",
        "약관",
        "동의서",
        "설명서",
        "정정신고",
        "보고",
    ]

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


def infer_sector(text: str, fallback: str = "bank"):
    investment_keywords = [
        "투자설명서",
        "증권신고서",
        "집합투자",
        "펀드",
        "자산운용",
        "투자위험",
        "총보수",
    ]

    card_keywords = [
        "카드",
        "신용카드",
        "체크카드",
        "이용약관",
        "연회비",
    ]

    bank_keywords = [
        "은행",
        "예금",
        "적금",
        "전자금융",
        "계좌",
        "이자",
        "금리",
    ]

    if any(keyword in text for keyword in investment_keywords):
        return "investment"

    if any(keyword in text for keyword in card_keywords):
        return "card"

    if any(keyword in text for keyword in bank_keywords):
        return "bank"

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
        "정정신고",
    ]

    return [term for term in candidate_terms if term in text]


def infer_processing_engine(source_path: Path):
    suffix = source_path.suffix.lower()

    if suffix == ".pdf":
        return "pdfminer_html_beautifulsoup"

    if suffix in {".html", ".htm", ".xml"}:
        return "beautifulsoup"

    if suffix == ".txt":
        return "plain_text"

    return "unknown"


# ==================================================
# JSON 생성 / 저장 함수
# ==================================================
def build_rag_document(
    source_path: str,
    pages: list,
    txt_path: str,
    document_uuid: str = None,
    user_id: str = None,
    document_sector: str = "bank",
    document_date: str = None,
    document_type: str = None,
    company: str = "",
    document_title: str = None,
    extra_metadata: dict = None,
):
    source_path = Path(source_path)

    document_uuid = document_uuid or str(uuid.uuid4())
    user_id = user_id or source_path.stem
    document_date = document_date or datetime.now().date().isoformat()

    full_text = pages_to_full_text(pages)

    inferred_sector = infer_sector(full_text, fallback=document_sector)
    final_sector = document_sector if document_sector else inferred_sector

    final_title = document_title or infer_title(full_text, fallback=source_path.stem)
    final_company = infer_company(full_text, fallback=company)
    final_document_type = document_type or final_title
    file_type = source_path.suffix.lower().replace(".", "")

    document = {
        "document_uuid": document_uuid,
        "user_id": user_id,
        "sector": final_sector,
        "document_date": document_date,
        "document_type": final_document_type,
        "company": final_company,
        "document_title": final_title,
        "created_at": datetime.now().isoformat(),
        "file_type": file_type,
        "processing_engine": infer_processing_engine(source_path),
        "pages_count": len(pages),
        "pages": pages,
        "metadata": {
            "customer_name": extract_customer_name(full_text),
            "checked_items": extract_checked_items(full_text),
            "source_file": str(source_path),
            "source_txt": str(txt_path),
            "key_terms": extract_key_terms(full_text),
        },
    }

    if extra_metadata:
        document["metadata"].update(extra_metadata)

    return document


def save_text_and_json(
    source_path: str,
    pages: list,
    output_dir: str,
    document_uuid: str = None,
    user_id: str = None,
    document_sector: str = "bank",
    document_date: str = None,
    document_type: str = None,
    company: str = "",
    document_title: str = None,
    extra_metadata: dict = None,
):
    source_path = Path(source_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = safe_filename(document_uuid or source_path.stem)
    txt_path = output_dir / f"{base_name}.txt"
    json_path = output_dir / f"{base_name}.json"

    full_text = pages_to_full_text(pages)
    txt_path.write_text(full_text, encoding="utf-8")

    rag_document = build_rag_document(
        source_path=str(source_path),
        pages=pages,
        txt_path=str(txt_path),
        document_uuid=document_uuid,
        user_id=user_id,
        document_sector=document_sector,
        document_date=document_date,
        document_type=document_type,
        company=company,
        document_title=document_title,
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
):
    document_uuid = str(uuid.uuid4())

    pages = extract_pages_from_file(
        file_path=file_path,
        document_uuid=document_uuid,
    )

    txt_path, json_path = save_text_and_json(
        source_path=file_path,
        pages=pages,
        output_dir=output_dir,
        document_uuid=document_uuid,
        user_id=user_id,
        document_sector=document_sector,
        document_date=document_date,
        document_type=document_type,
        company=company,
        document_title=document_title,
    )

    full_text = pages_to_full_text(pages)

    print(full_text[:300].replace("\n", " ") + " ... (후략)")
    print(f"[+] TXT 저장 완료: {txt_path}")
    print(f"[+] JSON 저장 완료: {json_path}")

    return full_text, txt_path, json_path


# ==================================================
# CLI
# ==================================================
def build_parser():
    parser = argparse.ArgumentParser(
        description="PDF/HTML/XML/TXT 문서를 pages 기반 RAG JSON/TXT로 저장합니다."
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

    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="파싱 결과 저장 폴더"
    )

    parser.add_argument(
        "--user-id",
        default=None,
        help="JSON에 저장할 user_id. 지정하지 않으면 파일명을 사용"
    )

    parser.add_argument(
        "--document-sector",
        default="",
        help="문서 업권/분야. 예: bank, card, investment. 비우면 텍스트에서 추정"
    )

    parser.add_argument(
        "--document-date",
        default=None,
        help="문서 날짜. 지정하지 않으면 오늘 날짜를 사용"
    )

    parser.add_argument(
        "--document-type",
        default=None,
        help="문서 유형. 예: 투자설명서, 약관, 신청서"
    )

    parser.add_argument(
        "--company",
        default="",
        help="회사명. 지정하지 않으면 텍스트에서 추정"
    )

    parser.add_argument(
        "--document-title",
        default=None,
        help="문서 제목. 지정하지 않으면 텍스트에서 추정"
    )

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
        output_dir = Path(args.output_dir)

        # 기존 구조에서는 파일명 기반 저장이었지만,
        # 새 구조는 UUID 기반 파일명으로 저장된다.
        # overwrite 체크를 위해 같은 stem으로 시작하는 json/txt가 있으면 건너뛰도록 처리한다.
        existing_json_files = list(output_dir.glob(f"*{base_name}*.json")) if output_dir.exists() else []
        existing_txt_files = list(output_dir.glob(f"*{base_name}*.txt")) if output_dir.exists() else []

        if not args.overwrite and existing_json_files and existing_txt_files:
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