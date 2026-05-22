import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path


# =========================================================
# 1. 기존 도구 함수들 (그대로 유지)
# =========================================================
def keep_overlap_lines(lines, target_overlap=200):
    kept = []
    total = 0
    for line in reversed(lines):
        kept.append(line)
        total += len(line) + 1
        if total >= target_overlap:
            break
    kept.reverse()
    return kept


def split_long_text_safe(text, max_length=1200, overlap=200):
    lines = text.split("\n")
    chunks = []
    current_chunk = []
    current_length = 0

    in_table = False
    table_lines = []

    for line in lines:
        line_len = len(line) + 1

        if "[표 시작]" in line:
            in_table = True

        if in_table:
            table_lines.append(line)
            if "[표 끝]" in line:
                in_table = False
                table_text = "\n".join(table_lines)
                table_len = len(table_text)

                if current_length + table_len > max_length and current_chunk:
                    chunks.append("\n".join(current_chunk))
                    current_chunk = keep_overlap_lines(
                        current_chunk, target_overlap=overlap
                    )
                    current_length = sum(len(l) + 1 for l in current_chunk)

                current_chunk.append(table_text)
                current_length += table_len
                table_lines = []
            continue

        if current_length + line_len > max_length:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = keep_overlap_lines(
                    current_chunk, target_overlap=overlap
                )
                current_length = sum(len(l) + 1 for l in current_chunk)

        current_chunk.append(line)
        current_length += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def convert_tables_to_markdown(tables):
    if not tables:
        return ""
    md_tables_text = ""
    for table in tables:
        rows = table.get("rows", [])
        if not rows:
            continue
        md_table = "\n[표 시작]\n"
        header = [str(cell).strip() if cell else "" for cell in rows[0]]
        md_table += "| " + " | ".join(header) + " |\n"
        md_table += "| " + " | ".join(["---"] * len(header)) + " |\n"
        for row in rows[1:]:
            cleaned_row = [str(cell).strip() if cell else "" for cell in row]
            md_table += "| " + " | ".join(cleaned_row) + " |\n"
        md_table += "[표 끝]\n"
        md_tables_text += md_table
    return md_tables_text


def merge_clause_lines(global_lines):
    merged_lines = []
    i = 0
    while i < len(global_lines):
        current_line, page_num = global_lines[i]
        if re.match(r"^제\s*\d+(?:\s*의\s*\d+)?\s*조$", current_line):
            if i + 1 < len(global_lines):
                next_line, next_page = global_lines[i + 1]
                if re.match(r"^[\(\[【].*?[\)\]】]$", next_line):
                    current_line = current_line + next_line
                    i += 1
        merged_lines.append((current_line, page_num))
        i += 1
    return merged_lines


def execute_structural_chunking_refined(parsed_json):
    doc_uuid = parsed_json.get("document_uuid")
    pages = parsed_json.get("pages", [])
    final_db_records = []
    chunk_index = 0
    created_at = datetime.now().isoformat()

    current_chunk_lines = []
    current_chunk_line_pages = []

    clause_pattern = r"^제\s*\d+(?:\s*의\s*\d+)?\s*조(?:\s*[\(\[【].*?[\)\]】])?"

    def flush_current_chunk():
        nonlocal chunk_index, current_chunk_lines, current_chunk_line_pages
        if not current_chunk_lines:
            return
        chunk_text_content = "\n".join(current_chunk_lines).strip()
        if not chunk_text_content:
            return

        sub_chunks = split_long_text_safe(
            chunk_text_content, max_length=1200, overlap=200
        )
        for sub_text in sub_chunks:
            chunk_id = str(uuid.uuid4())
            resolved_page = (
                current_chunk_line_pages[0] if current_chunk_line_pages else None
            )

            chunk = {
                "chunk_id": chunk_id,
                "document_uuid": doc_uuid,
                "chunk_index": chunk_index,
                "chunk_text": sub_text,
                "embedding_id": f"emb_{chunk_id}",
                "created_at": created_at,
                "page_number": resolved_page,
            }
            final_db_records.append(chunk)
            chunk_index += 1

        current_chunk_lines = []
        current_chunk_line_pages = []

    all_document_lines = []
    for page in pages:
        page_num = page.get("page_number")
        page_text = page.get("text", "")
        page_tables = page.get("tables", [])

        raw_lines = page_text.split("\n")
        if page_tables:
            md_table_string = convert_tables_to_markdown(page_tables)
            if md_table_string:
                raw_lines.extend(md_table_string.split("\n"))

        for line in raw_lines:
            cleaned_line = line.strip()
            if cleaned_line:
                all_document_lines.append((cleaned_line, page_num))

    processed_lines = merge_clause_lines(all_document_lines)

    for line_text, page_num in processed_lines:
        if re.match(clause_pattern, line_text):
            flush_current_chunk()
            current_chunk_lines = [line_text]
            current_chunk_line_pages = [page_num]
        else:
            current_chunk_lines.append(line_text)
            current_chunk_line_pages.append(page_num)

    flush_current_chunk()
    return final_db_records


# =========================================================
# 2. Jupyter 실행부 (폴더 대량 처리 및 저장)
# =========================================================

# 1. 현재 주피터 노트북 파일이 있는 위치(./)를 기준으로 잡습니다.
# .py 파일과 .ipynb 파일 모두에서 에러 없이 실행되는 안전한 방식입니다.
BASE_DIR = Path(".").resolve()

# 2. 직관적으로 하위 경로를 연결합니다. (마치 문자열을 더하듯 '/' 기호를 씁니다)
RAW_TEXT_DIR = BASE_DIR / "data" / "processed" / "json"
CHUNKING_DIR = BASE_DIR / "data" / "chunks" / "chunking"

# 3. 폴더가 없으면 자동으로 만들어주는 안전장치
RAW_TEXT_DIR.mkdir(parents=True, exist_ok=True)

print(f"현재 프로젝트 기준 경로: {BASE_DIR}")
print(f"팀원 공용 저장 경로: {RAW_TEXT_DIR}")

# 3) chunking 저장 폴더가 없으면 자동으로 생성
CHUNKING_DIR.mkdir(parents=True, exist_ok=True)

print(f"입력 폴더 위치: {RAW_TEXT_DIR}")
print(f"출력 폴더 위치: {CHUNKING_DIR}\n")

# 4) raw_text 폴더 내의 모든 json 파일 처리
if os.path.exists(RAW_TEXT_DIR):
    file_list = [f for f in os.listdir(RAW_TEXT_DIR) if f.endswith(".json")]

    if not file_list:
        print("💡 raw_text 폴더 안에 처리할 .json 파일이 없습니다.")

    for file_name in file_list:
        input_file_path = os.path.join(RAW_TEXT_DIR, file_name)

        # 파일 읽기
        with open(input_file_path, "r", encoding="utf-8") as f:
            parsed_data = json.load(f)

        # 청킹 로직 수행
        result_chunks = execute_structural_chunking_refined(parsed_data)

        # 저장할 파일명 설정 (예: data.json -> chunked_data.json)
        output_file_name = f"chunked_{file_name}"
        output_file_path = os.path.join(CHUNKING_DIR, output_file_name)

        # 청킹 결과 json 파일로 저장
        with open(output_file_path, "w", encoding="utf-8") as f:
            json.dump(result_chunks, f, indent=4, ensure_ascii=False)

        print(
            f"✅ 처리 완료: {file_name} -> {output_file_name} ({len(result_chunks)}개 청크 저장됨)"
        )

    print("\n🎉 모든 파일의 청킹 및 저장이 완료되었습니다!")
else:
    print(f"❌ 에러: '{RAW_TEXT_DIR}' 폴더를 찾을 수 없습니다. 폴더명을 확인해 주세요.")
