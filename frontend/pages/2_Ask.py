"""
2_Ask.py - ask the LogiStream agent (Groq + RAG + MCP tools) a question.

Put at D:\\LogiStream\\frontend\\pages\\2_Ask.py  (Streamlit adds it to the sidebar).
Needs: the Ollama app running (for the RAG embeddings) and GROQ_API_KEY in .env.
"""
import asyncio
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "agent"))
from agent import Agent  # noqa: E402

st.set_page_config(page_title="Ask LogiStream", layout="wide")
st.title("Ask LogiStream")
st.caption("The agent reads the business definitions (RAG) and calls data tools (MCP). "
           "Answers come only from those two sources.")

EXAMPLES = [
    "How many orders are currently in transit?",
    "Which hub has the highest number of late shipments?",
    "What counts as a late shipment?",
    "Has the late-delivery rate increased today compared with yesterday?",
    "Where is shipment 865 right now?",
    "What is the average delivery cost per order?",
]


async def ask_agent(question):
    # A fresh agent per question: it starts the MCP server, answers, and shuts it down.
    async with Agent("both") as agent:
        return await agent.ask(question)


if "history" not in st.session_state:
    st.session_state.history = {}   # question -> result (so repeats cost no Groq tokens)

st.write("Try an example:")
cols = st.columns(3)
for i, example in enumerate(EXAMPLES):
    if cols[i % 3].button(example, key=f"ex{i}"):
        st.session_state.question = example

question = st.text_input("Your question", key="question")

if st.button("Ask", type="primary") and question.strip():
    q = question.strip()
    if q not in st.session_state.history:
        with st.spinner("Thinking (retrieving definitions and calling tools)..."):
            st.session_state.history[q] = asyncio.run(ask_agent(q))
    r = st.session_state.history[q]

    if r["error"]:
        st.error("The agent could not answer.")
        st.code(r["error"])
        if "429" in r["error"] or "rate" in r["error"].lower():
            st.info("This looks like a Groq rate limit (free tier). Wait a minute and try again.")
    else:
        st.subheader("Answer")
        st.markdown(r["answer"])

    st.divider()
    left, right = st.columns(2)
    with left:
        st.subheader("Tools called")
        if r["tool_calls"]:
            for c in r["tool_calls"]:
                note = " (repaired)" if c.get("repaired") else ""
                st.code(f"{c['name']}({c['arguments']}){note}  ok={c['ok']}", language="text")
        else:
            st.write("None. The answer came from the definitions or the model refused.")
    with right:
        st.subheader("Definitions retrieved")
        for section in r["rag_sections"]:
            st.write("- " + section.split(" > ")[-1])

    st.caption(
        f"{r['seconds']} s, {r['steps']} model calls, "
        f"{r['prompt_tokens']} tokens read / {r['gen_tokens']} written, "
        f"{r.get('recoveries', 0)} tool-call repairs. Model: {r['model']}"
    )
