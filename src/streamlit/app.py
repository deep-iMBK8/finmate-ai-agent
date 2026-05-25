from pathlib import Path
import sys

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

st.set_page_config(page_title="금융 문서 챗봇", layout="wide")
st.title("금융 문서 통합 파싱 & AI 챗봇")

init_session_state()

left_col, right_col = st.columns([1, 1.6], gap="large")

with left_col:
    sector = render_sidebar()
    render_file_uploader(sector)

with right_col:
    render_chat_box(sector)
