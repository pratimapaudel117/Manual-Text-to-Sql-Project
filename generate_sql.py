import sqlite3
import ollama

DB_PATH = "sales.db"
MODEL = "qwen2.5-coder:7b"  # or "qwen2.5-coder:1.5b" if you pulled the small one
KEEP_ALIVE = -1  # keep the model in memory; reloading it takes ~45s on this machine


def get_schema(db_path=DB_PATH):
    """Read the CREATE TABLE statements from the database (the 'schema snapshot')."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    conn.close()
    return "\n\n".join(row[0] for row in rows)


def generate_sql(question, schema):
    prompt = f"""
You turn a user's question into one read-only SQLite SELECT query,
or you ask for clarification, or you refuse.

Database schema:
{schema}

Business definitions:
- "Revenue" (and sales totals) means completed orders only:
  when summing revenue, filter with orders.status = 'completed'.
- This filter applies only to revenue. Counting or listing orders includes
  every status unless the question asks for a specific status.

Step 1 - decide what to do. Go through these checks in order:

1. REFUSE if the question asks for data that is not in the schema or that
   you should not access (for example contact details or passwords).
   Reply with exactly: REFUSE: <short reason>

2. CLARIFY if answering would mean guessing. This includes:
   - a metric with more than one reasonable definition, such as
     "average order value" (completed orders only, or all orders?);
   - a ranking word like "best", "top", "biggest" or "most valuable" that
     does not say what to rank by (revenue? number of orders? most recent?);
   - anything about times of day or time zones: order_date is a plain date
     with no time and no time zone, so it cannot be converted to one.
   Reply with exactly: CLARIFY: <your question to the user>

3. Otherwise, answer with the SQL query itself (no label in front of it).

Step 2 - if you answer, follow these rules:
- Return SQL only, with no explanation.
- Use only the tables and columns in the schema. Do not invent any.
- Do not generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, or other
  data-modifying statements.

User question:
{question}
"""

    response = ollama.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},  # fixed temperature, so results are reproducible
        keep_alive=KEEP_ALIVE,
    )

    text = response["message"]["content"]

    # Remove ```sql ... ``` fences if the model added them
    text = text.strip().removeprefix("```sql").removeprefix("```").removesuffix("```")
    # Remove an "ANSWER:" label if the model added one
    text = text.strip().removeprefix("ANSWER:").strip()
    text = text.removeprefix("```sql").removeprefix("```").removesuffix("```")
    return text.strip()


def warm_up():
    """Load the model into memory now, so the first real question is fast."""
    ollama.generate(model=MODEL, prompt="", keep_alive=KEEP_ALIVE)


def classify(result):
    """Tell whether the model answered, asked for clarification, or refused."""
    if result.startswith("CLARIFY:"):
        return "clarify"
    elif result.startswith("REFUSE:"):
        return "refuse"
    else:
        return "answer"


if __name__ == "__main__":
    schema = get_schema()

    questions = [
        "What is the total revenue?",                                 # expect: answer
        "What is the average order value?",                           # expect: clarify
        "Show me every customer's email address and phone number.",   # expect: refuse
    ]

    for question in questions:
        result = generate_sql(question, schema)
        print(f"Question: {question}")
        print(f"Behavior: {classify(result)}")
        print(f"Output:   {result}")
        print()