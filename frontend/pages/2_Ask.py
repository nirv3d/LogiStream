
"""
Ask LogiStream - AI Assistant

Powered by Groq + RAG + MCP tools.
"""

import asyncio
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from agent import Agent


# ----------------------------------------------------------
# PAGE CONFIG
# ----------------------------------------------------------

st.set_page_config(
    page_title="LogiStream | Ask",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ----------------------------------------------------------
# STYLING
# ----------------------------------------------------------

st.markdown("""
<style>
    /* Main background */
    .stApp {
        background-color: #0F172A;
        color: #E2E8F0;
    }

    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        max-width: 1500px;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #020617;
    }

    [data-testid="stSidebar"] * {
        color: #CBD5E1;
    }

    /* Headings */
    h1, h2, h3 {
        color: #F1F5F9 !important;
    }

    p, label, .stMarkdown {
        color: #CBD5E1;
    }

    /* Text input */
    div[data-baseweb="input"] {
        background-color: #1E293B;
        border: 1px solid #334155;
        border-radius: 8px;
    }

    div[data-baseweb="input"] input {
        color: #E2E8F0;
        background-color: transparent;
    }

    div[data-baseweb="input"] input::placeholder {
        color: #94A3B8;
    }

    /* Example question buttons */
    .stButton > button {
        background-color: #1E293B;
        color: #E2E8F0;
        border: 1px solid #334155;
        border-radius: 8px;
        transition: background-color 0.2s ease;
    }

    .stButton > button:hover {
        background-color: #334155;
        color: #FFFFFF;
        border-color: #60A5FA;
    }

    /* Primary Ask button */
    .stButton > button[kind="primary"] {
        background-color: #2563EB;
        color: #FFFFFF;
        border: none;
    }

    .stButton > button[kind="primary"]:hover {
        background-color: #1D4ED8;
        color: #FFFFFF;
    }

    /* Answer and content containers */
    [data-testid="stAlert"] {
        background-color: #1E293B;
        border: 1px solid #334155;
        color: #E2E8F0;
    }

    /* Code blocks */
    [data-testid="stCode"] {
        background-color: #1E293B;
        border: 1px solid #334155;
        border-radius: 8px;
    }

    /* Dividers */
    hr {
        border-color: #334155;
    }

    /* Captions */
    [data-testid="stCaptionContainer"] {
        color: #94A3B8;
    }

    /* Spinner */
    [data-testid="stStatusWidget"] {
        background-color: #1E293B;
    }
</style>
""", unsafe_allow_html=True)


# ----------------------------------------------------------
# AGENT
# ----------------------------------------------------------

EXAMPLES = [
    "How many orders are currently in transit?",
    "Which hub has the highest number of late shipments?",
    "What counts as a late shipment?",
    "Has the late-delivery rate increased today compared with yesterday?",
    "Where is shipment 865 right now?",
    "What is the average delivery cost per order?",
]


async def ask_agent(question):
    # A fresh agent per question.
    async with Agent("both") as agent:
        return await agent.ask(question)


# ----------------------------------------------------------
# SESSION STATE
# ----------------------------------------------------------

if "history" not in st.session_state:
    st.session_state.history = {}

if "question" not in st.session_state:
    st.session_state.question = ""


# ----------------------------------------------------------
# HEADER
# ----------------------------------------------------------

st.title("Ask LogiStream")

st.caption(
    "Ask questions about your logistics operations. "
    "The AI agent uses business definitions through RAG "
    "and live data tools through MCP."
)

st.divider()


# ----------------------------------------------------------
# EXAMPLE QUESTIONS
# ----------------------------------------------------------

st.subheader("Try asking")

cols = st.columns(3)

for i, example in enumerate(EXAMPLES):

    if cols[i % 3].button(
        example,
        key=f"ex{i}",
        use_container_width=True,
    ):
        st.session_state.question = example


# ----------------------------------------------------------
# QUESTION INPUT
# ----------------------------------------------------------

st.divider()

st.subheader("Your question")

question = st.text_input(
    "Ask about shipments, orders, hubs or incidents",
    key="question",
    placeholder="e.g. Which hub has the highest number of late shipments?",
)

ask_clicked = st.button(
    "Ask LogiStream →",
    type="primary",
    use_container_width=True,
)


# ----------------------------------------------------------
# RESPONSE
# ----------------------------------------------------------

if ask_clicked and question.strip():

    q = question.strip()

    if q not in st.session_state.history:

        with st.spinner(
            "Retrieving definitions and querying logistics data..."
        ):
            st.session_state.history[q] = asyncio.run(
                ask_agent(q)
            )

    r = st.session_state.history[q]

    st.divider()

    if r["error"]:

        st.error("The agent could not answer.")

        st.code(r["error"])

        if "429" in r["error"] or "rate" in r["error"].lower():
            st.info(
                "This looks like a Groq rate limit. "
                "Wait a minute and try again."
            )

    else:

        st.subheader("Answer")

        st.markdown(r["answer"])

    st.divider()

    # ------------------------------------------------------
    # AGENT TRANSPARENCY
    # ------------------------------------------------------

    left, right = st.columns(2)

    with left:

        st.subheader("Tools called")

        if r["tool_calls"]:

            for c in r["tool_calls"]:

                note = " (repaired)" if c.get("repaired") else ""

                st.code(
                    f"{c['name']}({c['arguments']})"
                    f"{note}  ok={c['ok']}",
                    language="text",
                )

        else:

            st.caption(
                "No tools were called. The answer came from "
                "the retrieved definitions or the model."
            )

    with right:

        st.subheader("Definitions retrieved")

        if r["rag_sections"]:

            for section in r["rag_sections"]:
                st.markdown(
                    f"- {section.split(' > ')[-1]}"
                )

        else:

            st.caption("No RAG sections retrieved.")

    # ------------------------------------------------------
    # EXECUTION DETAILS
    # ------------------------------------------------------

    st.divider()

    st.subheader("Execution details")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Response time", f"{r['seconds']} s")
    c2.metric("Model calls", r["steps"])
    c3.metric("Tokens read", f"{r['prompt_tokens']:,}")
    c4.metric("Tokens generated", f"{r['gen_tokens']:,}")

    st.caption(
        f"Model: {r['model']} · "
        f"Tool-call repairs: {r.get('recoveries', 0)}"
    )

elif ask_clicked:

    st.warning("Please enter a question first.")


# ----------------------------------------------------------
# FOOTER
# ----------------------------------------------------------

st.divider()

st.caption(
    "LogiStream AI · Groq + RAG + MCP · "
    "Ollama embeddings"
)

