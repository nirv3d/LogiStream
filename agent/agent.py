"""
agent.py - the LogiStream AI agent, with four setups that differ ONLY in what the
model can access:

    llm    the model alone (no data, no documents)
    rag    + the definitions document (retrieved chunks pasted into the question)
    mcp    + the MCP tools (it can read the real data)
    both   RAG and MCP together

Same model, same instructions, same settings (temperature 0) in every setup, so
differences in accuracy come from the setup and not from anything else.

Put this at D:\\LogiStream\\agent\\agent.py
Needs: Ollama running with the model pulled (ollama pull llama3.2:3b), and for
the rag/both setups the index from rag_store.py (python rag\\rag_store.py build).

Try it (from D:\\LogiStream):
    python agent\\agent.py --setup mcp "How many orders are currently in transit?"
    python agent\\agent.py --setup both "What counts as a late shipment?"

Settings can be changed with environment variables: AGENT_MODEL, NUM_CTX,
MAX_TOOL_CHARS, OLLAMA_URL.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from contextlib import AsyncExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "server.py"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("AGENT_MODEL", "llama3.2:3b")
NUM_CTX = int(os.environ.get("NUM_CTX", "8192"))          # how much text the model can hold
MAX_TOOL_CHARS = int(os.environ.get("MAX_TOOL_CHARS", "12000"))
MAX_STEPS = 6                                              # model calls per question
SETUPS = ("llm", "rag", "mcp", "both")

BASE_PROMPT = (
    "You answer questions about a courier company's delivery data "
    "(orders, shipments, hubs, vehicles and incidents in Delhi-NCR).\n"
    "Rules:\n"
    "- Never guess or invent numbers, names or facts.\n"
    "- If you do not have what you need to answer (no matching tool result, nothing "
    "in the provided context, or the data does not exist), say clearly that you "
    "cannot answer and why.\n"
    "- Keep answers short and in plain language, and include the actual numbers.\n"
    "- For questions about today, now or the last hour, mention the as_of time from "
    "the tool result."
)
TOOLS_NOTE = (
    "\nYou have tools that read the company's data. Use a tool for every number, "
    "status or list: call it, read the result, then answer. Do not say you have no "
    "access before you have tried the tools."
)
RAG_NOTE = (
    "\nThe business rules (definitions) for this data are given in the message under "
    "'Context'. Use them to explain terms and to read the question correctly."
)


# ------------------------------------------------------------ Ollama helpers
def ollama_chat(model, messages, tools=None):
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": "30m",  # keep the model in memory between questions
        "options": {"temperature": 0, "seed": 42, "num_ctx": NUM_CTX},
    }
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(
        OLLAMA_URL + "/api/chat", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Ollama error {e.code}: {e.read().decode('utf-8', 'replace')}\n"
            f"Is the model pulled?  ollama pull {model}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Ollama at {OLLAMA_URL}. Is the Ollama app running?") from e


def _simplify(prop):
    """Make an MCP/pydantic JSON schema simple enough for Ollama (drop 'title',
    turn 'anything or null' into just 'anything')."""
    prop = dict(prop)
    prop.pop("title", None)
    if "anyOf" in prop:
        options = [o for o in prop.pop("anyOf") if o.get("type") != "null"]
        if options:
            prop.update(_simplify(options[0]))
    if isinstance(prop.get("items"), dict):
        prop["items"] = _simplify(prop["items"])
    return prop


def to_ollama_tools(mcp_tools):
    out = []
    for t in mcp_tools:
        schema = t.inputSchema or {}
        out.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": {
                    "type": "object",
                    "properties": {k: _simplify(v) for k, v in schema.get("properties", {}).items()},
                    "required": schema.get("required", []),
                },
            },
        })
    return out


def _compact(text):
    """Shrink JSON tool output (fewer tokens = faster on a CPU)."""
    try:
        return json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return text


# --------------------------------------------------------------------- agent
class Agent:
    def __init__(self, setup, model=MODEL, sql_tool=False):
        if setup not in SETUPS:
            raise ValueError(f"setup must be one of {SETUPS}")
        self.setup, self.model, self.sql_tool = setup, model, sql_tool
        self.use_rag = setup in ("rag", "both")
        self.use_mcp = setup in ("mcp", "both")
        self.session, self.tools, self._stack = None, [], None
        self.system = BASE_PROMPT + (TOOLS_NOTE if self.use_mcp else "") + (RAG_NOTE if self.use_rag else "")
        if self.use_rag:
            sys.path.insert(0, str(ROOT / "rag"))
            import rag_store  # noqa: E402
            self.rag = rag_store

    async def __aenter__(self):
        self._stack = AsyncExitStack()
        if self.use_mcp:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            env = dict(os.environ)
            env["LOGISTREAM_SQL_TOOL"] = "1" if self.sql_tool else "0"
            params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self.session = await self._stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            self.tools = to_ollama_tools((await self.session.list_tools()).tools)
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def _call_tool(self, name, args):
        if self.session is None:
            return f"Tool error: no tool called {name} is available.", False
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        try:
            res = await self.session.call_tool(name, args)
            text = "\n".join(c.text for c in res.content if getattr(c, "text", None))
            ok = not res.isError
        except Exception as e:  # the model sees the error and can try again
            text, ok = f"Tool error: {e}", False
        text = _compact(text)
        if len(text) > MAX_TOOL_CHARS:
            text = text[:MAX_TOOL_CHARS] + "...[cut: result too long]"
        return text, ok

    async def ask(self, question):
        t0 = time.time()
        r = {"setup": self.setup, "model": self.model, "question": question,
             "answer": "", "finished": False, "steps": 0, "tool_calls": [],
             "rag_sections": [], "prompt_tokens": 0, "gen_tokens": 0,
             "seconds": 0.0, "error": None}
        try:
            user = question
            if self.use_rag:
                chunks = await asyncio.to_thread(self.rag.search, question)
                r["rag_sections"] = [c["section"] for c in chunks]
                user = f"Context (business rules):\n{self.rag.format_context(chunks)}\n\nQuestion: {question}"
            messages = [{"role": "system", "content": self.system},
                        {"role": "user", "content": user}]

            for _ in range(MAX_STEPS):
                resp = await asyncio.to_thread(ollama_chat, self.model, messages, self.tools or None)
                r["steps"] += 1
                r["prompt_tokens"] += resp.get("prompt_eval_count", 0)
                r["gen_tokens"] += resp.get("eval_count", 0)
                msg = resp["message"]
                messages.append(msg)
                calls = msg.get("tool_calls") or []
                if not calls:
                    r["answer"] = (msg.get("content") or "").strip()
                    r["finished"] = True
                    break
                for call in calls:
                    fn = call["function"]
                    name, args = fn["name"], fn.get("arguments") or {}
                    text, ok = await self._call_tool(name, args)
                    r["tool_calls"].append({"name": name, "arguments": args, "ok": ok, "chars": len(text)})
                    messages.append({"role": "tool", "tool_name": name, "content": text})
            if not r["finished"]:
                r["answer"] = "(no final answer: step limit reached)"
        except Exception as e:
            r["error"] = f"{type(e).__name__}: {e}"
        r["seconds"] = round(time.time() - t0, 1)
        return r


# ----------------------------------------------------------------------- CLI
async def _main():
    ap = argparse.ArgumentParser(description="Ask the LogiStream agent one question.")
    ap.add_argument("question", nargs="+")
    ap.add_argument("--setup", choices=SETUPS, default="both")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--sql", action="store_true", help="also give the model the free-form run_sql tool")
    a = ap.parse_args()

    async with Agent(a.setup, a.model, a.sql) as agent:
        r = await agent.ask(" ".join(a.question))

    print(f"\nANSWER:\n{r['answer']}\n")
    print(f"setup={r['setup']}  model={r['model']}  {r['seconds']}s  "
          f"model calls={r['steps']}  tokens read/written={r['prompt_tokens']}/{r['gen_tokens']}")
    for c in r["tool_calls"]:
        print(f"  tool: {c['name']}({json.dumps(c['arguments'])})  ok={c['ok']}  {c['chars']} chars")
    if r["rag_sections"]:
        print("  RAG sections:", " | ".join(s.split(" > ")[-1] for s in r["rag_sections"]))
    if r["error"]:
        print("  ERROR:", r["error"])


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    asyncio.run(_main())
