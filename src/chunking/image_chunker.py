import argparse
import json
import uuid
from pathlib import Path

JSON_DIR   = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "image" / "json"
CHUNK_DIR  = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "image" / "chunks"
CHUNK_SIZE = 500   # 텍스트 청크 최대 글자 수


# ── 텍스트 → 청크 리스트 ───────────────────────────────────────────────────────

def split_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """단락 기준으로 분할, 단락이 chunk_size 초과하면 추가 분할"""
    if not text.strip():
        return []

    # 500자 이하면 그냥 1청크
    if len(text) <= chunk_size:
        return [text.strip()]

    # 단락(\n\n) 기준으로 먼저 나누기
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            # 단락 자체가 chunk_size 초과하면 강제 분할
            while len(para) > chunk_size:
                chunks.append(para[:chunk_size])
                para = para[chunk_size:]
            current = para

    if current:
        chunks.append(current)

    return chunks


# ── 표 rows → 텍스트 변환 ─────────────────────────────────────────────────────

def table_to_text(rows: list[list[str]]) -> str:
    return "\n".join(" | ".join(str(cell) for cell in row) for row in rows if row).strip()


# ── JSON 1개 → 청크 리스트 ────────────────────────────────────────────────────

def chunk_document(json_path: Path) -> list[dict]:
    data = json.loads(json_path.read_text(encoding="utf-8"))

    meta = {
        "document_uuid":  data.get("document_uuid", ""),
        "sector":         data.get("sector", ""),
        "company":        data.get("company", ""),
        "document_type":  data.get("document_type", ""),
        "document_title": data.get("document_title", ""),
        "document_date":  data.get("document_date"),
        "file_type":      data.get("file_type", ""),
        "source_file":    str(json_path.name),
    }

    chunks = []
    chunk_index = 0

    for page in data.get("pages", []):
        page_number = page.get("page_number", 1)

        # ── 텍스트 청크 ──────────────────────────────────────────────────────
        text = (page.get("text") or "").strip()
        for text_chunk in split_text(text):
            chunks.append({
                "chunk_id":    str(uuid.uuid4()),
                "chunk_index": chunk_index,
                "chunk_type":  "text",
                "text":        text_chunk,
                "metadata": {
                    **meta,
                    "page_number": page_number,
                },
            })
            chunk_index += 1

        # ── 표 청크 ───────────────────────────────────────────────────────────
        for table in page.get("tables", []):
            rows = table.get("rows", [])
            table_text = table_to_text(rows)
            if table_text:
                chunks.append({
                    "chunk_id":    str(uuid.uuid4()),
                    "chunk_index": chunk_index,
                    "chunk_type":  "table",
                    "text":        table_text,
                    "metadata": {
                        **meta,
                        "page_number": page_number,
                        "table_id":    table.get("table_id", ""),
                    },
                })
                chunk_index += 1

    return chunks


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="이미지 OCR JSON → 청크 분할")
    parser.add_argument("--json-dir",   default=str(JSON_DIR))
    parser.add_argument("--chunk-dir",  default=str(CHUNK_DIR))
    parser.add_argument("--limit",      type=int, default=None)
    args = parser.parse_args()

    json_dir  = Path(args.json_dir)
    chunk_dir = Path(args.chunk_dir)

    json_files = sorted(json_dir.rglob("*.json"))
    if args.limit:
        json_files = json_files[: args.limit]

    print(f"JSON 파일 수: {len(json_files)}")

    total_chunks = 0
    fail_count   = 0

    for json_path in json_files:
        try:
            chunks = chunk_document(json_path)

            # 원본 폴더 구조 유지해서 저장
            relative = json_path.relative_to(json_dir)
            out_path = chunk_dir / relative
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(chunks, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            total_chunks += len(chunks)
            print(f"  OK {relative}  ({len(chunks)} chunks)")

        except Exception as e:
            fail_count += 1
            print(f"  FAIL {json_path.name}: {e}")

    print(f"\n완료: 총 청크 {total_chunks}개 | 실패 {fail_count}건")
    print(f"저장 위치: {chunk_dir}")


if __name__ == "__main__":
    main()