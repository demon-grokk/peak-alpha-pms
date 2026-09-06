"""
SQLite database manager for Peak Alpha PMS.
"""
import sqlite3
from typing import Generator
from contextlib import contextmanager
from config.settings import DB_PATH
from src.utils.logger import logger

class DatabaseManager:
    def __init__(self, db_path=DB_PATH):
        self.db_path = str(db_path)

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise e
        finally:
            conn.close()

db_manager = DatabaseManager()
