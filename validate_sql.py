import sqlglot
from sqlglot import exp


def validate_sql(sql):
    """Return True only if `sql` is exactly one read-only SELECT statement."""
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except sqlglot.errors.ParseError:
        return False

    # Must be exactly one statement (blocks "SELECT ...; DROP TABLE ...")
    if len(statements) != 1 or statements[0] is None:
        return False

    statement = statements[0]

    # Must be a SELECT (a WITH ... SELECT also counts as a SELECT here)
    if not isinstance(statement, exp.Select):
        return False

    # Extra safety: no data-modifying statements hidden anywhere inside it
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter)
    if any(statement.find(kind) for kind in forbidden):
        return False

    return True


if __name__ == "__main__":
    tests = [
        ("SELECT SUM(amount) FROM orders WHERE status = 'completed'", True),
        ("WITH t AS (SELECT * FROM orders) SELECT COUNT(*) FROM t", True),
        ("DELETE FROM orders", False),
        ("DROP TABLE customers", False),
        ("SELECT 1; DROP TABLE orders", False),
        ("this is not sql", False),
    ]

    for sql, expected in tests:
        result = validate_sql(sql)
        status = "OK  " if result == expected else "FAIL"
        print(f"{status} {result!s:5}  {sql}")