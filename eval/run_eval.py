"""
run_eval.py - runs the 36 questions under the four setups and scores the answers
against ground_truth.json (the answer key made from PostgreSQL).

Every finished run is saved at once to eval\\results.jsonl, and a run that is already
saved is skipped next time. Scoring is done by "report" and NOT while running, so you
can change the scoring rules below and re-score without re-running the model.
To start over, delete results.jsonl.
"""
import argparse
import asyncio
import csv
import json
import re
import sys
import time
from collections import OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GT_PATH = HERE / "ground_truth.json"
RESULTS = HERE / "results.jsonl"
CSV_OUT = HERE / "results.csv"

SETUPS = ("llm", "rag", "mcp", "both")
GUESS_SECONDS = {"llm": 10, "rag": 25, "mcp": 30, "both": 70}  # first guess for time estimates

# ====================================================================== scoring
LIVE = ["live", "real-time", "gps", "current location", "exact location", "position"]
REFUSAL = ["cannot", "can't", "can not", "unable", "not available", "no data", "don't have",
           "do not have", "doesn't have", "does not have", "not measur", "not supported",
           "not possible", "not tracked", "no information", "not able"]

# How each question is scored. review=True means the rule is only a rough check and the
# answers should be read by a human (they are marked in results.csv).
SPEC = {
    "D1": dict(mode="single", cols=["avg_delivery_minutes"]),
    "D2": dict(mode="single", cols=["late_pct", "late_shipments", "delivered_shipments"]),
    "D3": dict(mode="set"),
    "D4": dict(mode="single", cols=["on_time_today"]),
    "D5": dict(mode="single", cols=["avg_delay_minutes"]),
    "D6": dict(mode="table", entity="city", cols=["on_time_pct"]),
    "H1": dict(mode="top", entity="hub", value="late_deliveries"),
    "H2": dict(mode="table", entity="hub", cols=["late_deliveries", "on_time_pct"]),
    "H3": dict(mode="table", entity="hub", cols=["shipments"]),
    "H4": dict(mode="top", entity="hub", value="incidents"),
    "H5": dict(mode="top", entity="hub", value="avg_processing_minutes"),
    "H6": dict(mode="top", entity="hub", value="on_time_pct"),
    "O1": dict(mode="single", cols=["cancelled_today"]),
    "O2": dict(mode="single", cols=["cancellation_pct"]),
    "O3": dict(mode="single", cols=["packed_orders"]),
    "O4": dict(mode="single", cols=["created_last_hour"]),
    "O5": dict(mode="single", cols=["in_transit_orders"]),
    "O6": dict(mode="keywords", from_key=["status"]),
    "O7": dict(mode="top", entity="city", value="cancellation_pct"),
    "C1": dict(mode="table", entity="city", cols=["on_time_pct", "late_pct", "avg_delivery_minutes"]),
    "C2": dict(mode="top", entity="city", value="avg_delivery_minutes"),
    "V1": dict(mode="top", entity="plate_no", value="incidents"),
    "V2": dict(mode="top", entity="plate_no", value="shipments"),
    "V3": dict(mode="top", entity="plate_no", value="late_shipments"),
    "T1": dict(mode="values", cols=["late_pct"]),
    "T2": dict(mode="values", cols=["late_pct"]),
    "T3": dict(mode="values", cols=["avg_delivery_minutes"]),
    # rough rules: a person should read these answers
    "V4": dict(mode="top", entity="incident_type", value="incidents", review=True),
    "M1": dict(mode="keywords", from_key=["hub"], review=True),
    "M2": dict(mode="top", entity="city", value="late_pct", review=True),
    "R1": dict(mode="keywords", groups=[["late"], ["planned"], ["actual arrival", "delivered", "arriv"]], review=True),
    "R2": dict(mode="keywords", groups=[["on time", "on-time"], ["delivered"], ["divid", "percent", "100", "ratio"]], review=True),
    "R3": dict(mode="keywords", groups=[["deleted", "delete"], ["silver", "source", "postgres"]], review=True),
    "U1": dict(mode="refusal", review=True),
    "U3": dict(mode="refusal", review=True),
    "P1": dict(mode="keywords", from_key=["shipment_status", "origin_hub", "destination_hub"],
               groups=[LIVE], review=True),
}

_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def numbers_in(text):
    return [float(m.group().replace(",", "")) for m in _NUM.finditer(text)]


def has_value(nums, v):
    """Whole numbers must match exactly; decimals may be off by 1%."""
    if v is None or isinstance(v, bool):
        return True
    if isinstance(v, int):
        return any(abs(n - v) < 1e-9 for n in nums)
    tol = max(0.01, 0.01 * abs(v))
    return any(abs(n - v) <= tol for n in nums)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def find_mentions(text, entities):
    """Where each entity name appears in the answer. Longer names are matched first,
    so 'New Delhi' is not also counted as 'Delhi'."""
    claimed, out = [], []
    for ent in sorted(set(entities), key=len, reverse=True):
        chars = [c for c in ent.lower() if c.isalnum()]
        if not chars:
            continue
        pat = re.compile(r"(?<![a-z0-9])" + r"[\W_]*".join(re.escape(c) for c in chars)
                         + r"(?![a-z0-9])", re.I)
        for m in pat.finditer(text):
            if any(m.start() < ce and m.end() > cs for cs, ce in claimed):
                continue
            claimed.append((m.start(), m.end()))
            out.append((m.start(), m.end(), ent))
    return sorted(out)


def score(q, answer):
    """Returns (score between 0 and 1 or None, method, needs_human_review)."""
    spec = SPEC.get(q["id"])
    if spec is None:
        return None, "no rule", True
    mode, review = spec["mode"], spec.get("review", False)
    if not answer.strip():
        return 0.0, mode, review
    nums = numbers_in(answer)
    ans = q.get("answer") or {}
    ci = lambda name: ans["columns"].index(name)  # noqa: E731

    if mode == "single":
        vals = [ans["rows"][0][ci(c)] for c in spec["cols"]]
        return sum(has_value(nums, v) for v in vals) / len(vals), mode, review

    if mode == "values":
        vals = [r[ci(c)] for r in ans["rows"] for c in spec["cols"]]
        return sum(has_value(nums, v) for v in vals) / len(vals), mode, review

    if mode == "set":
        ids = [r[0] for r in ans["rows"]]
        if not ids:
            return None, "set (key is empty)", True
        found = {int(n) for n in nums if n == int(n)}
        return sum(i in found for i in ids) / len(ids), mode, review

    if mode == "top":
        ei, vi = ci(spec["entity"]), ci(spec["value"])
        rows = ans["rows"]
        top_val = rows[0][vi]
        tied = {str(r[ei]) for r in rows if r[vi] == top_val}
        mentions = find_mentions(answer, [str(r[ei]) for r in rows])
        ok_entity = bool(mentions) and mentions[0][2] in tied
        ok_value = has_value(nums, top_val)
        return (ok_entity + ok_value) / 2, mode, review

    if mode == "table":
        ei = ci(spec["entity"])
        rows = ans["rows"]
        mentions = find_mentions(answer, [str(r[ei]) for r in rows])
        good = 0
        for r in rows:
            name = str(r[ei])
            vals = [r[ci(c)] for c in spec["cols"]]
            for k, (start, end, ent) in enumerate(mentions):
                if ent != name:
                    continue
                stop = mentions[k + 1][0] if k + 1 < len(mentions) else len(answer)
                seg = numbers_in(answer[end:min(stop, end + 300)])
                if all(has_value(seg, v) for v in vals):
                    good += 1
                    break
        return good / len(rows), mode, review

    if mode == "keywords":
        groups = [[str(ans["rows"][0][ci(c)])] for c in spec.get("from_key", [])]
        groups += spec.get("groups", [])
        na = norm(answer)
        hit = [any(norm(a) and norm(a) in na for a in g) for g in groups]
        return sum(hit) / len(hit), mode, review

    if mode == "refusal":
        na = norm(answer)
        return float(any(norm(p) in na for p in REFUSAL)), mode, review

    return None, "unknown mode", True


# ================================================================== file helpers
def load_gt():
    return json.loads(GT_PATH.read_text(encoding="utf-8"))


def read_results():
    recs = []
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def fmt(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m" if seconds >= 3600 else f"{seconds // 60}m{seconds % 60:02d}s"


# ========================================================================== run
async def run(args):
    sys.path.insert(0, str(ROOT / "agent"))
    from agent import MODEL, Agent  # noqa: E402

    gt = load_gt()
    qs = gt["questions"]
    if args.only:
        wanted = set(args.only.split(","))
        qs = [q for q in qs if q["id"] in wanted]
    if args.sample:
        seen, picked = set(), []
        for q in qs:
            if q["category"] not in seen:
                seen.add(q["category"])
                picked.append(q)
        qs = picked
    if args.limit:
        qs = qs[:args.limit]
    setups = [s for s in args.setups.split(",") if s in SETUPS]
    model = args.model or MODEL

    old = read_results()
    done = set() if args.redo else {(r["qid"], r["setup"]) for r in old if not r.get("error")}
    todo = {s: [q for q in qs if (q["id"], s) not in done] for s in setups}
    total = sum(len(v) for v in todo.values())
    if total == 0:
        print("Nothing to do: all of these runs are already saved. Try:  python eval\\run_eval.py report")
        return

    avg = dict(GUESS_SECONDS)
    for s in SETUPS:
        secs = [r["seconds"] for r in old if r["setup"] == s and not r.get("error")]
        if secs:
            avg[s] = sum(secs) / len(secs)
    eta = sum(len(todo[s]) * avg[s] for s in setups)
    print(f"Model: {model}   runs to do: {total}   rough time: {fmt(eta)} "
          f"(guess from earlier timings; the real number shows as it goes)\n")

    n_done, t_start = 0, time.time()
    for setup in setups:
        if not todo[setup]:
            continue
        print(f"=== setup: {setup} ({len(todo[setup])} questions) ===")
        async with Agent(setup, model, args.sql) as agent:
            for q in todo[setup]:
                r = await agent.ask(q["question"])
                r.update(qid=q["id"], category=q["category"],
                         finished_at=datetime.now().isoformat(timespec="seconds"))
                with open(RESULTS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                n_done += 1
                if r["error"]:
                    mark = "ERROR"
                else:
                    sc, _, review = score(q, r["answer"])
                    mark = "n/a" if sc is None else ("OK" if sc >= 0.999 else "X")
                    mark += "?" if review else ""
                avg[setup] = (avg[setup] + r["seconds"]) / 2
                remaining = total - n_done
                eta = remaining * (sum(avg[s] for s in setups) / len(setups))
                print(f"[{n_done:>3}/{total}] {setup:<4} {q['id']:<3} {r['seconds']:>6.1f}s "
                      f"tools={len(r['tool_calls'])}  {mark:<6} (about {fmt(eta)} left)")
                if r["error"]:
                    print("        ", r["error"][:300])
                    if "Cannot reach Ollama" in r["error"]:
                        print("Stopping: start the Ollama app and run the same command again.")
                        return
    print(f"\nFinished {n_done} runs in {fmt(time.time() - t_start)}. Next:  python eval\\run_eval.py report")


# ======================================================================= report
def expected_text(q):
    if q.get("answer_text"):
        return q["answer_text"]
    a = q.get("answer")
    if a:
        return " | ".join(json.dumps(dict(zip(a["columns"], r)), default=str) for r in a["rows"][:3])
    return ""


def report():
    gt = load_gt()
    latest = {}
    for r in read_results():
        latest[(r["qid"], r["setup"])] = r  # the last run of a question wins
    if not latest:
        print("No results yet. Run:  python eval\\run_eval.py run --sample")
        return

    cats = list(OrderedDict.fromkeys(q["category"] for q in gt["questions"]))
    cell = defaultdict(lambda: [0, 0])          # (category, setup) -> [correct, total]
    clean = defaultdict(lambda: [0, 0])         # setup -> only automatic-rule questions
    allq = defaultdict(lambda: [0, 0])          # setup -> everything with a rule
    secs, tool_runs, runs, errors = defaultdict(list), defaultdict(int), defaultdict(int), defaultdict(int)
    out_rows = []

    for q in gt["questions"]:
        for setup in SETUPS:
            r = latest.get((q["id"], setup))
            if not r:
                continue
            if r.get("error"):
                sc, method, review = 0.0, "error", False
                errors[setup] += 1
            else:
                sc, method, review = score(q, r["answer"])
            correct = None if sc is None else sc >= 0.999
            if sc is not None:
                cell[(q["category"], setup)][1] += 1
                allq[setup][1] += 1
                if correct:
                    cell[(q["category"], setup)][0] += 1
                    allq[setup][0] += 1
                if not review:
                    clean[setup][1] += 1
                    clean[setup][0] += int(correct)
            runs[setup] += 1
            secs[setup].append(r["seconds"])
            tool_runs[setup] += 1 if r["tool_calls"] else 0
            out_rows.append({
                "qid": q["id"], "category": q["category"], "setup": setup,
                "correct": "" if correct is None else int(correct),
                "score": "" if sc is None else round(sc, 2), "method": method,
                "needs_review": "yes" if review else "",
                "question": q["question"], "expected": expected_text(q),
                "answer": r["answer"],
                "tools_called": "; ".join(f"{c['name']}({json.dumps(c['arguments'])})" for c in r["tool_calls"]),
                "seconds": r["seconds"], "model_calls": r["steps"],
                "tokens_read": r["prompt_tokens"], "tokens_written": r["gen_tokens"],
                "finished": r["finished"], "error": r.get("error") or "",
                "manual_correct": "",
            })

    with open(CSV_OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    present = [s for s in SETUPS if runs[s]]
    print(f"\nCorrect / answered, by category   (answers: {sum(runs.values())})")
    print(f"{'category':<14}" + "".join(f"{s:>9}" for s in present))
    for c in cats:
        if any(cell[(c, s)][1] for s in present):
            print(f"{c:<14}" + "".join(f"{cell[(c, s)][0]}/{cell[(c, s)][1]}".rjust(9) for s in present))
    print("-" * (14 + 9 * len(present)))
    print(f"{'all rules':<14}" + "".join(f"{allq[s][0]}/{allq[s][1]}".rjust(9) for s in present))
    print(f"{'  %':<14}" + "".join(f"{100 * allq[s][0] / max(1, allq[s][1]):.0f}%".rjust(9) for s in present))
    print(f"{'no review':<14}" + "".join(f"{clean[s][0]}/{clean[s][1]}".rjust(9) for s in present))
    print(f"{'avg seconds':<14}" + "".join(f"{sum(secs[s]) / len(secs[s]):.0f}".rjust(9) for s in present))
    print(f"{'used a tool':<14}" + "".join(f"{tool_runs[s]}/{runs[s]}".rjust(9) for s in present))
    print(f"{'errors':<14}" + "".join(str(errors[s]).rjust(9) for s in present))
    print("\n'no review' counts only questions with an exact numeric check; 'all rules' also includes")
    print("the rough keyword checks (definitions, unanswerable, mixed). Read those answers in results.csv.")
    print(f"Wrote {CSV_OUT}")


# ========================================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="run questions through the agent")
    p.add_argument("--setups", default=",".join(SETUPS), help="comma list, default all four")
    p.add_argument("--only", help="comma list of question ids, e.g. D1,O5")
    p.add_argument("--sample", action="store_true", help="first question of each category")
    p.add_argument("--limit", type=int, help="only the first N questions")
    p.add_argument("--model", help="Ollama model (default: AGENT_MODEL or llama3.2:3b)")
    p.add_argument("--sql", action="store_true", help="give the mcp/both setups the free-form run_sql tool")
    p.add_argument("--redo", action="store_true", help="run again even if already saved (the newest run counts in the report)")
    sub.add_parser("report", help="score what is saved and write results.csv")
    a = ap.parse_args()
    if a.cmd == "run":
        asyncio.run(run(a))
    else:
        report()


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    main()