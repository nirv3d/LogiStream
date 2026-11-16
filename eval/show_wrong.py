"""
show_wrong.py - print the wrong answers of the MCP setups next to the expected ones.

Put at D:\\LogiStream\\eval\\show_wrong.py and run (after "run_eval.py report"):
    python eval\\show_wrong.py              wrong answers of mcp and both
    python eval\\show_wrong.py llm rag      choose other setups
"""
import csv
import sys
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent / "results.csv"
setups = sys.argv[1:] or ["mcp", "both"]
sys.stdout.reconfigure(errors="replace")

for r in csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")):
    if r["correct"] == "0" and r["setup"] in setups:
        print("=" * 70)
        print(f"{r['setup']}  {r['qid']}  {r['question']}")
        print("EXPECTED:", r["expected"][:350])
        print("ANSWER  :", r["answer"][:500])
        print("TOOLS   :", r["tools_called"] or "(none)")
