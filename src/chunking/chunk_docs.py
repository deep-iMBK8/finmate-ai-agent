import json
import os
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config.paths import CHUNKS_DIR, JSON_DIR

CHUNKS_DIR.mkdir(parents=True, exist_ok=True)  # chunks 폴더 새로 생성

splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)

for filename in os.listdir(JSON_DIR):
    if not filename.endswith(".json"):
        continue

    # json 파일 경로
    path = os.path.join(JSON_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    # 청크 데이터 저장용
    chunk_data = []

    # TEXT 청킹 - 페이지 정보 살려서
    text_chunk_idx = 0

    for page in doc["pages"]:
        page_number = page["page_number"]
        page_text = page["text"]
        text_chunks = splitter.split_text(page_text)

        for chunk in text_chunks:
            chunk_data.append(
                {
                    "chunk_id": f"{doc['document_uuid']}_text_{text_chunk_idx}",
                    "document_uuid": doc["document_uuid"],
                    # 검색을 위한 메타데이터
                    "sector": doc["sector"],
                    "document_date": doc["document_date"],
                    "document_type": doc["document_type"],
                    "company": doc["company"],
                    "chunk_type": "text",
                    "page_number": page_number,
                    "chunk_index": text_chunk_idx,
                    "chunk_text": chunk,
                }
            )

            text_chunk_idx += 1

    # TABLE 청킹
    table_chunk_idx = 0
    for page in doc["pages"]:
        page_number = page["page_number"]

        for table in page["tables"]:
            table_id = table["table_id"]

            # rows -> 문자열로 평탄화
            table_text = "\n".join([" | ".join(row) for row in table["rows"]])

            # table도 splitter 적용
            split_table_chunks = splitter.split_text(table_text)

            for chunk in split_table_chunks:
                chunk_data.append(
                    {
                        "chunk_id": f"{doc['document_uuid']}_table_{table_chunk_idx}",
                        # 검색을 위한 메타데이터
                        "document_uuid": doc["document_uuid"],
                        "sector": doc["sector"],
                        "document_date": doc["document_date"],
                        "document_type": doc["document_type"],
                        "company": doc["company"],
                        "chunk_type": "table",
                        "page_number": page_number,
                        "table_id": table_id,
                        "chunk_index": table_chunk_idx,
                        "chunk_text": chunk,
                    }
                )

                table_chunk_idx += 1

    # 청크 저장
    save_path = os.path.join(CHUNKS_DIR, f"chunk_{doc['document_uuid']}.json")

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(chunk_data, f, ensure_ascii=False, indent=2)

    print(f"{filename} 청킹 완료")
