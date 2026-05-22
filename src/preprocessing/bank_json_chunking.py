import glob
import json
import os
import re


def convert_table_to_markdown(table_data):
    """JSON 표 데이터를 마크다운 형식으로 변환"""
    rows = table_data.get("rows", [])
    if not rows:
        return ""
    md_lines = []
    for i, row in enumerate(rows):
        clean_row = [str(cell).replace("\n", " ").strip() for cell in row]
        md_line = "| " + " | ".join(clean_row) + " |"
        md_lines.append(md_line)
        if i == 0:
            separator = "|" + "|".join(["---"] * len(clean_row)) + "|"
            md_lines.append(separator)
    return "\n".join(md_lines)


def overlap_text_chunks(text, chunk_size=500, overlap=50):
    """긴 텍스트를 지정된 크기와 오버랩을 적용해 분할"""
    sentences = re.split(r"(?<=[.!?])\s+|\n", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    chunks = []
    current_chunk = ""
    for sentence in sentences:
        if len(current_chunk) + len(sentence) > chunk_size and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = (
                current_chunk[-overlap:] + " " + sentence if overlap > 0 else sentence
            )
        else:
            current_chunk += " " + sentence if current_chunk else sentence
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks


def process_document(json_data):
    chunks = []

    doc_uuid = json_data.get("document_uuid", "Unknown_UUID")
    company = json_data.get("company", "Unknown")
    doc_type = json_data.get("document_type", "Unknown")
    doc_date = json_data.get("document_date", "Unknown")
    pages_count = json_data.get("pages_count", 1)
    pages = json_data.get("pages", [])

    if not pages:
        return chunks

    if pages_count == 1:
        page = pages[0]
        page_num = page.get("page_number", 1)

        content_parts = []
        if page.get("subtitle"):
            content_parts.append(f"## {page['subtitle']}")
        if page.get("text"):
            content_parts.append(page["text"])
        for tbl in page.get("tables", []):
            content_parts.append(convert_table_to_markdown(tbl))

        full_content = "\n\n".join(content_parts)

        chunk_data = {
            "page_content": full_content,
            "metadata": {
                "document_uuid": doc_uuid,
                "document_type": doc_type,
                "company": company,
                "document_date": doc_date,
                "chunk_type": "short_document",
                "page_number": page_num,
            },
        }
        chunks.append(chunk_data)
        return chunks

    for page in pages:
        page_num = page.get("page_number", 1)

        subtitle = page.get("subtitle", "").strip()
        text = page.get("text", "").strip()
        full_page_text = ""

        if subtitle:
            full_page_text += f"## {subtitle}\n"
        if text:
            full_page_text += text

        if full_page_text:
            text_chunks = overlap_text_chunks(
                full_page_text, chunk_size=600, overlap=100
            )
            for tc in text_chunks:
                chunk_data = {
                    "page_content": tc,
                    "metadata": {
                        "document_uuid": doc_uuid,
                        "document_type": doc_type,
                        "company": company,
                        "document_date": doc_date,
                        "chunk_type": "text",
                        "page_number": page_num,
                    },
                }
                chunks.append(chunk_data)

        for tbl in page.get("tables", []):
            md_table = convert_table_to_markdown(tbl)
            if md_table:
                chunk_data = {
                    "page_content": md_table,
                    "metadata": {
                        "document_uuid": doc_uuid,
                        "document_type": doc_type,
                        "company": company,
                        "document_date": doc_date,
                        "chunk_type": "table",
                        "page_number": page_num,
                    },
                }
                chunks.append(chunk_data)

    return chunks


def batch_process_json_files(input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    search_pattern = os.path.join(input_dir, "*.json")
    json_files = glob.glob(search_pattern)

    if not json_files:
        print(f"'{input_dir}' 폴더에 처리할 JSON 파일이 없습니다.")
        return

    print(
        f"총 {len(json_files)}개의 JSON 파일을 찾았습니다. 청킹 작업을 시작합니다...\n"
    )
    print("-" * 50)

    for file_path in json_files:
        file_name = os.path.basename(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)

            chunks = process_document(json_data)

            if not chunks:
                print(
                    f"[경고] {file_name} 파일에서 추출할 데이터가 없습니다. 건너뜁니다."
                )
                continue

            base_name, _ = os.path.splitext(file_name)
            output_file_name = f"{base_name}_chunked.json"
            output_file_path = os.path.join(output_dir, output_file_name)

            with open(output_file_path, "w", encoding="utf-8") as f:
                json.dump(chunks, f, ensure_ascii=False, indent=4)

            print(
                f"[성공] {file_name} -> {output_file_name} ({len(chunks)}개 청크 분할 완료)"
            )

        except Exception as e:
            print(f"[에러] {file_name} 처리 중 문제 발생: {str(e)}")

    print("-" * 50)
    print(f"작업 완료! 결과물 확인 경로: {output_dir}")


if __name__ == "__main__":
    INPUT_JSON_DIR = "data/processed/json"
    OUTPUT_CHUNK_DIR = "src/chunking"
    batch_process_json_files(INPUT_JSON_DIR, OUTPUT_CHUNK_DIR)
