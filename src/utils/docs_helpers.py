import re


# 텍스트 내의 불필요한 특수 공백(\xa0) 및 연속된 공백을 정제
# - keep_newlines=True: 개행 구조 유지 (카드 파서 등 중심)
# - keep_newlines=False: 개행 없애고 한 줄로 축소 (증권 파서 등 중심)
def clean_text(text: str, keep_newlines: bool = True) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    
    if keep_newlines:
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
    else:
        text = re.sub(r"\s+", " ", text)

    return text.strip()

# OS에서 파일명으로 못 쓰는 문자(\ / : * ? " < > |) 처리
# - replacement: 금지 문자 대체 값 (예: "" 또는 "_")
# - replace_space: 공백 문자 대체 값 (예: "" 또는 "_")
def safe_filename(text: str, replacement: str = "", replace_space: str = "") -> str:
    if not text:
        return "unknown"
    text = str(text)
    # 1. OS 금지 특수문자 제어
    text = re.sub(r'[\\/*?:"<>|]', replacement, text)
    # 2. 공백 문자 제어
    if replace_space:
        text = re.sub(r"\s+", replace_space, text)
    
    return text.strip(replace_space) if replace_space else text.strip()

# 날짜를 YYYY-MM-DD 형태로 정규화
def normalize_date(date_str: str) -> str:
    if not date_str:
        return ""
    return date_str.replace(".", "-").replace("/", "-").strip()
