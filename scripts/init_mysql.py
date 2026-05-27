import sys
from pathlib import Path

from src.config.paths import BASE_DIR

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.database import db_store

if __name__ == "__main__":
    print("DB 초기화를 시작합니다...")
    db_store.execute_schema()
    print("DB schema 초기화 완료")
