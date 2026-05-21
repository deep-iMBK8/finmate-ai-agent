from google import genai
import os
import shutil
import uuid

# 1. API 키 설정 
GOOGLE_API_KEY = "GOOGLE_API_KEY"
client = genai.Client(api_key=GOOGLE_API_KEY)

def print_pdf_text(pdf_filepath):
    # 파일 존재 여부 확인
    if not os.path.exists(pdf_filepath):
        print(f"파일을 찾을 수 없습니다: {pdf_filepath}")
        return

    print(f"'{pdf_filepath}' 처리 중...\n")

    # 한글 파일명 에러 방지를 위한 임시 영어 파일 생성
    temp_filename = f"temp_upload_{uuid.uuid4().hex}.pdf"
    shutil.copy(pdf_filepath, temp_filename)

    try:
        # 1. 구글 서버에 파일 업로드 (임시 영어 파일 사용)
        print("PDF 파일을 서버로 업로드 중입니다...")
        sample_file = client.files.upload(file=temp_filename)

        # 2. 프롬프트 작성
        prompt = """
        첨부된 PDF 문서에 있는 모든 텍스트를 추출해 줘.
        단, 절대 요약하거나 내용을 빼먹지 말고 원문 그대로 추출해.
        또한 마침표, 쉼표 등 모든 특수기호를 완전히 제거하고 오직 한글, 영문, 숫자, 줄바꿈, 띄어쓰기만 남겨서 아주 깔끔한 텍스트로만 반환해 줘.
        """

        # 3. 텍스트 생성 요청
        print("텍스트 추출 중... (잠시만 기다려주세요)\n")
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite',
            contents=[sample_file, prompt]
        )
        cleaned_text = response.text

        # 4. 추출된 텍스트를 터미널에 바로 출력
        print("=" * 50)
        print("[추출된 텍스트 결과]")
        print("=" * 50)
        print(cleaned_text)
        print("=" * 50)

        # 5. 구글 서버에 올린 PDF 삭제
        client.files.delete(name=sample_file.name)
        print("\n텍스트 추출 및 출력 완료!")

    except Exception as e:
        print(f"\n처리 중 에러 발생: {e}")
        
    finally:
        # 6. 작업 완료 후 로컬에 만든 임시 파일 삭제
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

# 실행 부분
if __name__ == "__main__":
    # 한글 파일명 그대로 사용 가능
    TARGET_PDF_FILE = "국민은행_KB Star 정기예금_약관 및 상품설명서.pdf" 
    
    print_pdf_text(TARGET_PDF_FILE)