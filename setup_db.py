import sqlite3

DB_PATH = "sales.db"

def setup_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.executescript("""
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            region TEXT NOT NULL
        );

        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            order_date TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );
    """)

    cursor.executemany(
        "INSERT INTO customers VALUES (?, ?, ?)",
        [
            (1, "Acme Corp", "North"),
            (2, "Beta Inc", "South"),
            (3, "Gamma Ltd", "North"),
            (4, "Delta Co", "West"),  # has no orders (tests LEFT JOIN handling)
        ],
    )

    cursor.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
        [
            (1, 1, 1000.00, "completed", "2026-01-10"),
            (2, 1, 500.00, "completed", "2026-02-15"),
            (3, 2, 750.00, "completed", "2026-02-20"),
            (4, 2, 200.00, "cancelled", "2026-03-05"),
            (5, 3, 1200.00, "completed", "2026-03-12"),
        ],
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    setup_database()
    print("Database created successfully.")
