import requests

import streamlit as st

st.title("금융 문서 통합 파싱 가이드 UI")

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
            st.error("백엔드 서버와 통신에 실패했습니다.")