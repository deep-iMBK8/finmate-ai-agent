import argparse
import json
import mimetypes
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent  # src/preprocessing → src → 프로젝트 루트
ENV_PATH = BASE_DIR / ".env"
genai = None

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
}

SECTOR_MAP = {
    "은행": "bank",
    "보험": "insurance",
    "증권": "investment",
    "카드": "card",
}

MANIFEST_FILENAME = ".processed_manifest.txt"


# ── 환경변수 로드 ──────────────────────────────────────────────────────────────

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


# ── Gemini 설정 ────────────────────────────────────────────────────────────────

def configure_gemini(api_key: str) -> None:
    global genai
    try:
        import google.generativeai as google_genai
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "google-generativeai가 설치되어 있지 않습니다.\n"
            "  pip install google-generativeai"
        ) from exc
    genai = google_genai
    genai.configure(api_key=api_key)


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def clean_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").strip()
    if text.endswith("```"):
        text = text.removesuffix("```").strip()
    return text


def wait_for_uploaded_file(uploaded_file):
    while uploaded_file.state.name == "PROCESSING":
        print(".", end="", flush=True)
        time.sleep(2)
        uploaded_file = genai.get_file(uploaded_file.name)
    if uploaded_file.state.name != "ACTIVE":
        raise RuntimeError(f"Gemini file upload failed: {uploaded_file.state.name}")
    return uploaded_file


def upload_image(file_path: Path):
    mime_type = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
    uploaded_file = genai.upload_file(path=str(file_path), mime_type=mime_type)
    return wait_for_uploaded_file(uploaded_file)


# ── 폴더명 파싱: "은행 - 은행거래신청서" → ("bank", "은행거래신청서") ──────────

def parse_folder_name(folder_name: str) -> tuple[str, str]:
    if " - " in folder_name:
        sector_kr, doc_type = folder_name.split(" - ", 1)
        sector_kr = sector_kr.strip()
        doc_type = doc_type.strip()
    else:
        sector_kr = folder_name.strip()
        doc_type = folder_name.strip()
    sector_en = SECTOR_MAP.get(sector_kr, sector_kr)
    return sector_en, doc_type


# ── 처리 완료 목록 관리 (--skip-existing 용) ──────────────────────────────────

def load_manifest(processed_root: Path) -> set[str]:
    manifest_path = processed_root / MANIFEST_FILENAME
    if not manifest_path.exists():
        return set()
    return set(manifest_path.read_text(encoding="utf-8").splitlines())


def append_manifest(processed_root: Path, relative_key: str) -> None:
    manifest_path = processed_root / MANIFEST_FILENAME
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(relative_key + "\n")


# ── OCR ───────────────────────────────────────────────────────────────────────

def extract_text_with_gemini(image_path: Path, model_name: str, retries: int = 3) -> str:
    model = genai.GenerativeModel(model_name=model_name)
    prompt = """
    이 이미지는 한국어 금융 문서입니다. OCR만 수행하세요.

    규칙:
    1. 맨 위에 [PAGE 1]을 적고 시작하세요.
    2. 보이는 인쇄 텍스트와 손글씨를 가능한 원문 그대로 추출하세요.
    3. 표/칸 구조는 Markdown 표 또는 줄바꿈으로 알아볼 수 있게 정리하세요.
    4. 없는 내용은 추측하지 마세요.
    5. 읽기 어려운 글자는 [불명확]으로 표시하세요.
    6. JSON이 아니라 순수 텍스트만 출력하세요.
    """
    for attempt in range(retries):
        uploaded_file = upload_image(image_path)
        try:
            response = model.generate_content([uploaded_file, prompt])
            return (response.text or "").strip()
        except Exception as e:
            genai.delete_file(uploaded_file.name)
            if attempt < retries - 1:
                wait = (attempt + 1) * 10
                print(f"    재시도 {attempt + 1}/{retries} ({wait}초 대기): {e}")
                time.sleep(wait)
            else:
                raise
        else:
            genai.delete_file(uploaded_file.name)


# ── rows 정규화 ────────────────────────────────────────────────────────────────

def normalize_rows(rows) -> list[list[str]]:
    if not isinstance(rows, list):
        return []
    normalized = []
    for row in rows:
        if isinstance(row, list):
            normalized.append(["" if cell is None else str(cell) for cell in row])
        else:
            normalized.append(["" if row is None else str(row)])
    return normalized


# ── JSON 정규화 (example_final 필드 기준) ─────────────────────────────────────

def normalize_processed_json(
    ai_data: dict,
    document_uuid: str,
    created_at: str,
    image_path: Path,
    ocr_text: str,
    sector: str,
    document_type: str,
    file_type: str,
) -> dict:
    pages = ai_data.get("pages")
    if not isinstance(pages, list) or not pages:
        pages = [{
            "page_number": 1,
            "subtitle": image_path.stem,
            "text": ocr_text,
            "tables": [],
            "images": [],
        }]

    normalized_pages = []
    for fallback_idx, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            page = {"text": str(page)}

        page_number = page.get("page_number") or fallback_idx
        try:
            page_number = int(page_number)
        except (TypeError, ValueError):
            page_number = fallback_idx

        page_id = f"{document_uuid}_p{page_number}"

        tables = page.get("tables") or []
        if not isinstance(tables, list):
            tables = []

        normalized_tables = []
        for tbl_fallback, table in enumerate(tables, start=1):
            if not isinstance(table, dict):
                table = {"rows": table}
            table_index = table.get("table_index") or tbl_fallback
            try:
                table_index = int(table_index)
            except (TypeError, ValueError):
                table_index = tbl_fallback
            normalized_tables.append({
                "table_id": f"{page_id}_tbl{table_index}",
                "table_index": table_index,
                "rows": normalize_rows(table.get("rows")),
            })

        images = page.get("images") or []
        if not isinstance(images, list):
            images = [str(images)]

        normalized_pages.append({
            "page_id": page_id,
            "page_number": page_number,
            "subtitle": page.get("subtitle"),
            "text": page.get("text") or "",
            "tables": normalized_tables,
            "images": images,
        })

    return {
        "document_uuid":     document_uuid,
        "sector":            sector,
        "document_date":     ai_data.get("document_date"),       # 문서 자체 날짜
        "document_type":     document_type,
        "company":           ai_data.get("company"),
        "document_title":    ai_data.get("document_title") or image_path.stem,
        "processing_engine": "gemini_ocr",
        "file_type":         file_type,
        "created_at":        created_at,                         # JSON 파일 생성 시각
        "pages_count":       len(normalized_pages),
        "pages":             normalized_pages,
    }


# ── Gemini로 JSON 구조화 ───────────────────────────────────────────────────────

def build_chunking_json(
    image_path: Path,
    raw_root: Path,
    ocr_text: str,
    model_name: str,
    sector: str,
    document_type: str,
) -> dict:
    document_uuid = str(uuid.uuid4())
    created_at = datetime.now().isoformat()
    relative_path = image_path.relative_to(raw_root)
    file_type = image_path.suffix.lstrip(".").lower()

    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config={"response_mime_type": "application/json"},
    )

    prompt = f"""
    당신은 금융 문서 OCR 결과를 청킹/RAG용 JSON으로 정리하는 데이터 추출기입니다.
    아래 OCR 텍스트를 보고 지정된 스키마의 JSON만 반환하세요.

    [중요]
    - document_uuid, page_id, table_id는 코드에서 생성하므로 출력하지 마세요.
    - JSON 외 설명이나 마크다운 코드블록은 쓰지 마세요.
    - 찾을 수 없는 값은 문자열 "null"이 아니라 JSON null로 넣으세요.
    - 이미지는 1장짜리 문서이므로 page_number는 1입니다.
    - 표는 tables.rows에 2차원 배열로 정리하세요.
    - 표가 없으면 tables는 빈 배열 []로 두세요.
    - images는 보통 빈 배열 []로 두세요.
    - document_date는 문서 자체에 기재된 날짜(작성일/발행일/신청일 등)입니다.
      YYYY-MM-DD 형식으로 넣고, 날짜를 찾을 수 없으면 null로 넣으세요.

    [출력 스키마]
    {{
      "company": "문서에 나온 회사명(은행/보험사/증권사 등) 또는 null",
      "document_title": "문서 제목 또는 원본 파일명 기반 제목",
      "document_date": "문서 자체 날짜 YYYY-MM-DD 또는 null",
      "pages": [
        {{
          "page_number": 1,
          "subtitle": "페이지 소제목 또는 null",
          "text": "표를 제외한 페이지 주요 본문 텍스트",
          "tables": [
            {{
              "table_index": 1,
              "rows": [
                ["첫 번째 행 첫 번째 열", "첫 번째 행 두 번째 열"],
                ["두 번째 행 첫 번째 열", "두 번째 행 두 번째 열"]
              ]
            }}
          ],
          "images": []
        }}
      ]
    }}

    [문서 힌트]
    - 원본 파일명: {image_path.name}
    - 원본 상대 경로: {relative_path}
    - 섹터: {sector}
    - 문서 유형: {document_type}

    [OCR 텍스트]
    {ocr_text}
    """

    response = model.generate_content(prompt)
    json_text = clean_json_text(response.text or "")
    ai_data = json.loads(json_text)

    return normalize_processed_json(
        ai_data=ai_data,
        document_uuid=document_uuid,
        created_at=created_at,
        image_path=image_path,
        ocr_text=ocr_text,
        sector=sector,
        document_type=document_type,
        file_type=file_type,
    )


# ── 파일 순회 ──────────────────────────────────────────────────────────────────

def iter_image_files(raw_root: Path):
    for file_path in sorted(raw_root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.name.startswith("."):
            continue
        if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        yield file_path


def make_safe_name(name: str) -> str:
    """파일명에 쓸 수 없는 문자 제거"""
    for ch in r'/\:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip() or "unknown"


# ── 이미지 1개 처리 ────────────────────────────────────────────────────────────

def process_one_image(
    image_path: Path,
    raw_root: Path,
    processed_root: Path,
    model_name: str,
    skip_existing: bool,
    manifest: set[str],
) -> tuple[Path, Path]:
    relative_key = str(image_path.relative_to(raw_root))

    if skip_existing and relative_key in manifest:
        print(f"SKIP {relative_key}")
        return None, None

    print(f"PROCESS {relative_key}")

    # 1단계: OCR
    ocr_text = extract_text_with_gemini(image_path, model_name)

    # 2단계: JSON 구조화 (sector/document_type은 폴더명에서 파싱)
    folder_name = image_path.parent.name
    sector, document_type = parse_folder_name(folder_name)

    json_data = build_chunking_json(
        image_path=image_path,
        raw_root=raw_root,
        ocr_text=ocr_text,
        model_name=model_name,
        sector=sector,
        document_type=document_type,
    )

    # 파일명: 회사명_uuid (인식 실패 시 uuid만)
    company = json_data.get("company")
    document_uuid = json_data["document_uuid"]
    if company:
        base = f"{make_safe_name(company)}_{document_uuid}"
    else:
        base = document_uuid

    relative_parent = image_path.relative_to(raw_root).parent
    text_path = processed_root / "txt" / relative_parent / f"{base}.txt"
    json_path = processed_root / "json" / relative_parent / f"{base}.json"

    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(ocr_text, encoding="utf-8")

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(json_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return text_path, json_path


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="이미지 → Gemini OCR → processed_3 (example_final 필드 기준)"
    )
    parser.add_argument("--raw-dir",       default="./data/raw/image", help="raw 이미지 폴더 경로")
    parser.add_argument("--processed-dir", default="./data/processed/image", help="결과 저장 폴더 경로")
    parser.add_argument("--model",         default="models/gemini-3.1-flash-lite")
    parser.add_argument("--limit",         type=int, default=None)
    parser.add_argument("--skip-existing", action="store_true",
                        help="이미 처리된 파일 건너뜀 (.processed_manifest.txt 기준)")
    parser.add_argument("--aggregate",     action="store_true",
                        help="processed_text.txt + processed_json.json 통합 파일 생성")
    parser.add_argument("--aggregate-only", action="store_true",
                        help="통합 파일만 남기고 개별 파일 삭제")
    parser.add_argument("--zip",           action="store_true", help="processed_3.zip 생성")
    args = parser.parse_args()

    if args.aggregate_only:
        args.aggregate = True

    load_dotenv(ENV_PATH)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(".env에 GEMINI_API_KEY=내_API_키 형태로 넣어주세요.")

    configure_gemini(api_key)

    raw_root       = Path(args.raw_dir).expanduser().resolve()
    processed_root = Path(args.processed_dir).expanduser().resolve()

    if not raw_root.exists():
        raise FileNotFoundError(f"raw directory not found: {raw_root}")

    image_files = list(iter_image_files(raw_root))
    if args.limit is not None:
        image_files = image_files[: args.limit]

    processed_root.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(processed_root)

    print(f"raw      : {raw_root}")
    print(f"processed: {processed_root}")
    print(f"images   : {len(image_files)}")

    success_count, fail_count = 0, 0
    aggregate_text_parts = []
    aggregate_json_items = []

    for image_path in image_files:
        try:
            text_path, json_path = process_one_image(
                image_path=image_path,
                raw_root=raw_root,
                processed_root=processed_root,
                model_name=args.model,
                skip_existing=args.skip_existing,
                manifest=manifest,
            )

            if text_path is None:          # SKIP된 경우
                continue

            relative_key = str(image_path.relative_to(raw_root))
            append_manifest(processed_root, relative_key)
            manifest.add(relative_key)

            if args.aggregate:
                aggregate_text_parts.append(
                    f"\n\n===== {relative_key} =====\n"
                    f"{text_path.read_text(encoding='utf-8')}"
                )
                aggregate_json_items.append(
                    json.loads(json_path.read_text(encoding="utf-8"))
                )
            success_count += 1

        except Exception as exc:
            fail_count += 1
            print(f"FAIL {image_path.relative_to(raw_root)}: {exc}")

    print(f"\ndone. success={success_count}, fail={fail_count}")

    if args.aggregate:
        agg_txt  = processed_root / "processed_text.txt"
        agg_json = processed_root / "processed_json.json"
        agg_txt.write_text("\n".join(aggregate_text_parts).strip() + "\n", encoding="utf-8")
        agg_json.write_text(json.dumps(aggregate_json_items, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"aggregate text : {agg_txt}")
        print(f"aggregate json : {agg_json}")

        if args.aggregate_only:
            shutil.rmtree(processed_root / "txt", ignore_errors=True)
            shutil.rmtree(processed_root / "json", ignore_errors=True)

    if args.zip:
        zip_path = shutil.make_archive(str(processed_root), "zip", processed_root)
        print(f"zip: {zip_path}")


if __name__ == "__main__":
    main()