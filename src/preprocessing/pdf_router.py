from datetime import datetime
from pathlib import Path

from preprocessing.pdf_bank_parser import extract_bank_pdf
from src.preprocessing.pdf_card_parser import extract_card_pdf
from src.preprocessing.pdf_insurance_parser import extract_insurance_pdf
from src.preprocessing.pdf_stock_parser import extract_stock_pdf


class PDFRouter:
    def __init__(self):
        # 사용자의 입력 키값과 실제 함수 매핑
        self.router = {
            "bank": extract_bank_pdf,
            "card": extract_card_pdf,
            "insurance": extract_insurance_pdf,
            "stock": extract_stock_pdf
        }

    def process_pdf(self, sector: str, pdf_path: Path, metadata: dict = None) -> dict:
        # 사용자가 선택한 sector에 따라 적절한 파서를 매칭하여 실행
        sector = sector.strip()
        if sector not in self.router:
            raise ValueError(f"지원하지 않는 섹터입니다: {sector}.")

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")

        print(f"섹터 '{sector}' 감지 -> 파싱 {pdf_path.name}")
        
        # 매핑된 함수 동적 실행
        parse_function = self.router[sector]
        
        # 공통 기본 메타데이터 세팅
        if metadata is None:
            metadata = {}
            
        # 각 엔진별 파싱 수행
        document_data = parse_function(pdf_path, metadata)
        
        return document_data