"""SQLite helpers for EarnScope."""
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
DB_PATH = os.path.join(DATA_DIR, "earnscope.db")
SCHEMA_PATH = os.path.join(HERE, "schema.sql")


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn=None):
    close = False
    if conn is None:
        conn = connect()
        close = True
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    if close:
        conn.close()
