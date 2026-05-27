import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mysql.db_store import execute_schema


if __name__ == "__main__":
    execute_schema()
    print("MySQL schema initialized.")
