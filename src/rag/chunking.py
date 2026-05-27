from __future__ import annotations

import glob
import json
import os

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.utils.chunk_helpers import (
    clean_noise, 
    restore_hierarchy, 
    is_valid_table, 
    convert_table_to_markdown
)

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


def get_dynamic_chunk_settings(json_data: dict) -> tuple[int, int]:
    document_type = (json_data.get("document_type") or "").lower()
    document_title  = (json_data.get("document_title") or "").lower()
    pages_count = json_data.get("pages_count", 1)

    # 문서 유형·분량별 규칙
    if "보험" in document_title or "약관" in document_type or pages_count > 50:
        # 보험/약관: 문서 사이즈가 큼
        return 900, 200
    elif "설명서" in document_type or "가이드" in document_type or "투자" in document_title or 2 < pages_count <= 50:
        # 상품설명서·투자안내서 혼합형
        return 700, 150
    elif "명세서" in document_type or "내역" in document_type or "card" in document_title:
        # 표 위주 명세서
        return 500, 100
    elif pages_count <= 2 or "통장" in document_type or "bankbook" in document_title:
        # 1~2페이지 증명서·통장 사본
        return 400, 50

    # 기본값
    return 600, 100


def build_splitter(chunk_size: int, overlap: int) -> RecursiveCharacterTextSplitter:
    """동적으로 계산된 설정값으로 RecursiveCharacterTextSplitter를 생성합니다."""
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=KOREAN_FINANCIAL_SEPARATORS,
        length_function=len,          # 한국어 문자 수 기준 (토큰 아님)
        is_separator_regex=False,     # separators를 정규식이 아닌 리터럴로 처리
        keep_separator=False,         # 구분자 자체는 청크에 포함하지 않음
    )


def _make_chunk(content: str, base_meta: dict, chunk_type: str, page_num: int) -> dict:
    """문서 공통 메타데이터(base_meta)에 청크 고유 정보를 추가하여 반환합니다."""
    # base_meta가 다른 청크에 의해 변경되지 않도록 복사(copy)해서 사용합니다.
    chunk_meta = base_meta.copy()
    chunk_meta["chunk_type"] = chunk_type
    chunk_meta["page_number"] = page_num

    return {
        "chunk": content,
        "metadata": chunk_meta
    }


def _process_page(page: dict, base_meta: dict, splitter: RecursiveCharacterTextSplitter) -> list[dict]:
    """페이지 하나에서 텍스트 청크와 테이블 청크를 모두 추출합니다."""
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
            chunks.append(_make_chunk(tc, base_meta, "text", page_num))

    # 테이블 처리
    for tbl in page.get("tables", []):
        if not is_valid_table(tbl):
            continue
        md_table = convert_table_to_markdown(tbl)
        if md_table:
            chunks.append(_make_chunk(md_table, base_meta, "table", page_num))

    return chunks


# 값이 없거나 null 문자열일 때 빈 문자열("")로 안전하게 바꿔주는 함수
def _safe_str(value) -> str:
    """DB 에러를 방지하기 위해 null이나 None 값을 빈 문자열로 반환합니다."""
    if value is None or str(value).strip().lower() == "null":
        return ""
    return str(value)


def chunk_document(json_data: dict, custom_config: dict | None = None) -> list[dict]:
    chunks = []

    # 1. 절대 타협 불가 키 (고유 식별자가 없으면 DB 매핑/업데이트 불가능)
    doc_uuid = json_data.get("document_uuid")
    if not doc_uuid:
        print("  [경고] document_uuid가 누락된 문서입니다. 건너뜁니다.")
        return chunks

    pages = json_data.get("pages", [])
    if not pages:
        return chunks

    actual_pages_count = len(pages)

    # 2. 메타데이터 안전망 처리 (_safe_str 적용)
    # 누락되었거나 null인 값들은 모두 빈 문자열("")로 치환되어 저장됩니다.
    base_meta = {
        "document_uuid": doc_uuid,
        "sector": _safe_str(json_data.get("sector")),
        "document_date": _safe_str(json_data.get("document_date")),
        "document_type": _safe_str(json_data.get("document_type")),
        "company": _safe_str(json_data.get("company")),
        "document_title": _safe_str(json_data.get("document_title")),
        "created_at": _safe_str(json_data.get("created_at")),
        "file_type": _safe_str(json_data.get("file_type")),
        "processing_engine": _safe_str(json_data.get("processing_engine")),
        "pages_count": actual_pages_count
    }

    # base_meta가 json_data_corrected 역할을 완벽히 대체하므로 바로 전달
    chunk_size, overlap = get_dynamic_chunk_settings(base_meta, custom_config)
    print(f"  -> 적용된 설정: chunk_size={chunk_size}, overlap={overlap}")

    splitter = build_splitter(chunk_size, overlap)

    # 1페이지 단일 청크 처리 로직
    if actual_pages_count == 1:
        page = pages[0]
        page_num = page.get("page_number", 1)

        content_parts = []
        subtitle = clean_noise(page.get("subtitle", ""))
        raw_text = clean_noise(page.get("text", ""))
        hierarchical = restore_hierarchy(raw_text)

        if subtitle:
            content_parts.append(f"## {subtitle}")
        if hierarchical:
            content_parts.append(hierarchical)
            
        for tbl in page.get("tables", []):
            if is_valid_table(tbl):
                content_parts.append(convert_table_to_markdown(tbl))

        full_content = "\n\n".join(content_parts)

        # 내용이 적을 경우 통째로 하나의 문서로 반환
        if full_content and len(full_content) <= chunk_size * 1.5:
            chunks.append(_make_chunk(full_content, base_meta, "short_document", page_num))
        else:
            # 길면 일반 페이지 처리로 넘김
            chunks.extend(_process_page(page, base_meta, splitter))

    else:
        # 다중 페이지 문서 처리
        for page in pages:
            chunks.extend(_process_page(page, base_meta, splitter))

    # ==================================================
    # 1페이지든 다중 페이지든 마지막에 일괄적으로 ID를 부여합니다.
    # "chunk_id"가 가장 최상단에 위치하도록 재조립합니다.
    # ==================================================
    for index, chunk in enumerate(chunks, start=1):
        chunk_id_val = f"{base_meta['document_uuid']}_chunk{index}"
        
        # 새로운 딕셔너리를 만들어 "chunk_id"를 가장 먼저 넣습니다.
        reordered_chunk = {"chunk_id": chunk_id_val}
        # 그 뒤에 기존 내용("chunk", "metadata")을 합칩니다.
        reordered_chunk.update(chunk)
        
        # 완성된 딕셔너리로 원본 리스트의 요소를 교체합니다.
        chunks[index-1] = reordered_chunk

    return chunks


def batch_process_json_files(
    input_dir: str,
    output_dir: str,
    custom_config: dict | None = None,  
) -> None:
    if custom_config is None:
        custom_config = {}

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
            chunks = chunk_document(json_data, custom_config=custom_config)

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