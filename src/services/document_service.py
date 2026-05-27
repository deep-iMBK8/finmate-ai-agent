import requests

BACKEND_URL = "http://127.0.0.1:8080"


def parse_uploaded_document(sector: str, uploaded_file) -> dict:
    files = {
        "file": (
            uploaded_file.name,
            uploaded_file.getvalue(),
            uploaded_file.type,
        )
    }
    data = {"sector": sector}

    try:
        # FastAPI 엔드포인트 호출 (상수 URL 및 timeout 300초 유지)
        response = requests.post(
            f"{BACKEND_URL}/api/parse",
            files=files,
            data=data,
            timeout=300,
        )

        if response.status_code == 200:
            res_data = response.json()
            if res_data.get("status") == "success":
                # 성공 시 데이터 딕셔너리 반환
                return res_data.get("data", {})
            else:
                raise Exception(res_data.get("message", "백엔드 처리 실패"))
        else:
            raise Exception(
                f"서버 응답 에러 ({response.status_code}): {response.reason}"
            )

    except requests.exceptions.ConnectionError:
        raise Exception(
            "FastAPI 서버가 꺼져있거나 연결할 수 없습니다. 포트(8080)를 확인하세요."
        )