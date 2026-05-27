import re


def clean_noise(text: str) -> str:
    """OCR 과정에서 섞여 들어온 제어 문자 및 반복 노이즈를 제거합니다."""
    if not text:
        return ""
    cleaned = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]', '', text)
    cleaned = re.sub(r'(?:[0-9]\s){10,}', '', cleaned)
    return cleaned.strip()
    pass


def restore_hierarchy(text: str) -> str:
    """사라진 줄바꿈을 복원하여 조항·목차 등 문단의 위계 구조를 살려냅니다."""
    if not text:
        return ""
    text = re.sub(r'\s+(제\s*\d+\s*조\s*\()', r'\n\n\1', text)
    text = re.sub(r'\s+([①-⑳])', r'\n\1', text)
    text = re.sub(r'\s+(\d+\.)\s+', r'\n\1 ', text)
    text = re.sub(r'\s+([가-하]\.)\s+', r'\n\1 ', text)
    text = re.sub(r'\s+([▶※\[])', r'\n\n\1', text)
    return text.strip()
    pass

def is_valid_table(table_data: dict) -> bool:
    """빈 값이나 노이즈만 들어간 가짜 테이블을 걸러냅니다."""
    rows = table_data.get("rows", [])
    if not rows:
        return False
    char_count = sum(len(str(cell).strip()) for row in rows for cell in row)
    return char_count >= 10
    pass

def convert_table_to_markdown(table_data: dict) -> str:
    """진짜 표면 마크다운으로, 단순 박스(1열)면 일반 텍스트로 복원합니다."""
    rows = table_data.get("rows", [])
    if not rows:
        return ""

    max_columns = max(len(row) for row in rows)

    # 1열짜리는 박스형 텍스트로 판단 → 일반 텍스트로 병합
    if max_columns == 1:
        text_lines = [str(row[0]).strip() for row in rows if row and row[0].strip()]
        merged = " ".join(text_lines)
        return re.sub(r'\s+([①-⑳]|\d+\.)', r'\n\1', merged)

    md_lines = []
    for i, row in enumerate(rows):
        clean_row = [str(cell).replace("\n", "<br>").strip() for cell in row]
        md_lines.append("| " + " | ".join(clean_row) + " |")
        if i == 0:
            md_lines.append("|" + "|".join(["---"] * len(clean_row)) + "|")

    return "\n".join(md_lines)
    pass