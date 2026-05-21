import json
import os
import re
import time
import uuid
from datetime import datetime

import dart_fss as dart
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# api key 로드
load_dotenv()

dart_api_key = os.getenv("DART_API_KEY")

dart.set_api_key(api_key=dart_api_key)

# 추출된 메타데이터 저장할 경로
DOWNLOAD_DIR = "/data/processed/json/dart"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# 찾을 기업
targets = set(
    ["미래에셋자산운용", "삼성자산운용", "KB자산운용", "신한자산운용", "한화자산운용"]
)
# 가져올 문서 종류
target_types = [
    "투자설명서",
    "증권신고서",
    "사업보고서",
    "반기보고서",
    "분기보고서",
    "감사보고서",
    "약관",
    "설명서",
    "정관",
]

# 기업 검색

corp_list = dart.get_corp_list()

# 찾고 싶은 대상 기업
target_corps = [corp for corp in corp_list if corp.corp_name in targets]

# 공시 가져오기

report_list = []

# 여러 기업 순회
for corp in target_corps:
    reports = corp.search_filings(bgn_de="20250501", end_de="20260430")

    # 공시 순회
    for report in reports:
        title = report.report_nm

        print(title)

        if any(doc_type in title for doc_type in target_types):
            rcept_no = report.rcept_no

            report_list.append(
                {
                    "corp_name": corp.corp_name,  # 기업명
                    "report_name": report.report_nm,  # 공시명
                    "rcept_no": rcept_no,  # 접수번호
                    "rcept_dt": report.rcept_dt,  # 공시일
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
                    "filing": report,  # 실제 객체
                }
            )

# # 확인
# for report in report_list:
#     print(report)


# document_type 추출 함수 -----
def extract_document_type(doc_title: str) -> str:
    document_type_patterns = {
        "투자설명서": r"투자설명서",
        "증권신고서": r"증권신고서",
        "사업보고서": r"사업보고서",
        "반기보고서": r"반기보고서",
        "분기보고서": r"분기보고서",
        "감사보고서": r"감사보고서",
        "약관": r"약관",
        "설명서": r"설명서",
        "정관": r"정관",
    }

    for document_type, pattern in document_type_patterns.items():
        if re.search(pattern, doc_title):
            return document_type

    return "기타"


# -----------------------------

for report in report_list:
    company = report["corp_name"]
    document_title = report["report_name"]
    rcept_no = report["rcept_no"]
    filing_obj = report["filing"]  # dart-fss의 Report 객체

    # document uuid 생성
    document_uuid = str(uuid.uuid4())

    # 문서 공시일 추출
    document_date = f"{rcept_no[:4]}-{rcept_no[4:6]}-{rcept_no[6:8]}"

    print(f"\n[문서 수집] {company} | {document_title}")

    # 문서 메타데이터 - 이 형태로 파일 저장할 거임
    document_data = {
        "document_uuid": document_uuid,
        "sector": "investment",
        "document_date": document_date,
        "document_type": extract_document_type(document_title),
        "company": company,
        "document_title": document_title,
        # "rcept_no": rcept_no,
        "created_at": datetime.now().isoformat(),
        "file_type": "html",
        "processing_engine": "beautifulsoup",
        "pages_count": 0,
        "pages": [],
    }

    try:
        pages = filing_obj.pages

        for page_idx, page in enumerate(pages, start=1):
            # 페이지 제목
            sub_title = page.title

            # HTML 원문
            html_content = page.html

            # BeautifulSoup 생성
            soup = BeautifulSoup(html_content, "lxml")

            # -----------------------------------
            # TABLE 추출
            # -----------------------------------
            table_list = []

            for table_idx, table in enumerate(soup.find_all("table"), start=1):
                table_rows = []

                for row in table.find_all("tr"):
                    cells = [
                        cell.get_text(strip=True) for cell in row.find_all(["td", "th"])
                    ]

                    if cells:
                        table_rows.append(cells)

                table_list.append(
                    {
                        "table_id": f"{document_uuid}_p{page_idx}_tbl{table_idx}",
                        "table_index": table_idx,
                        "rows": table_rows,
                    }
                )

            # -----------------------------------
            # IMAGE 추출
            # -----------------------------------
            image_list = []

            for img_idx, img in enumerate(soup.find_all("img"), start=1):
                image_info = {
                    "image_id": f"{document_uuid}_p{page_idx}_img{img_idx}",
                    "src": img.get("src", ""),
                    "alt": img.get("alt", ""),
                }

                image_list.append(image_info)

            # -----------------------------------
            # TEXT 추출
            # -----------------------------------

            # text 추출 전 table 제거
            soup_for_text = BeautifulSoup(html_content, "html.parser")

            for table in soup_for_text.find_all("table"):
                table.decompose()

            text = soup_for_text.get_text(separator="\n", strip=True)

            # 공백 정리
            text = re.sub(r"\s+", " ", text)

            # -----------------------------------
            # 페이지 단위로 저장
            # -----------------------------------
            page_data = {
                "page_id": f"{document_uuid}_p{page_idx}",
                "page_number": page_idx,
                "subtitle": sub_title if sub_title else "",
                "text": text if text else "",
                "tables": table_list if table_list else [],
                "images": image_list if image_list else [],
            }

            document_data["pages"].append(page_data)

    except Exception as e:
        print(f"[-] {rcept_no} 문서 추출 중 오류 발생: {e}")
        continue

    if not document_data["pages"]:
        print("[-] 페이지 데이터가 비어있습니다.")
        continue

    # 전체 페이지 수 추가
    document_data["pages_count"] = len(document_data["pages"])

    # # 내용 확인용 출력
    # first_page_preview = (
    #     document_data["pages"][0]["text"][:200]
    # )
    # print(first_page_preview + " ... (후략)")

    # 파일명 안전 처리
    safe_report_name = re.sub(r'[\\/*?:"<>|]', "", document_title)

    # JSON 확장자로 저장
    file_path = os.path.join(DOWNLOAD_DIR, f"{company}_{document_uuid}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(document_data, f, ensure_ascii=False, indent=2)

    print(f"[+] JSON 저장 완료: {file_path}")

    # 요청 속도 조절
    time.sleep(0.1)
