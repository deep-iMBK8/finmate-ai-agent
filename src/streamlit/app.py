import os
import sys
from pathlib import Path

import streamlit as st

# 경로 가이드 설정 (로컬 모듈보다 상단에 유지할 것)
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
st.caption("당신 곁의 금융 메이트 -")

# 세션 상태 초기화
init_session_state()

# 화면 레이아웃 분할
left_col, right_col = st.columns([1, 2], gap="large")

with left_col:
    sector = render_sidebar()
    render_file_uploader(sector)

with right_col:
    render_chat_box(sector)