import os
import sys
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "muhive")))
load_dotenv()

from src.db.connection import db_conn

def get_detailed_counts():
    try:
        with db_conn.get_cursor() as cur:
            cur.execute("""
                SELECT COALESCE(data->>'category', 'NULL_OR_UNSPECIFIED'), COUNT(*) 
                FROM scraped_data 
                GROUP BY COALESCE(data->>'category', 'NULL_OR_UNSPECIFIED');
            """)
            print("Row counts in scraped_data by category:")
            for row in cur.fetchall():
                print(f"Category '{row[0]}': {row[1]} rows")
                
            cur.execute("""
                SELECT COALESCE(category, 'NULL_OR_UNSPECIFIED'), COUNT(*) 
                FROM events 
                GROUP BY COALESCE(category, 'NULL_OR_UNSPECIFIED');
            """)
            print("\nRow counts in events by category:")
            for row in cur.fetchall():
                print(f"Category '{row[0]}': {row[1]} rows")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    get_detailed_counts()
