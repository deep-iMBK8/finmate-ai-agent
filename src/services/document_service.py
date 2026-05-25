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

    response = requests.post(
        f"{BACKEND_URL}/api/parse",
        files=files,
        data=data,
        timeout=300,
    )

    if response.status_code != 200:
        raise RuntimeError(f"백엔드 오류: {response.status_code}")

    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(payload.get("message") or "문서 파싱에 실패했습니다.")

    return payload.get("data", {})
