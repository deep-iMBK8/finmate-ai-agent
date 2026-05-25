import requests
import streamlit as st

from chatbot_view import render_chatbot

BACKEND_URL = "http://127.0.0.1:8080"

st.set_page_config(page_title="금융 문서 챗봇", layout="wide")
st.title("금융 문서 통합 파싱 & AI 챗봇")

if "last_parsed_data" not in st.session_state:
    st.session_state.last_parsed_data = None

left_col, right_col = st.columns([1, 1.6], gap="large")

with left_col:
    st.subheader("문서 업로드")

    sector = st.selectbox("업권을 선택하세요", ["은행", "카드", "보험", "투자"])
    uploaded_file = st.file_uploader(
        "파일을 업로드하세요",
        type=["pdf", "png", "jpg", "jpeg", "webp"]
    )

    if st.button("문서 업로드", use_container_width=True) and uploaded_file is not None:
        with st.spinner("텍스트 추출 중입니다..."):
            files = {
                "file": (
                    uploaded_file.name,
                    uploaded_file.getvalue(),
                    uploaded_file.type
                )
            }
            data = {"sector": sector}

            try:
                response = requests.post(
                    f"{BACKEND_URL}/api/parse",
                    files=files,
                    data=data,
                    timeout=300
                )

                if response.status_code == 200:
                    res_data = response.json()
                    if res_data.get("status") == "success":
                        st.success("파싱 및 JSON 저장 완료!")
                        st.session_state.last_parsed_data = res_data["data"]
                    else:
                        st.error(f"실패: {res_data.get('message')}")
                else:
                    st.error(f"백엔드 오류: {response.status_code}")
            except Exception as e:
                st.error(f"서버 통신 실패: {e}")

    if st.session_state.last_parsed_data is not None:
        st.markdown("### 최근 파싱 결과")
        st.json(st.session_state.last_parsed_data)
    else:
        st.info("아직 파싱된 문서가 없습니다.")

with right_col:
    render_chatbot(sector)