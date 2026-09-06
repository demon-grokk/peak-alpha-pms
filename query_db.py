"""
Simple helper script to execute SQL queries or inspect tables on data/pms.db.
Usage:
    ./venv/bin/python query_db.py "SELECT * FROM stocks LIMIT 5;"
    ./venv/bin/python query_db.py tables
    ./venv/bin/python query_db.py desc stocks
"""
import sys
import pandas as pd
from src.db.database import db_manager

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  ./venv/bin/python query_db.py tables")
        print("  ./venv/bin/python query_db.py desc <table_name>")
        print("  ./venv/bin/python query_db.py \"<SQL_QUERY>\"")
        sys.exit(1)
        
    arg = sys.argv[1].strip()
    
    with db_manager.get_connection() as conn:
        if arg.lower() in ["tables", ".tables", "show tables"]:
            query = "SELECT name AS table_name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        elif arg.lower().startswith("desc ") or arg.lower().startswith("describe "):
            table_name = arg.split()[1].rstrip(";")
            query = f"PRAGMA table_info({table_name});"
        elif len(sys.argv) >= 3 and arg.lower() in ["desc", "describe"]:
            table_name = sys.argv[2].rstrip(";")
            query = f"PRAGMA table_info({table_name});"
        else:
            query = arg
            
        df = pd.read_sql_query(query, conn)
        print(f"\nExecuting: {query}")
        print(f"Results ({len(df)} rows):\n")
        print(df.to_string(index=False))

if __name__ == "__main__":
    main()
