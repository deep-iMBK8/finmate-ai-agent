import streamlit as st


def render_sidebar() -> str:
    st.subheader("문서 업로드")
    sector = st.selectbox("업권을 선택하세요", ["은행", "카드", "보험", "투자"])
    return sector
