import logging
import sqlite3
import time

import sqlglot
from sqlglot import exp

DB_PATH = "sales.db"
MAX_ROWS = 1000          # row limit
TIMEOUT_SECONDS = 5      # query timeout
ALLOWED_TABLES = {"customers", "orders"}  # restrict accessible tables

# Log to a file: generated SQL, outcome, duration, failures
logging.basicConfig(
    filename="queries.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def open_readonly_database(path):
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def uses_only_allowed_tables(sql):
    tree = sqlglot.parse_one(sql, read="sqlite")
    cte_names = {cte.alias for cte in tree.find_all(exp.CTE)}
    tables = {t.name for t in tree.find_all(exp.Table)} - cte_names
    return tables <= ALLOWED_TABLES


def run_query(sql, path=DB_PATH):
    if not uses_only_allowed_tables(sql):
        logging.warning("REJECTED (table not allowed): %s", sql)
        raise PermissionError("Query uses a table that is not allowed.")

    conn = open_readonly_database(path)

    # Timeout: SQLite calls this regularly; returning True stops the query
    deadline = time.monotonic() + TIMEOUT_SECONDS
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)

    start = time.monotonic()
    try:
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(MAX_ROWS + 1)
        if len(rows) > MAX_ROWS:
            raise RuntimeError(f"Query returned more than {MAX_ROWS} rows.")
        duration = time.monotonic() - start
        logging.info("OK %.3fs rows=%d: %s", duration, len(rows), sql)
        return rows
    except Exception as e:
        duration = time.monotonic() - start
        logging.error("FAILED %.3fs %s: %s", duration, e, sql)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    # Should work
    print(run_query("SELECT SUM(amount) FROM orders WHERE status = 'completed'"))

    # Should fail: the connection is read-only
    try:
        run_query("DELETE FROM orders")
    except Exception as e:
        print("Blocked as expected:", e)