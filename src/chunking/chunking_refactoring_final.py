import glob
import json
import os
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.utils.chunk_helpers import (
    clean_noise, 
    restore_hierarchy, 
    is_valid_table, 
    convert_table_to_markdown
)
from src.config.paths import CHUNKS_DIR, PROCESSED_JSON_DIR

# RecursiveCharacterTextSplitter는 앞 구분자부터 시도하고,
# 청크가 chunk_size를 초과하면 다음 구분자로 내려가며 재귀 분할
# 나열 순서대로 우선순위 부여: 문단 > 줄바꿈 > 일본식 마침표 > 영문 종결 > 단어 > 글자
KOREAN_FINANCIAL_SEPARATORS = [
    "\n\n",     
    "\n",        
    "。",        
    ". ",        
    "! ",
    "? ",
    " ",        
    "",          
]


def get_dynamic_chunk_settings(json_data: dict, custom_config: dict | None = None) -> tuple[int, int]:
    """문서 메타데이터를 분석하여 최적의 (chunk_size, overlap)을 반환합니다."""

    # 1. 수동 강제 설정 우선 적용
    if custom_config:
        company_key = json_data.get("company", "")
        if company_key in custom_config:
            cfg = custom_config[company_key]
            return cfg.get("chunk_size", 600), cfg.get("overlap", 100)

    doc_type = (json_data.get("document_type") or "").lower()
    company  = (json_data.get("company")        or "").lower()
    # pages_count는 process_document에서 실제 len(pages)로 보정된 값이 전달됨
    pages_count = json_data.get("pages_count", 1)

    # 2. 문서 유형·분량별 규칙
    if "보험" in company or "약관" in doc_type or pages_count > 50:
        # 초거대 보험 약관: 조항 간 문맥 보존을 위해 크게
        return 900, 200
    elif "설명서" in doc_type or "가이드" in doc_type or "투자" in doc_type or 2 < pages_count <= 50:
        # 상품설명서·투자안내서 혼합형
        return 700, 150
    elif "명세서" in doc_type or "내역" in doc_type or "card" in company:
        # 표 위주 명세서
        return 500, 100
    elif pages_count <= 2 or "통장" in doc_type or "bankbook" in company:
        # 1~2페이지 증명서·통장 사본
        return 400, 50

    # 기본값
    return 600, 100


def build_splitter(chunk_size: int, overlap: int) -> RecursiveCharacterTextSplitter:
    """
    [핵심 변경] 동적으로 계산된 설정값으로 RecursiveCharacterTextSplitter를 생성합니다.
    문서마다 호출되어 각 문서에 최적화된 splitter 인스턴스를 반환합니다.
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=KOREAN_FINANCIAL_SEPARATORS,
        length_function=len,          # 한국어 문자 수 기준 (토큰 아님)
        is_separator_regex=False,     # separators를 정규식이 아닌 리터럴로 처리
        keep_separator=False,         # 구분자 자체는 청크에 포함하지 않음
    )


def _make_chunk(content: str, doc_uuid: str, doc_type: str, company: str,
                doc_date: str, chunk_type: str, page_num: int) -> dict:
    """반복되는 청크 딕셔너리 생성을 단일 함수로 관리합니다."""
    return {
        "page_content": content,
        "metadata": {
            "document_uuid": doc_uuid,
            "document_type": doc_type,
            "company": company,
            "document_date": doc_date,
            "chunk_type": chunk_type,
            "page_number": page_num,
        },
    }


def _process_page(page: dict, doc_meta: tuple, splitter: RecursiveCharacterTextSplitter) -> list[dict]:
    """
    페이지 하나에서 텍스트 청크와 테이블 청크를 모두 추출합니다.
    1페이지·다중 페이지 분기 없이 동일한 로직을 공유합니다.
    """
    doc_uuid, doc_type, company, doc_date = doc_meta
    page_num = page.get("page_number", 1)
    chunks = []

    # 텍스트 처리
    subtitle        = clean_noise(page.get("subtitle", ""))
    raw_text        = clean_noise(page.get("text", ""))
    hierarchical    = restore_hierarchy(raw_text)

    full_page_text = ""
    if subtitle:
        full_page_text += f"## {subtitle}\n"
    if hierarchical:
        full_page_text += hierarchical

    if full_page_text:
        # split_text()로 RecursiveCharacterTextSplitter 호출
        for tc in splitter.split_text(full_page_text):
            chunks.append(_make_chunk(tc, doc_uuid, doc_type, company, doc_date, "text", page_num))

    # 테이블 처리
    for tbl in page.get("tables", []):
        if not is_valid_table(tbl):
            continue
        md_table = convert_table_to_markdown(tbl)
        if md_table:
            chunks.append(_make_chunk(md_table, doc_uuid, doc_type, company, doc_date, "table", page_num))

    return chunks

def process_document(json_data: dict, custom_config: dict | None = None) -> list[dict]:
    chunks = []

    doc_uuid    = json_data.get("document_uuid", "Unknown_UUID")
    company     = json_data.get("company",        "Unknown")
    doc_type    = json_data.get("document_type",  "Unknown")
    doc_date    = json_data.get("document_date",  "Unknown")
    pages       = json_data.get("pages",          [])

    if not pages:
        return chunks

    doc_meta = (doc_uuid, doc_type, company, doc_date)

    # pages_count를 메타데이터가 아닌 실제 pages 리스트 길이로 확정
    actual_pages_count = len(pages)
    json_data_corrected = {**json_data, "pages_count": actual_pages_count}

    chunk_size, overlap = get_dynamic_chunk_settings(json_data_corrected, custom_config)
    print(f"  -> 적용된 설정: chunk_size={chunk_size}, overlap={overlap}")

    # 문서마다 최적화된 splitter 인스턴스 생성
    splitter = build_splitter(chunk_size, overlap)

    # 1페이지 단일 청크 처리
    if actual_pages_count == 1:
        page    = pages[0]
        page_num = page.get("page_number", 1)

        content_parts = []
        subtitle    = clean_noise(page.get("subtitle", ""))
        raw_text    = clean_noise(page.get("text", ""))
        hierarchical = restore_hierarchy(raw_text)

        if subtitle:
            content_parts.append(f"## {subtitle}")
        if hierarchical:
            content_parts.append(hierarchical)
        for tbl in page.get("tables", []):
            if is_valid_table(tbl):
                content_parts.append(convert_table_to_markdown(tbl))

        full_content = "\n\n".join(content_parts)

        # 빈 문서 방어 + chunk_size 이하면 단일 청크로 저장
        if full_content and len(full_content) <= chunk_size * 1.5:
            chunks.append(
                _make_chunk(full_content, doc_uuid, doc_type, company, doc_date, "short_document", page_num)
            )
            return chunks

        # 내용이 길면 _process_page로 동일하게 분할 처리
        chunks.extend(_process_page(page, doc_meta, splitter))
        return chunks

    # 다중 페이지 문서 처리
    for page in pages:
        chunks.extend(_process_page(page, doc_meta, splitter))

    return chunks

def batch_process_json_files(
    input_dir: str,
    output_dir: str,
    custom_config: dict | None = None,  
) -> None:
    if custom_config is None:
        custom_config = CUSTOM_CHUNK_CONFIG = {}

    os.makedirs(output_dir, exist_ok=True)

    json_files = glob.glob(os.path.join(input_dir, "*.json"))
    if not json_files:
        print(f"'{input_dir}' 폴더에 처리할 JSON 파일이 없습니다.")
        return

    print(f"총 {len(json_files)}개의 JSON 파일을 찾았습니다. 청킹 작업을 시작합니다...\n")
    print("-" * 50)

    for file_path in json_files:
        file_name = os.path.basename(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)

            print(f"[처리중] {file_name}")
            chunks = process_document(json_data, custom_config=custom_config)

            if not chunks:
                print(f"  [경고] {file_name}에서 추출할 데이터가 없습니다. 건너뜁니다.")
                continue

            base_name        = os.path.splitext(file_name)[0]
            output_file_path = os.path.join(output_dir, f"{base_name}_chunked.json")

            with open(output_file_path, "w", encoding="utf-8") as f:
                json.dump(chunks, f, ensure_ascii=False, indent=4)

            print(f"  [성공] -> {base_name}_chunked.json ({len(chunks)}개 청크)\n")

        except Exception as e:
            print(f"  [에러] {file_name} 처리 중 문제 발생: {e}\n")

    print("-" * 50)
    print(f"작업 완료! 결과물 확인 경로: {output_dir}")


if __name__ == "__main__":
   
    current_dir = os.path.dirname(os.path.abspath(__file__)) 
    project_root = os.path.dirname(os.path.dirname(current_dir)) 
    
    # 루트 아래의 data 폴더로 경로 설정
    INPUT_JSON_DIR = os.path.join(project_root, "data", "processed", "json")
    OUTPUT_CHUNKS_DIR = os.path.join(project_root, "data", "processed", "chunking")
    
    print(f"입력 데이터 폴더: {INPUT_JSON_DIR}")
    
    batch_process_json_files(INPUT_JSON_DIR, OUTPUT_CHUNKS_DIR)