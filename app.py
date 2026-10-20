"""Simple web app: ask a question, see the generated SQL and its results.

Run:   python app.py
Open:  http://localhost:8000        (ask questions)
       http://localhost:8000/tests  (run the evaluation suite)
"""

import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sqlglot

from evaluate import run_case
from generate_sql import classify, generate_sql, get_schema, warm_up
from run_query import run_query
from test_cases import TEST_CASES
from validate_sql import validate_sql

PORT = 8000
SCHEMA = get_schema()


def column_names(sql, row_width):
    """Best-effort column headers from the SELECT list."""
    try:
        selects = sqlglot.parse_one(sql, read="sqlite").selects
        # Use the alias or column name, or the expression itself (e.g. "COUNT(*)")
        names = [
            s.alias or (s.name if isinstance(s, sqlglot.exp.Column) else s.sql(dialect="sqlite"))
            for s in selects
        ]
    except Exception:
        names = []
    if len(names) != row_width:
        names = [f"col{i + 1}" for i in range(row_width)]
    return names


def answer(question):
    """Run the full pipeline and return a dict for the page to render."""
    result = generate_sql(question, SCHEMA)
    behavior = classify(result)

    if behavior == "clarify":
        return {"kind": "clarify", "message": result.removeprefix("CLARIFY:").strip()}
    if behavior == "refuse":
        return {"kind": "refuse", "message": result.removeprefix("REFUSE:").strip()}

    sql = result
    if not validate_sql(sql):
        return {"kind": "error", "sql": sql, "message": "Generated SQL is not a single read-only SELECT."}

    try:
        rows = run_query(sql)
    except Exception as e:
        return {"kind": "error", "sql": sql, "message": str(e)}

    columns = column_names(sql, len(rows[0]) if rows else 0)
    return {"kind": "answer", "sql": sql, "columns": columns, "rows": [list(r) for r in rows]}


STYLE = """
  body { font-family: system-ui, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 16px; color: #222; }
  h1 { font-size: 1.5rem; }
  nav { margin-bottom: 16px; font-size: 0.95rem; } nav a { margin-right: 16px; color: #2563eb; }"""

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ask questions to your database</title>
<style>
  __STYLE__
  form { display: flex; gap: 8px; }
  input { flex: 1; padding: 10px; font-size: 1rem; border: 1px solid #bbb; border-radius: 6px; }
  button { padding: 10px 18px; font-size: 1rem; border: 0; border-radius: 6px; background: #2563eb; color: #fff; cursor: pointer; }
  button:disabled { background: #94a3b8; }
  pre { background: #f4f4f5; padding: 12px; border-radius: 6px; overflow-x: auto; }
  table { border-collapse: collapse; margin-top: 8px; }
  th, td { border: 1px solid #ddd; padding: 6px 12px; text-align: left; }
  th { background: #f4f4f5; }
  .note { padding: 12px; border-radius: 6px; margin-top: 16px; }
  .clarify { background: #fef9c3; } .refuse { background: #fee2e2; } .error { background: #fee2e2; }
  details { margin-top: 24px; color: #555; }
</style>
</head>
<body>
<nav><a href="/">Ask a question</a><a href="/tests">Run all tests</a></nav>
<h1>Ask questions to your database</h1>
<form id="f">
  <input id="q" placeholder="e.g. What is the total revenue?" autofocus required>
  <button id="b">Ask</button>
</form>
<div id="out"></div>
<details><summary>Database schema</summary><pre>__SCHEMA__</pre></details>
<script>
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('f').onsubmit = async e => {
  e.preventDefault();
  const q = document.getElementById('q').value, b = document.getElementById('b'), out = document.getElementById('out');
  b.disabled = true; b.textContent = 'Thinking...'; out.innerHTML = '';
  try {
    const r = await (await fetch('/ask', {method: 'POST', body: JSON.stringify({question: q})})).json();
    let h = r.sql ? '<h3>SQL</h3><pre>' + esc(r.sql) + '</pre>' : '';
    if (r.kind === 'answer') {
      h += '<h3>Results (' + r.rows.length + (r.rows.length === 1 ? ' row' : ' rows') + ')</h3><table><tr>' + r.columns.map(c => '<th>' + esc(c) + '</th>').join('') + '</tr>'
         + r.rows.map(row => '<tr>' + row.map(v => '<td>' + esc(v ?? 'NULL') + '</td>').join('') + '</tr>').join('') + '</table>';
    } else {
      const label = {clarify: 'Needs clarification', refuse: 'Refused', error: 'Error'}[r.kind];
      h += '<div class="note ' + r.kind + '"><b>' + label + ':</b> ' + esc(r.message) + '</div>';
    }
    out.innerHTML = h;
  } catch (err) {
    out.innerHTML = '<div class="note error"><b>Error:</b> ' + esc(err) + '</div>';
  }
  b.disabled = false; b.textContent = 'Ask';
};
</script>
</body>
</html>
"""

TESTS_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evaluation suite</title>
<style>
  __STYLE__
  body { max-width: 1000px; }
  button { padding: 10px 18px; font-size: 1rem; border: 0; border-radius: 6px; background: #2563eb; color: #fff; cursor: pointer; }
  button:disabled { background: #94a3b8; }
  table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
  th, td { border: 1px solid #ddd; padding: 6px 10px; text-align: left; vertical-align: top; }
  th { background: #f4f4f5; }
  td.sql { font-family: ui-monospace, monospace; font-size: 0.8rem; white-space: pre-wrap; max-width: 320px; }
  .pass { color: #15803d; font-weight: 600; } .fail { color: #b91c1c; font-weight: 600; } .wait { color: #64748b; }
  #summary { margin: 16px 0; padding: 14px; border-radius: 6px; font-size: 1.1rem; display: none; }
  #summary.ok { background: #dcfce7; display: block; } #summary.bad { background: #fee2e2; display: block; }
</style>
</head>
<body>
<nav><a href="/">Ask a question</a><a href="/tests">Run all tests</a></nav>
<h1>Evaluation suite: __COUNT__ test questions</h1>
<p>Each question runs through the full pipeline (generate, validate, run) and is checked against an answer worked out by hand.
Vague questions must get a follow-up question back, and requests for private data must be refused.</p>
<button id="run">Run all tests</button>
<div id="summary"></div>
<table>
  <tr><th>#</th><th>Category</th><th>Question</th><th>Expected</th><th>Model's reply</th><th>Result</th></tr>
  <tbody id="rows">__ROWS__</tbody>
</table>
<script>
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('run').onclick = async () => {
  const btn = document.getElementById('run'), summary = document.getElementById('summary');
  btn.disabled = true; summary.className = ''; summary.textContent = '';
  let passed = 0;
  for (let i = 0; i < __COUNT__; i++) {
    const row = document.getElementById('row' + i);
    row.cells[5].innerHTML = '<span class="wait">Running...</span>';
    row.scrollIntoView({block: 'center', behavior: 'smooth'});
    const r = await (await fetch('/run-test', {method: 'POST', body: JSON.stringify({index: i})})).json();
    passed += r.passed;
    row.cells[4].innerHTML = esc(r.sql ?? r.error ?? '');
    row.cells[5].innerHTML = r.passed
      ? '<span class="pass">PASS</span> ' + r.seconds + 's'
      : '<span class="fail">FAIL</span> ' + esc(r.outcome);
  }
  const pct = Math.round(passed / __COUNT__ * 1000) / 10;
  summary.className = passed === __COUNT__ ? 'ok' : 'bad';
  summary.innerHTML = '<b>' + passed + ' of __COUNT__ passed (' + pct + '%)</b>';
  summary.scrollIntoView({block: 'center', behavior: 'smooth'});
  btn.disabled = false;
};
</script>
</body>
</html>
"""

EXPECTED_LABELS = {"answer": "Correct result", "clarify": "Ask a follow-up", "refuse": "Refuse"}


def tests_page():
    rows = "".join(
        f"<tr id='row{i}'><td>{i + 1}</td><td>{html.escape(c['category'])}</td>"
        f"<td>{html.escape(c['question'])}</td><td>{EXPECTED_LABELS[c['expected_behavior']]}</td>"
        f"<td class='sql'></td><td></td></tr>"
        for i, c in enumerate(TEST_CASES)
    )
    return (TESTS_PAGE.replace("__STYLE__", STYLE)
            .replace("__ROWS__", rows)
            .replace("__COUNT__", str(len(TEST_CASES))))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Don't print each request to the console: on Windows, clicking in the
        # terminal pauses console output, which froze the server mid-reply.
        # Queries are still logged to queries.log by run_query.
        pass

    def send_body(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/tests":
            page = tests_page()
        else:
            page = PAGE.replace("__STYLE__", STYLE).replace("__SCHEMA__", html.escape(SCHEMA))
        self.send_body(page.encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path not in ("/ask", "/run-test"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        try:
            if self.path == "/run-test":
                data = run_case(TEST_CASES[int(request["index"])], SCHEMA)
            else:
                question = request.get("question", "").strip()
                data = answer(question) if question else {"kind": "error", "message": "Please type a question."}
        except Exception as e:  # e.g. Ollama not running
            data = {"kind": "error", "message": str(e), "passed": False, "outcome": "error", "error": str(e)}
        self.send_body(json.dumps(data, default=str).encode(), "application/json")


class Server(ThreadingHTTPServer):
    # On Windows, address reuse lets a second copy of the app share the port,
    # and requests can go to an old, stuck copy. Fail loudly instead.
    allow_reuse_address = sys.platform != "win32"


if __name__ == "__main__":
    try:
        server = Server(("localhost", PORT), Handler)
    except OSError:
        sys.exit(f"Port {PORT} is already in use. Is the app already running? Close it first.")
    print("Loading the model (this can take up to a minute the first time)...")
    warm_up()
    print(f"Ready. Open http://localhost:{PORT}")
    server.serve_forever()
