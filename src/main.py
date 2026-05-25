import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from src.config.paths import RAW_IMAGE_DIR, RAW_PDF_DIR
from src.preprocessing.image_ocr_engine import GeminiOCREngine
from src.preprocessing.pdf_router import PDFRouter

# 환경 변수 로드 (Gemini 클라이언트용)
load_dotenv()

app = FastAPI(title="금융 문서 파싱 API 서버")

pdf_router = PDFRouter()

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
if not PROJECT_ID:
    print("GOOGLE_CLOUD_PROJECT 키 값 없음")

ocr_engine = GeminiOCREngine(project=PROJECT_ID, location=LOCATION)

SECTOR_MAP = {
    "은행": "bank",
    "카드": "card",
    "보험": "insurance",
    "투자": "stock"
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

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

    # 파일 확장자 검사 후 분기 처리
    filename = file.filename
    file_suffix = Path(filename).suffix.lower()

    try:
        # PDF 문서 파싱 파이프라인 -----------------------
        if file_suffix == ".pdf":
            # 로컬에 파일 저장
            temp_dir = RAW_PDF_DIR / eng_sector
            temp_dir.mkdir(parents=True, exist_ok=True)
            target_path = temp_dir / filename
            with target_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
                
            # PDFRouter 동적 호출
            # metadata 딕셔너리를 함께 전달
            metadata = {"document_title": filename, "sector": eng_sector}
            result_json = pdf_router.process_pdf(sector=eng_sector, pdf_path=target_path, metadata=metadata)
    
        # 이미지 파싱 파이프라인 -----------------------
        elif file_suffix in IMAGE_EXTENSIONS:
            # 로컬에 파일 저장
            temp_dir = RAW_IMAGE_DIR / eng_sector
            temp_dir.mkdir(parents=True, exist_ok=True)
            target_path = temp_dir / filename
            with target_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            # 피딩 메타데이터 조립
            feed_metadata = {
                "sector": clean_sector,
                "company": None,
                "document_title": Path(filename).stem
            }

            result_json = image_ocr_engine.process_image(image_path=target_path, metadata=feed_metadata)
        
        else:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 파일 형식입니다 ({file_suffix}).")
            
        return {"status": "success", "data": result_json}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    # 포트 포워딩
    import uvicorn
    uvicorn.run("src.main:app", host="127.0.0.1", port=8080, reload=True)