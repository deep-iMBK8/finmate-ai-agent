import shutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, Form, File

from src.config.paths import RAW_PDF_DIR
from src.preprocessing.pdf_router import PDFRouter

app = FastAPI(title="금융 문서 파싱 API 서버")
pdf_router = PDFRouter()

@app.post("/api/parse")
async def parse_pdf_endpoint(
    sector: str = Form(...),          # "은행", "카드", "보험", "투자"
    file: UploadFile = File(...)      # 업로드된 파일
):
    # 임시 파일 저장 경로
    temp_dir = RAW_PDF_DIR / sector.lower()
    temp_dir.mkdir(parents=True, exist_ok=True)
    target_path = temp_dir / file.filename

    # 사용자가 업로드한 파일 스트림을 로컬 물리 파일로 저장
    with target_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # PDFRouter 동적 호출
        # metadata 딕셔너리를 함께 전달
        metadata = {"document_title": file.filename}
        result_json = pdf_router.process_pdf(sector=sector, pdf_path=target_path, metadata=metadata)
        
        return {"status": "success", "data": result_json}
        
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="127.0.0.1", port=8080, reload=True)