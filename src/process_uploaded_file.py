import argparse
import importlib.util
import shutil
from datetime import datetime
from pathlib import Path

try:
    from scripts import db_store
except ModuleNotFoundError:
    import db_store


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
DOCUMENT_EXTENSIONS = {".pdf", ".html", ".htm", ".xml", ".txt"}
DEFAULT_UPLOAD_DIR = "data/uploads"
DEFAULT_OUTPUT_DIR = "data/processed/ocr_text"


def load_module(module_name: str, path: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save_uploaded_copy(file_path: Path, upload_dir: Path):
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / file_path.name

    if file_path.resolve() != destination.resolve():
        shutil.copy2(file_path, destination)

    return destination


def process_image(
    file_path: Path,
    output_dir: Path,
    user_id: str,
    document_sector: str,
    document_date: str,
    document_type: str,
    company: str,
    document_title: str,
    model: str,
    chunk_size: int,
    chunk_overlap: int,
):
    gemini_ocr = load_module("gemini_ocr", "scripts/gemini_ocr_images.py")
    client = gemini_ocr.get_gemini_client()
    ocr_text = gemini_ocr.run_gemini_ocr(client, file_path, model)

    if not ocr_text or not ocr_text.strip():
        raise ValueError("OCR 결과가 비어 있습니다.")

    return gemini_ocr.save_result(
        image_path=file_path,
        output_dir=output_dir,
        ocr_text=ocr_text,
        model_name=model,
        user_id=user_id or file_path.stem,
        document_sector=document_sector,
        document_date=document_date,
        document_type=document_type,
        company=company,
        document_title=document_title,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        status="success",
    )


def process_document(
    file_path: Path,
    output_dir: Path,
    user_id: str,
    document_sector: str,
    document_date: str,
    document_type: str,
    company: str,
    document_title: str,
    chunk_size: int,
    chunk_overlap: int,
):
    document_parser = load_module("document_parser", "scripts/document_parser.py")
    _, txt_path, json_path = document_parser.save_parsed_file(
        file_path=str(file_path),
        output_dir=str(output_dir),
        user_id=user_id or file_path.stem,
        document_sector=document_sector,
        document_date=document_date,
        document_type=document_type,
        company=company,
        document_title=document_title,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    return txt_path, json_path


def process_uploaded_file(args):
    source_path = Path(args.file_path)
    if not source_path.exists():
        raise FileNotFoundError(f"업로드 파일이 없습니다: {source_path}")

    uploaded_path = save_uploaded_copy(source_path, Path(args.upload_dir))
    suffix = uploaded_path.suffix.lower()
    output_dir = Path(args.output_dir)
    document_date = args.document_date or datetime.now().date().isoformat()

    if suffix in IMAGE_EXTENSIONS:
        print(f"[업로드 처리] 이미지 OCR: {uploaded_path}")
        txt_path, json_path = process_image(
            file_path=uploaded_path,
            output_dir=output_dir,
            user_id=args.user_id,
            document_sector=args.document_sector,
            document_date=document_date,
            document_type=args.document_type,
            company=args.company,
            document_title=args.document_title,
            model=args.model,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
    elif suffix in DOCUMENT_EXTENSIONS:
        print(f"[업로드 처리] 개인문서 파싱: {uploaded_path}")
        txt_path, json_path = process_document(
            file_path=uploaded_path,
            output_dir=output_dir,
            user_id=args.user_id,
            document_sector=args.document_sector,
            document_date=document_date,
            document_type=args.document_type,
            company=args.company,
            document_title=args.document_title,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
    else:
        raise ValueError(f"지원하지 않는 파일 형식입니다: {suffix}")

    print("\n==============================")
    print("업로드 파일 처리 완료")
    print("==============================")
    print(f"원본 보관: {uploaded_path}")
    print(f"TXT: {txt_path}")
    print(f"JSON: {json_path}")

    if args.store_mysql:
        if not db_store.mysql_enabled():
            print("[MySQL] 비활성화: MYSQL_HOST/MYSQL_DATABASE 환경변수를 설정하세요.")
        else:
            db_store.upsert_document_from_json(
                json_path=str(json_path),
                original_filename=source_path.name,
                stored_path=str(uploaded_path),
            )
            print("[MySQL] 문서 저장 완료")

    return txt_path, json_path


def parse_args():
    parser = argparse.ArgumentParser(
        description="업로드된 파일 확장자에 따라 이미지 OCR 또는 개인문서 파싱을 실행합니다."
    )
    parser.add_argument("file_path", help="업로드된 파일 경로")
    parser.add_argument("--upload-dir", default=DEFAULT_UPLOAD_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--document-sector", default="bank")
    parser.add_argument("--document-date", default=None)
    parser.add_argument("--document-type", default=None)
    parser.add_argument("--company", default="")
    parser.add_argument("--document-title", default=None)
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--chunk-overlap", type=int, default=120)
    parser.add_argument(
        "--store-mysql",
        action="store_true",
        help="처리 완료 후 MySQL documents/document_chunks 테이블에 저장합니다.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    process_uploaded_file(parse_args())
