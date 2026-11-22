"""0.37.2: Hintergrund-Threads duerfen erst starten, wenn alles definiert ist, was sie aufrufen.

Der Tagesbericht-Thread startete beim Import (vor `_live_values`): nach einem Neustart vormittags lief er sofort in
einen NameError und holte den Bericht erst 5 min spaeter nach."""
import ast
import os

MAIN = os.path.join(os.path.dirname(__file__), "..", "app", "main.py")


def _top_level():
    return ast.parse(open(MAIN, encoding="utf-8").read()).body


def test_tagesbericht_thread_startet_nach_allen_definitionen():
    body = _top_level()
    defs = {n.name: n.lineno for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    start = [n.lineno for n in body if "_report_loop" in ast.unparse(n) and "Thread" in ast.unparse(n)
             and not isinstance(n, ast.FunctionDef)]
    assert start, "Start des Tagesbericht-Threads nicht gefunden"
    for name in ("_live_values", "_report_health", "_report_space", "_usage_tick", "_report_loop"):
        assert defs[name] < start[0], f"{name} (Zeile {defs[name]}) erst nach dem Thread-Start (Zeile {start[0]}) definiert"
