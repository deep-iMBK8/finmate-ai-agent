import streamlit as st
from services.document_service import parse_uploaded_document


def render_file_uploader(sector: str) -> None:
    uploaded_file = st.file_uploader(
        "파일을 업로드하세요",
        type=["pdf", "png", "jpg", "jpeg", "webp"],
    )

    if st.button("문서 업로드", use_container_width=True) and uploaded_file is not None:
        with st.spinner("문서 파싱 중입니다..."):
            try:
                parsed_data = parse_uploaded_document(sector=sector, uploaded_file=uploaded_file)
                st.success("파싱 및 JSON 저장 완료!")
                st.session_state.last_parsed_data = parsed_data
            except Exception as exc:
                st.error(f"서버 통신 실패: {exc}")

    if st.session_state.last_parsed_data is not None:
        st.markdown("### 최근 파싱 결과")
        st.json(st.session_state.last_parsed_data)
    else:
        st.info("아직 파싱된 문서가 없습니다.")
