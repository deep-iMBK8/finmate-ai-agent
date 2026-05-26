import os
import sys
from pathlib import Path

import requests

import streamlit as st

STREAMLIT_DIR = Path(__file__).resolve().parent
SRC_DIR = STREAMLIT_DIR.parent
for path in (STREAMLIT_DIR, SRC_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from components.chat_box import render_chat_box
from components.file_uploader import render_file_uploader
from components.sidebar import render_sidebar
from state.session import init_session_state

st.set_page_config(page_title="FinMate", layout="wide")
st.title("FinMate")

init_session_state()

left_col, right_col = st.columns([1, 1.6], gap="large")

with left_col:
    sector = render_sidebar()
    render_file_uploader(sector)

with right_col:
    render_chat_box(sector)

# 1. UI 컨트롤러 배치
sector = st.selectbox("업권을 선택하세요", ["은행", "카드", "보험", "투자"])
uploaded_file = st.file_uploader("파일을 업로드하세요", type=["pdf", "png", "jpg", "jpeg", "webp"])

if st.button("문서 업로드") and uploaded_file is not None:
    # TODO: 기존에 업로드된 파일인지 체크하는 로직 필요
    # 메타데이터 비교 "sector", "document_type", "company", "document_title"

    with st.spinner("텍스트 추출 중입니다..."):
        # 2. FastAPI 백엔드로 전송할 멀티파트 폼 데이터 구성
        files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
        data = {"sector": sector}
        
        try:
            # 3. FastAPI 엔드포인트 호출
            response = requests.post("http://127.0.0.1:8080/api/parse", files=files, data=data)
            
            if response.status_code == 200:
                res_data = response.json()
                if res_data.get("status") == "success":
                    st.success("파싱 및 JSON 저장 완료!")
                    st.json(res_data["data"])  # 결과 구조 시각화
                else:
                    st.error(f"실패: {res_data.get('message')}")
            else:
                st.error(f"API 요청 실패 {response.status_code} {response.reason}")
                with st.expander("상세 에러 본문 확인"):
                    st.text(response.text)
                    
        # 아예 서버가 꺼져있을 때
        except requests.exceptions.ConnectionError:
            st.error("서버가 연결되어 있는지 확인하세요.")