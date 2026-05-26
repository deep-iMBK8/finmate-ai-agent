import streamlit as st
from services.document_service import parse_uploaded_document


def render_file_uploader(sector: str) -> None:
    uploaded_file = st.file_uploader(
        f"[{sector}] 파일을 업로드하세요", 
        type=["pdf", "png", "jpg", "jpeg", "webp"],
        key="uploader_file_input"   # 고유 키값(key)을 부여하여 ID 충돌 안전 보장
    )

    if st.button("문서 업로드", use_container_width=True, key="uploader_submit_btn") and uploaded_file is not None:
        # TODO: 기존에 업로드된 파일인지 체크하는 로직 필요 (세션 스토리지 비교 등)

        with st.spinner(f"{sector} 문서 파싱 및 텍스트 추출 중..."):
            try:
                # 백엔드 서버 호출
                parsed_data = parse_uploaded_document(sector=sector, uploaded_file=uploaded_file)
                st.success("파싱 및 구조화 JSON 저장 완료!")
                # 최근 파싱 결과를 세션 상태에 저장
                st.session_state.last_parsed_data = parsed_data
            except Exception as exc:
                st.error(f"파싱 실패: {exc}")

    # 최근 파싱 결과 시각화 영역
    # init_session_state()에서 'last_parsed_data'가 사전에 무조건 초기화되어 있어야 함
    if st.session_state.get("last_parsed_data") is not None:
        st.markdown("### 최근 파싱 결과")
        st.json(st.session_state.last_parsed_data)
    else:
        st.info("아직 파싱된 문서 데이터가 없습니다.")