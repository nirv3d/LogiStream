"""
agent.py - LogiStream AI agent powered by Groq.

Setups:
    llm  - Groq alone
    rag  - Groq + retrieved business definitions
    mcp  - Groq + MCP tools
    both - Groq + RAG + MCP tools

Environment variables:
    GROQ_API_KEY
    GROQ_MODEL
    GROQ_TIMEOUT_SECONDS
    MAX_TOOL_CHARS

Run from D:\\LogiStream:
    python agent\\agent.py --setup llm "What is a logistics pipeline?"
    python agent\\agent.py --setup mcp "How many orders are in transit?"
    python agent\\agent.py --setup both "What counts as a late shipment?"
"""

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq


ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "server.py"

# Load the project's .env file.
load_dotenv(ROOT / ".env")

MODEL = os.environ.get(
    "GROQ_MODEL",
    os.environ.get("AGENT_MODEL", "openai/gpt-oss-120b")
)

GROQ_TIMEOUT_SECONDS = int(
    os.environ.get("GROQ_TIMEOUT_SECONDS", "120")
)

MAX_TOOL_CHARS = int(os.environ.get("MAX_TOOL_CHARS", "12000"))

MAX_STEPS = 6
SETUPS = ("llm", "rag", "mcp", "both")



BASE_PROMPT = (
    "You are an AI assistant for a courier company's logistics data "
    "system in Delhi-NCR. You answer questions about orders, shipments, "
    "hubs, vehicles, deliveries and incidents.\n\n"

    "GENERAL RULES:\n"
    "- Never guess or invent numbers, names, dates, locations or facts.\n"
    "- Only use information provided in the context or returned by tools.\n"
    "- If you do not have enough information to answer, clearly explain "
    "what is missing instead of making up an answer.\n"
    "- If the required data does not exist in the system, explicitly say so.\n"
    "- Keep answers concise, clear and in plain language.\n"
    "- Include actual numbers and relevant comparisons when available.\n"
    "- Do not claim that an operation or database query succeeded unless "
    "a tool actually returned the result.\n\n"

    "TIME AND DATE RULES:\n"
    "- For questions about today, now, yesterday or the last hour, "
    "use the timestamps and time boundaries provided by the tools.\n"
    "- Mention the as_of timestamp returned by the tool when answering "
    "time-sensitive questions.\n"
    "- Do not manually guess or convert time zones. Only provide a "
    "converted timestamp when the conversion is known to be correct.\n"
    "- If today's data covers only part of the day, clearly mention "
    "that it is a partial-day result.\n\n"

    "SHIPMENT LOCATION RULES:\n"
    "- The system does not have live GPS coordinates or live physical "
    "locations for vehicles or shipments.\n"
    "- Never infer a shipment's current physical location from its "
    "origin hub, destination hub, route, shipment status or delay.\n"
    "- Never say that a shipment is between two hubs, near a hub, "
    "or at a particular location unless an actual location field "
    "or GPS result explicitly confirms it.\n"
    "- If asked where a shipment is right now, clearly explain that "
    "its live physical location cannot be determined from the available data.\n"
    "- You may still provide its shipment status, origin hub, destination "
    "hub, planned arrival and current delay if those details are available.\n\n"

    "DATA LIMITATIONS:\n"
    "- Do not confuse a shipment being delayed with knowing its physical location.\n"
    "- Do not confuse an incident description with an aggregated incident count.\n"
    "- Do not calculate totals from a limited sample of records.\n"
    "- If a tool returns sample records or truncated results, clearly "
    "identify them as samples and do not present them as complete data.\n"
    "- Do not claim that intermediate hub dwell time, delivery costs, "
    "live GPS positions or other unavailable metrics exist.\n\n"

    "ANSWERING STYLE:\n"
    "- Answer the actual question directly.\n"
    "- Use bullet points or small tables when they make comparisons easier.\n"
    "- Distinguish between confirmed facts, estimates and unavailable information.\n"
    "- Never hide uncertainty or present an assumption as a confirmed fact."
)



TOOLS_NOTE = (
    "\nYou have tools that read the company's data. Use a tool for every number, "
    "status or list: call it, read the result, then answer. Do not say you have no "
    "access before you have tried the tools.\n"

    "\nTOOL ROUTING RULES:\n"
    "- For order counts, cancellations, or order status totals, use order_counts.\n"
    "- For a specific order, use order_lookup.\n"
    "- For shipment status or shipment details, use shipment_status.\n"
    "- For currently delayed shipments, use currently_delayed_shipments.\n"
    "- For delivery averages, late percentages, and on-time performance, "
    "use delivery_summary.\n"
    "- For hub comparisons or the hub with the most late shipments, "
    "use hub_performance.\n"
    "- For city comparisons, use city_performance.\n"
    "- For daily changes and today-versus-yesterday trends, use daily_trends.\n"
    "- For vehicle rankings, use vehicle_ranking.\n"
    "- For incident counts grouped by hub, use incidents_by_hub.\n"

    "\nINCIDENT TOOL RULES:\n"
    "- For the most common incident types and their total counts, "
    "use incident_type_summary.\n"
    "- For descriptions of incidents at a hub, the EXACT tool name is "
    "incident_descriptions. Spell it exactly as written.\n"
    "- Never use incident_desinations or any other variation.\n"
    "- If asked which hub has the most incidents AND what its descriptions say, "
    "call incidents_by_hub first.\n"
    "- Then call incident_descriptions with hub_name and limit=10.\n"
    "- Treat the returned descriptions as samples, not all incidents.\n"

    "\nIMPORTANT DATA LIMITATIONS:\n"
    "- Do not invent missing data or guess values.\n"
    "- Do not claim to know a shipment's live GPS location. "
    "shipment_status does not provide live GPS tracking.\n"
    "- Do not infer a shipment's exact current location from its origin "
    "and destination hubs.\n"
    "- If a question requires data that the tools do not contain, "
    "clearly say that the data is unavailable.\n"
)


RAG_NOTE = (
    "\nThe business rules (definitions) for this data are given in the message under "
    "'Context'. Use them to explain terms and to read the question correctly."
)


# ------------------------------------------------------------ Groq helpers

def _simplify(value):
    """
    Simplify an MCP JSON schema for the model.

    Removes unnecessary titles and simplifies nullable fields.
    Recursively handles nested objects and arrays.
    """
    if isinstance(value, list):
        return [_simplify(item) for item in value]

    if not isinstance(value, dict):
        return value

    result = {}

    for key, item in value.items():
        if key in ("title", "$schema"):
            continue

        if key == "anyOf":
            options = [
                option for option in item
                if not (
                    isinstance(option, dict)
                    and option.get("type") == "null"
                )
            ]

            if len(options) == 1:
                simplified = _simplify(options[0])
                result.update(simplified)
            else:
                result[key] = _simplify(options)

        else:
            result[key] = _simplify(item)

    return result


def to_groq_tools(mcp_tools):
    """
    Convert MCP tool definitions into Groq/OpenAI-compatible
    function declarations.

    Groq receives the tool names, descriptions and input schemas.
    Python still executes the actual MCP tools.
    """
    declarations = []

    for tool in mcp_tools:
        schema = _simplify(tool.inputSchema or {
            "type": "object",
            "properties": {}
        })

        declarations.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": schema,
            }
        })
    # Compatibility alias for a common Qwen tool-name typo.
    delivery_tool = next(
        (
            item for item in declarations
            if item["function"]["name"] == "delivery_summary"
        ),
        None
    )

    
    if delivery_tool:
        alias = json.loads(json.dumps(delivery_tool))
        alias["function"]["name"] = "delivery_summar"
        alias["function"]["description"] = (
            "Compatibility alias for delivery_summary. "
            "Use this for delivery averages, late percentages, "
            "and on-time performance."
        )
        declarations.append(alias)

    # Compatibility alias for Qwen camelCase tool-name variation.
    schema_tool = next(
        (
            item for item in declarations
            if item["function"]["name"] == "describe_schema"
        ),
        None
    )

    if schema_tool:
        alias = json.loads(json.dumps(schema_tool))
        alias["function"]["name"] = "describeSchema"
        alias["function"]["description"] = (
            "Compatibility alias for describe_schema. "
            "Use this to inspect the available Gold database schema."
        )
        declarations.append(alias)

    return declarations or None



def _compact(text):
    """Reduce the size of JSON tool output."""
    try:
        return json.dumps(
            json.loads(text),
            separators=(",", ":"),
            ensure_ascii=False
        )
    except Exception:
        return text


def _response_tokens(response, field):
    """Safely read token counts from Groq usage metadata."""
    usage = getattr(response, "usage", None)
    return getattr(usage, field, 0) or 0


# ------------------------------------------------------------------ Agent

class Agent:

    def __init__(self, setup, model=MODEL, sql_tool=False):

        if setup not in SETUPS:
            raise ValueError(f"setup must be one of {SETUPS}")

        self.setup = setup
        self.model = model
        self.sql_tool = sql_tool

        self.use_rag = setup in ("rag", "both")
        self.use_mcp = setup in ("mcp", "both")

        self.session = None
        self.tools = []
        self.groq_tools = None
        self._stack = None

        self.system = (
            BASE_PROMPT
            + (TOOLS_NOTE if self.use_mcp else "")
            + (RAG_NOTE if self.use_rag else "")
        )

        # Create Groq client.
        api_key = os.environ.get("GROQ_API_KEY")

        if not api_key:
            raise ValueError(
                "GROQ_API_KEY not found. Check the .env file in D:\\LogiStream."
            )

        self.client = Groq(
            api_key=api_key,
            timeout=GROQ_TIMEOUT_SECONDS,
            max_retries=2,
        )

        # Load RAG only when this setup needs it.
        if self.use_rag:
            sys.path.insert(0, str(ROOT / "rag"))
            import rag_store
            self.rag = rag_store

    async def __aenter__(self):

        self._stack = AsyncExitStack()

        if self.use_mcp:

            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            env = dict(os.environ)
            env["LOGISTREAM_SQL_TOOL"] = "1" if self.sql_tool else "0"

            params = StdioServerParameters(
                command=sys.executable,
                args=[str(SERVER)],
                env=env
            )

            read, write = await self._stack.enter_async_context(
                stdio_client(params)
            )

            self.session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )

            await self.session.initialize()

            # Get available tools from the MCP server.
            mcp_tools = (await self.session.list_tools()).tools

            # Convert them into Groq-compatible function declarations.
            self.groq_tools = to_groq_tools(mcp_tools)

        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    async def _call_tool(self, name, args):
        """
        Execute the MCP tool requested by Groq.

        Groq only suggests the function call.
        This method actually executes it through the MCP session.
        """

        if self.session is None:
            return f"Tool error: no tool called {name} is available.", False
        # Map model-generated aliases to actual MCP tool names.
        if name == "delivery_summar":
            name = "delivery_summary"
        elif name == "describeSchema":
            name = "describe_schema"

        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}

        try:
            result = await self.session.call_tool(name, args)

            text = "\n".join(
                content.text
                for content in result.content
                if getattr(content, "text", None)
            )

            ok = not result.isError

        except Exception as e:
            text = f"Tool error: {e}"
            ok = False

        text = _compact(text)

        if len(text) > MAX_TOOL_CHARS:
            text = text[:MAX_TOOL_CHARS] + "...[cut: result too long]"

        return text, ok

    async def ask(self, question):

        t0 = time.time()

        result = {
            "setup": self.setup,
            "model": self.model,
            "question": question,
            "answer": "",
            "finished": False,
            "steps": 0,
            "tool_calls": [],
            "rag_sections": [],
            "prompt_tokens": 0,
            "gen_tokens": 0,
            "seconds": 0.0,
            "error": None
        }

        try:

            # ------------------------------------------- RAG retrieval

            user = question

            if self.use_rag:

                chunks = await asyncio.to_thread(
                    self.rag.search,
                    question
                )

                result["rag_sections"] = [
                    chunk["section"] for chunk in chunks
                ]

                user = (
                    f"Context (business rules):\n"
                    f"{self.rag.format_context(chunks)}\n\n"
                    f"Question: {question}"
                )

            # Groq conversation history.
            messages = [
                {
                    "role": "system",
                    "content": self.system
                },
                {
                    "role": "user",
                    "content": user
                }
            ]

            # ------------------------------------------- Groq loop

            for _ in range(MAX_STEPS):

                # Send the conversation to Groq.
                request = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0,
                }

                if self.groq_tools:
                    request["tools"] = self.groq_tools
                    request["tool_choice"] = "auto"

                response = await asyncio.to_thread(
                    self.client.chat.completions.create,
                    **request
                )

                result["steps"] += 1

                result["prompt_tokens"] += _response_tokens(
                    response,
                    "prompt_tokens"
                )

                result["gen_tokens"] += _response_tokens(
                    response,
                    "completion_tokens"
                )

                if not response.choices:
                    raise RuntimeError(
                        "Groq returned no response choices."
                    )

                message = response.choices[0].message

                # Add Groq's response to conversation history.
                messages.append(message.model_dump(exclude_none=True))

                # --------------------------------------- No tool call

                tool_calls = message.tool_calls or []

                if not tool_calls:

                    result["answer"] = (message.content or "").strip()
                    result["finished"] = True
                    break

                # --------------------------------------- Execute tools

                for call in tool_calls:

                    name = call.function.name
                    raw_args = call.function.arguments or "{}"

                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}

                    # Execute the actual MCP tool.
                    text, ok = await self._call_tool(name, args)

                    result["tool_calls"].append({
                        "name": name,
                        "arguments": args,
                        "ok": ok,
                        "chars": len(text)
                    })

                    # Send the tool result back to Groq.
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": text
                    })

            if not result["finished"]:
                result["answer"] = "(no final answer: step limit reached)"

        except Exception as e:

            result["error"] = f"{type(e).__name__}: {e}"

        result["seconds"] = round(time.time() - t0, 1)

        return result


# --------------------------------------------------------------------- CLI

async def _main():

    parser = argparse.ArgumentParser(
        description="Ask the LogiStream Groq agent one question."
    )

    parser.add_argument("question", nargs="+")
    parser.add_argument("--setup", choices=SETUPS, default="both")
    parser.add_argument("--model", default=MODEL)

    parser.add_argument(
        "--sql",
        action="store_true",
        help="also give the model the free-form run_sql tool"
    )

    args = parser.parse_args()

    async with Agent(args.setup, args.model, args.sql) as agent:
        result = await agent.ask(" ".join(args.question))

    print(f"\nANSWER:\n{result['answer']}\n")

    print(
        f"setup={result['setup']}  "
        f"model={result['model']}  "
        f"{result['seconds']}s  "
        f"model calls={result['steps']}  "
        f"tokens read/written="
        f"{result['prompt_tokens']}/{result['gen_tokens']}"
    )

    for call in result["tool_calls"]:
        print(
            f"  tool: {call['name']}("
            f"{json.dumps(call['arguments'])})  "
            f"ok={call['ok']}  "
            f"{call['chars']} chars"
        )

    if result["rag_sections"]:
        print(
            "  RAG sections:",
            " | ".join(
                section.split(" > ")[-1]
                for section in result["rag_sections"]
            )
        )

    if result["error"]:
        print("  ERROR:", result["error"])


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    asyncio.run(_main())
