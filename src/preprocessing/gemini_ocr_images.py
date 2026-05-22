import os
import json
import time
import argparse
import re
import uuid
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from google import genai
from google.genai import types


# ==================================================
# 환경변수 로드
# ==================================================
load_dotenv()


# ==================================================
# OCR 프롬프트
# ==================================================
OCR_PROMPT = """
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


# ==================================================
# 이미지 확장자
# ==================================================
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp"
}


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise ValueError("GEMINI_API_KEY가 없습니다. .env 파일을 확인하세요.")

    return genai.Client(api_key=api_key)


# ==================================================
# 이미지 파일 수집
# ==================================================
def collect_image_files(image_dir: str):
    image_path = Path(image_dir)

    if not image_path.exists():
        raise FileNotFoundError(f"이미지 폴더가 없습니다: {image_dir}")

    files = [
        p for p in image_path.rglob("*")
        if p.suffix.lower() in IMAGE_EXTENSIONS
    ]

    return sorted(files)


# ==================================================
# Gemini OCR 실행
# ==================================================
def run_gemini_ocr(client, image_path: Path, model_name: str):
    image_bytes = image_path.read_bytes()

    mime_type = "image/jpeg"

    if image_path.suffix.lower() == ".png":
        mime_type = "image/png"
    elif image_path.suffix.lower() == ".webp":
        mime_type = "image/webp"
    elif image_path.suffix.lower() == ".bmp":
        mime_type = "image/bmp"

    response = client.models.generate_content(
        model=model_name,
        contents=[
            types.Part.from_bytes(
                data=image_bytes,
                mime_type=mime_type
            ),
            OCR_PROMPT
        ]
    )

    return response.text


# ==================================================
# 텍스트 추출 보조 함수
# ==================================================
def extract_customer_name(ocr_text: str):
    patterns = [
        r"성\s*명\s*\(.*?\)\s*Name\s*\|\s*([^|\n]+)",
        r"고객\s*성명\s*([^\n|]+)",
        r"성\s*명\s*[:：]?\s*([^\n|]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, ocr_text)
        if match:
            return match.group(1).strip()

    return ""


def extract_checked_items(ocr_text: str):
    checked_items = {}

    for line in ocr_text.splitlines():
        if "[x]" not in line.lower():
            continue

        cleaned = line.strip().strip("|").strip()
        if not cleaned:
            continue

        checked_items[f"item_{len(checked_items) + 1}"] = cleaned

    return checked_items


def infer_company(ocr_text: str, fallback: str = ""):
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
        if company in ocr_text:
            return company

    if fallback:
        return fallback

    return next((line.strip() for line in ocr_text.splitlines() if line.strip()), "")


def infer_title(ocr_text: str, fallback: str):
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

    for line in ocr_text.splitlines():
        line = line.strip()
        if any(keyword in line for keyword in title_keywords):
            return line

    return fallback


def infer_subtitle(ocr_text: str, fallback: str = ""):
    for line in ocr_text.splitlines():
        line = line.strip()
        if line:
            return line

    return fallback


def extract_key_terms(ocr_text: str):
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

    return [term for term in candidate_terms if term in ocr_text]


# ==================================================
# 표 처리 함수
# ==================================================
def extract_markdown_tables(ocr_text: str, document_uuid: str, page_number: int = 1):
    """
    Gemini OCR 결과 안에 Markdown 표가 있을 경우 tables 구조로 변환한다.

    예:
    | 항목 | 내용 |
    | --- | --- |
    | 성명 | 김찬규 |

    결과:
    {
      "table_id": "...",
      "table_index": 1,
      "rows": [
        ["항목", "내용"],
        ["성명", "김찬규"]
      ]
    }
    """
    tables = []
    current_table = []

    for line in ocr_text.splitlines():
        stripped = line.strip()

        if "|" in stripped:
            current_table.append(stripped)
        else:
            if current_table:
                tables.append(current_table)
                current_table = []

    if current_table:
        tables.append(current_table)

    parsed_tables = []

    for table_index, table_lines in enumerate(tables, start=1):
        rows = []

        for line in table_lines:
            # Markdown 표 구분선 제거: | --- | --- |
            if re.fullmatch(r"[\|\-\s:]+", line):
                continue

            cells = [
                cell.strip()
                for cell in line.strip("|").split("|")
            ]

            if any(cells):
                rows.append(cells)

        if rows:
            parsed_tables.append(
                {
                    "table_id": f"{document_uuid}_p{page_number}_tbl{table_index}",
                    "table_index": table_index,
                    "rows": rows,
                }
            )

    return parsed_tables


def remove_markdown_tables_from_text(ocr_text: str):
    """
    pages[].text에는 일반 문장 중심 텍스트를 넣기 위해
    Markdown 표 라인은 제외한다.
    """
    lines = []
    in_table = False

    for line in ocr_text.splitlines():
        stripped = line.strip()

        if "|" in stripped:
            in_table = True
            continue

        if in_table and "|" not in stripped:
            in_table = False

        if not in_table and stripped:
            lines.append(stripped)

    return " ".join(lines).strip()


# ==================================================
# JSON 생성
# ==================================================
def build_rag_document(
    image_path: Path,
    txt_path: Path,
    ocr_text: str,
    user_id: str,
    model_name: str,
    status: str,
    document_sector: str,
    document_date: str,
    document_type: str,
    company: str,
    document_title: str,
    error_message: str = None,
):
    document_uuid = str(uuid.uuid4())

    inferred_title = infer_title(ocr_text, fallback=image_path.stem)
    final_title = document_title or inferred_title
    final_company = infer_company(ocr_text, fallback=company)
    final_document_type = document_type or final_title
    file_type = image_path.suffix.lower().replace(".", "")

    page_number = 1
    page_id = f"{document_uuid}_p{page_number}"

    page_text = remove_markdown_tables_from_text(ocr_text)
    page_tables = extract_markdown_tables(
        ocr_text=ocr_text,
        document_uuid=document_uuid,
        page_number=page_number,
    )

    return {
        "document_uuid": document_uuid,
        "user_id": user_id,
        "sector": document_sector,
        "document_date": document_date,
        "document_type": final_document_type,
        "company": final_company,
        "document_title": final_title,
        "created_at": datetime.now().isoformat(),
        "file_type": file_type,
        "processing_engine": model_name,
        "pages_count": 1,
        "pages": [
            {
                "page_id": page_id,
                "page_number": page_number,
                "subtitle": infer_subtitle(ocr_text, fallback=final_title),
                "text": page_text,
                "tables": page_tables,
            }
        ],
        "metadata": {
            "customer_name": extract_customer_name(ocr_text),
            "checked_items": extract_checked_items(ocr_text),
            "source_image": str(image_path),
            "source_txt": str(txt_path) if status == "success" else None,
            "ocr_status": status,
            "error_message": error_message,
            "key_terms": extract_key_terms(ocr_text),
        },
    }


def save_result(
    image_path: Path,
    output_dir: Path,
    ocr_text: str,
    model_name: str,
    user_id: str,
    document_sector: str,
    document_date: str,
    document_type: str,
    company: str,
    document_title: str,
    status: str = "success",
    error_message: str = None
):
    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = image_path.stem

    txt_path = output_dir / f"{base_name}.txt"
    json_path = output_dir / f"{base_name}.json"

    if status == "success":
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(ocr_text)

    rag_document = build_rag_document(
        image_path=image_path,
        txt_path=txt_path,
        ocr_text=ocr_text,
        user_id=user_id,
        model_name=model_name,
        status=status,
        document_sector=document_sector,
        document_date=document_date,
        document_type=document_type,
        company=company,
        document_title=document_title,
        error_message=error_message,
    )

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rag_document, f, ensure_ascii=False, indent=2)

    return txt_path, json_path


# ==================================================
# 메인 함수
# ==================================================
def main():
    parser = argparse.ArgumentParser(
        description="Gemini API를 이용한 금융 문서 이미지 OCR"
    )

    parser.add_argument(
        "--image-dir",
        default="data/raw",
        help="OCR할 이미지 폴더"
    )

    parser.add_argument(
        "--output-dir",
        default="data/processed",
        help="OCR 결과 저장 폴더"
    )

    parser.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help="사용할 Gemini 모델명"
    )

    parser.add_argument(
        "--sleep",
        type=float,
        default=5.0,
        help="요청 사이 대기 시간(초)"
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
        help="기존 txt/json 결과가 있어도 새 JSON 스키마로 다시 저장"
    )

    parser.add_argument(
        "--user-id",
        default=None,
        help="JSON에 저장할 user_id. 지정하지 않으면 이미지 파일명을 사용"
    )

    parser.add_argument(
        "--customer-id",
        default=None,
        help="기존 호환용 옵션입니다. --user-id와 같은 값으로 처리됩니다."
    )

    parser.add_argument(
        "--document-sector",
        default="bank",
        help="문서 업권/분야"
    )

    parser.add_argument(
        "--document-date",
        default=None,
        help="문서 날짜. 지정하지 않으면 오늘 날짜를 사용"
    )

    parser.add_argument(
        "--document-type",
        default=None,
        help="문서 유형. 예: 은행거래신청서, 투자설명서"
    )

    parser.add_argument(
        "--company",
        default="",
        help="회사명. 지정하지 않으면 OCR 텍스트에서 추정"
    )

    parser.add_argument(
        "--document-title",
        default=None,
        help="문서 제목. 지정하지 않으면 OCR 텍스트에서 추정"
    )

    args = parser.parse_args()

    image_dir = args.image_dir
    output_dir = Path(args.output_dir)
    model_name = args.model
    document_date = args.document_date or datetime.now().date().isoformat()
    client = get_gemini_client()

    print("==============================")
    print("Gemini OCR 시작")
    print("==============================")
    print(f"이미지 폴더: {image_dir}")
    print(f"저장 폴더: {output_dir}")
    print(f"사용 모델: {model_name}")
    print(f"요청 대기시간: {args.sleep}초")

    image_files = collect_image_files(image_dir)

    if args.limit is not None:
        image_files = image_files[:args.limit]

    print(f"처리 대상 이미지 수: {len(image_files)}")

    success_count = 0
    skip_count = 0
    fail_count = 0

    for idx, image_path in enumerate(image_files, start=1):
        base_name = image_path.stem
        txt_path = output_dir / f"{base_name}.txt"
        legacy_md_path = output_dir / f"{base_name}.md"
        user_id = args.user_id or args.customer_id or base_name

        if txt_path.exists() and not args.overwrite:
            print(f"[{idx}/{len(image_files)}] 건너뜀: {image_path}")
            skip_count += 1
            continue

        if txt_path.exists():
            print(f"[{idx}/{len(image_files)}] 기존 TXT 결과 변환: {txt_path}")
            try:
                ocr_text = txt_path.read_text(encoding="utf-8")
                save_result(
                    image_path=image_path,
                    output_dir=output_dir,
                    ocr_text=ocr_text,
                    model_name=model_name,
                    user_id=user_id,
                    document_sector=args.document_sector,
                    document_date=document_date,
                    document_type=args.document_type,
                    company=args.company,
                    document_title=args.document_title,
                    status="success"
                )
                success_count += 1
                continue
            except Exception as e:
                print(f"기존 TXT 결과 변환 실패: {txt_path}")
                print(e)

        if legacy_md_path.exists():
            print(f"[{idx}/{len(image_files)}] 기존 OCR 결과 변환: {legacy_md_path}")
            try:
                ocr_text = legacy_md_path.read_text(encoding="utf-8")
                save_result(
                    image_path=image_path,
                    output_dir=output_dir,
                    ocr_text=ocr_text,
                    model_name=model_name,
                    user_id=user_id,
                    document_sector=args.document_sector,
                    document_date=document_date,
                    document_type=args.document_type,
                    company=args.company,
                    document_title=args.document_title,
                    status="success"
                )
                success_count += 1
                continue
            except Exception as e:
                print(f"기존 OCR 결과 변환 실패: {legacy_md_path}")
                print(e)

        print(f"[{idx}/{len(image_files)}] OCR 시작: {image_path}")

        try:
            ocr_text = run_gemini_ocr(client, image_path, model_name)

            if not ocr_text or len(ocr_text.strip()) == 0:
                raise ValueError("OCR 결과가 비어 있습니다.")

            save_result(
                image_path=image_path,
                output_dir=output_dir,
                ocr_text=ocr_text,
                model_name=model_name,
                user_id=user_id,
                document_sector=args.document_sector,
                document_date=document_date,
                document_type=args.document_type,
                company=args.company,
                document_title=args.document_title,
                status="success"
            )

            print(f"OCR 성공: {image_path}")
            success_count += 1

        except Exception as e:
            error_message = str(e)

            print(f"OCR 실패: {image_path}")
            print(error_message)

            save_result(
                image_path=image_path,
                output_dir=output_dir,
                ocr_text="",
                model_name=model_name,
                user_id=user_id,
                document_sector=args.document_sector,
                document_date=document_date,
                document_type=args.document_type,
                company=args.company,
                document_title=args.document_title,
                status="fail",
                error_message=error_message
            )

            fail_count += 1

        time.sleep(args.sleep)

    print("\n==============================")
    print("Gemini OCR 완료")
    print("==============================")
    print(f"성공: {success_count}")
    print(f"건너뜀: {skip_count}")
    print(f"실패: {fail_count}")
    print(f"저장 위치: {output_dir}")


if __name__ == "__main__":
    main()