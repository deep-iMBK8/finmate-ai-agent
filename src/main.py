import shutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, Form, File

from src.config.paths import RAW_PDF_DIR
from src.preprocessing.pdf_router import PDFRouter

app = FastAPI(title="금융 문서 파싱 API 서버")
pdf_router = PDFRouter()

SECTOR_MAP = {
    "은행": "bank",
    "카드": "card",
    "보험": "insurance",
    "투자": "stock"
}

# pdf 인풋 파싱 API
@app.post("/api/parse")
async def parse_pdf_endpoint(
    sector: str = Form(...),          # "은행", "카드", "보험", "투자"
    file: UploadFile = File(...)      # 업로드된 파일
):
    # 섹터명 정제 후 영문으로 매핑
    clean_sector = sector.strip()
    # if clean_sector not in SECTOR_MAP:
    #     raise HTTPException(status_code=400, detail=f"지원하지 않는 섹터명입니다: {clean_sector}")
    eng_sector = SECTOR_MAP[clean_sector]

    # 사용자가 업로드한 파일 스트림을 로컬 물리 파일로 저장
    # 임시 파일 저장 경로
    # 예시: src/data/raw/pdf/bank/파일명.pdf
    temp_dir = RAW_PDF_DIR / eng_sector
    temp_dir.mkdir(parents=True, exist_ok=True)
    target_path = temp_dir / file.filename
    with target_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # PDFRouter 동적 호출
        # metadata 딕셔너리를 함께 전달
        metadata = {"document_title": file.filename}
        result_json = pdf_router.process_pdf(sector=eng_sector, pdf_path=target_path, metadata=metadata)
        
        return {"status": "success", "data": result_json}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    # 포트 포워딩
    import uvicorn
    uvicorn.run("src.main:app", host="127.0.0.1", port=8080, reload=True)