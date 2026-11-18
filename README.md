# LogiStream

An end-to-end data engineering and AI project for a simulated last-mile courier network in Delhi-NCR. Orders and shipments are generated in PostgreSQL, streamed through change data capture into a Delta Lake medallion lakehouse, served through DuckDB and an MCP server, and queried in natural language by an AI agent that uses retrieval-augmented generation (RAG) for business definitions. Results appear in a Streamlit dashboard.

Built as a final-year undergraduate project. Runs locally on Windows (16 GB RAM), with all project files on `D:\LogiStream`.

---

## Architecture

```
Simulated courier company (simulator/)
        │
        ▼
PostgreSQL 16 (wal_level=logical, schema in sql/schema.sql)
        │  Debezium CDC (Kafka Connect, REST :8083, connectors/logistics-connector.json)
        ▼
Redpanda (Kafka-compatible)
        │
        ▼
Spark Structured Streaming (Docker, spark/)
        │
        ▼
Delta Lake  ──  bronze → silver → gold
        │
        ▼
DuckDB (read-only access to gold)
        │
        ├──► MCP server (tools over gold data)
        │          │
        │          ▼
        └──► RAG (Chroma + nomic-embed-text via Ollama)
                   │
                   ▼
        AI agent (Groq API, Qwen model)
                   │
                   ▼
        Streamlit dashboard (4 pages)
```

## Data layers

| Layer | Contents |
|---|---|
| **Bronze** | Append-only raw Debezium change events. Holds full history. Written by `spark/bronze_stream.py`. |
| **Silver** | Current-state tables. Latest version per record by `source.lsn`, soft deletes via `is_deleted`, and a quarantine table for bad records. Built by `spark/silver_batch.py`. |
| **Gold** | Rebuilt in full on each run, in `lake/gold/`: `fact_orders`, `fact_shipments`, `fact_incidents`, `agg_hub_daily`, `agg_daily_overview`, `meta`. Built by `spark/gold_batch.py`. |

### Business rules

The rules the agent relies on are written in `docs/gold_definitions.md`, which is also the RAG source. Key definitions:

- **"Now"** is `as_of`, the latest event time recorded in `meta`. Days use Indian time (Asia/Kolkata).
- **Delivered** means the shipment has an actual arrival time.
- **Late** means delivered, with arrival after the planned time.
- **On-time rate** is on-time deliveries divided by delivered shipments. Undelivered shipments are never counted as on time.
- **Currently delayed** means not delivered and planned arrival earlier than `as_of`.
- Late deliveries are attributed to the **destination** hub.
- Cancellation rate by city uses the **customer's** city. Delivery metrics by city use the **destination hub's** city.
- Hub processing time covers the **origin hub only**. Time spent at intermediate hubs cannot be measured.
- `scanned` events are ignored in gold.

## Frontend

Streamlit with a dark theme (configured in `.streamlit/config.toml`). Pages in `frontend/pages/`:

1. **Overview** (`dashboard.py`): network KPIs, daily delivery trends, hub comparison, a Delhi-NCR hub map, and incident counts by hub. Reads gold tables directly. No LLM.
2. **Shipments** (`shipments.py`): search by shipment ID, filter by status, route, and vehicle, and view origin, destination, planned and actual arrival, and delay.
3. **Analytics** (`Analytics.py`): delivery performance, vehicle performance, hub comparison, and incident analysis, with date and hub filters.
4. **Ask LogiStream** (`2_Ask.py`): natural-language questions answered by the agent. Shows the answer, the MCP tools used, the RAG sections retrieved, timing, and token usage.

Pages 1–3 read gold data through DuckDB. Only page 4 uses the agent.

## Requirements

- Windows with WSL 2 (for Docker)
- Docker Desktop
- Python 3.x with a virtual environment (`.venv/`)
- [Ollama](https://ollama.com), with the `nomic-embed-text` model pulled. Set the model location to `D:\` in Ollama's settings. Cloud features should be off.
- A Groq API key

## Setup

1. Clone the repository into `D:\LogiStream`.
2. Create and activate a virtual environment in `.venv/`, then install the Python dependencies. Pin `mcp<2`, because the MCP 2.x import path differs from the code. The server uses `FastMCP`.
3. Create a `.env` file in the project root:

   ```
   GROQ_API_KEY=your_key_here
   GROQ_MODEL=...            # see "Agent" below
   GROQ_TIMEOUT_SECONDS=...
   MAX_TOOL_CHARS=...
   ```

   `.env` must be listed in `.gitignore`. Do not commit it.

4. Start the Docker services defined in `docker-compose.yml` (Postgres, Redpanda, Kafka Connect/Debezium, Spark). Apply `sql/schema.sql` to Postgres. The Postgres data is a bind mount at `D:/logistream/data/pgdata`.
5. Register the Debezium connector from `connectors/logistics-connector.json`.
6. Build the lake (bronze, silver, gold) with the Spark scripts in `spark/`. The lake lives at `D:/logistream/lake`. Use `spark/smoke_test.py` and the `check_*.py` scripts to verify each layer.
7. Build the RAG index:

   ```
   python rag\rag_store.py build
   ```

## Running

Docker is needed only to build the lake. The dashboard and the agent read the gold files and need only Ollama (for embeddings) and the Groq key.

```
streamlit run frontend\pages\dashboard.py
```

Useful commands, run from `D:\LogiStream`:

```
python mcp\test_tools.py              # expect 27/27
python rag\rag_store.py build         # rebuild after editing gold_definitions.md
python rag\rag_store.py test
python eval\run_eval.py run --setups both --redo
python eval\run_eval.py report
python eval\show_wrong.py both
python test_groq.py                   # checks the Groq connection
```

## Agent

The agent uses **both** RAG and MCP (`Agent("both")`, in `agent/agent.py`). It was chosen after comparing four setups:

| Setup | Description |
|---|---|
| LLM only | Model answers without tools or retrieval |
| RAG only | Retrieved definitions, no data tools |
| MCP only | Data tools, no definitions |
| **Both** | **Final architecture** |

The LLM is hosted on Groq (Qwen). Embeddings run locally through Ollama. The earlier local-only runs are kept as evidence of why the cloud model was chosen.

Local `llama3.2:3b` was too slow on CPU (roughly 1–1.5 hours for a full run) and made unreliable tool calls.

Groq sometimes returns malformed tool calls (HTTP 400 `tool_use_failed`). The agent has a repair step that reads `failed_generation` and runs the intended tool. Repairs are counted separately from the raw score.

## Evaluation

The question set has 36 questions in `eval/ground_truth.py`. The answer key is computed directly from Postgres and saved to `eval/ground_truth.json`. Categories: delivery, hub, orders, city, vehicle, incident, trend, mixed, definition, unanswerable, and partial.

Scoring (`eval/run_eval.py`):

- Numeric answers: exact match for whole numbers, within 1% for decimals.
- Text, definition, unanswerable, and mixed questions: keyword checks, marked `needs_review`, so a person reads them.

### Results

| Run | Result |
|---|---|
| Local sample, llama3.2:3b (11 questions × 4 setups) | LLM 1/11, RAG 2/11, MCP 5/11, Both 6/11 |
| Groq full run, both setups (36 questions) | 20/36 correct. 9 failed with API tool-call errors and were counted as wrong. Of the 27 that answered, 20 were correct (~74%). |
| Groq sample, both setups (11 questions) | 11/11, average 48 s. Small sample. |

The 36-question result is the headline figure. Both raw scores and repair counts are reported.

## Key numbers (snapshot at `as_of` 2026-11-15 03:53 UTC)

- Orders: 2,564 (1,520 delivered, 612 packed, 170 in transit, 152 cancelled, 110 created)
- Currently delayed shipments: 51
- Incident types: flat tyre 102, road closed 84, breakdown 82
- Top late hubs: Rohini Depot 141, Greater Noida Depot 140, Mayur Vihar Depot 133

## Data caveats

- The simulator was restarted several times, so delay figures are slightly inflated. The simulator is stopped; do not restart it, or the answer key goes out of date.
- Bronze is a snapshot of changes, not full change history for every record.
- About 890 orders remained open at the end of the run, mostly waiting for vehicles.
- The late rate of about 65% is by design: trips are planned at median duration, and incidents add delay.
- Area names overlap ("Delhi" and "New Delhi", "Noida" and "Greater Noida"). This is reported as a data-quality finding and not merged.
- Agent tool arguments were made more forgiving after the local sample run. This is disclosed in the evaluation.

## Project structure

```
LogiStream/
├── .venv/                       # Python virtual environment (not committed)
├── agent/
│   └── agent.py                 # Groq-backed agent (RAG + MCP)
├── connectors/
│   └── logistics-connector.json # Debezium connector config
├── data/                        # local data files
├── docs/
│   └── gold_definitions.md      # business rules, RAG source document
├── eval/
│   ├── ground_truth.py          # question set and answer key from Postgres
│   ├── ground_truth.json
│   ├── run_eval.py              # runner and scorer
│   ├── show_wrong.py            # inspect failed answers
│   ├── results.jsonl
│   └── results.csv
├── frontend/
│   └── pages/
│       ├── dashboard.py         # Overview
│       ├── shipments.py         # Shipment explorer
│       ├── Analytics.py         # Analytics
│       └── 2_Ask.py             # Ask LogiStream (agent)
├── lake/                        # bronze, silver, gold Delta tables
├── mcp/
│   ├── gold_db.py               # read-only DuckDB access over gold (blocks DROP etc.)
│   ├── server.py                # MCP server, 14+ tools
│   └── test_tools.py            # tool tests against the Postgres answer key
├── models/                      # local model files (not committed)
├── rag/
│   ├── chroma_db/               # Chroma index (not committed)
│   └── rag_store.py             # chunks by heading, Chroma, nomic-embed-text, TOP_K=5
├── simulator/
│   ├── config.yaml              # simulator settings
│   ├── seed_reference.py        # seeds hubs, vehicles and other reference data
│   └── simulator.py             # generates orders, shipments and incidents
├── spark/
│   ├── Dockerfile
│   ├── bronze_stream.py         # raw CDC events → bronze
│   ├── silver_batch.py          # bronze → silver
│   ├── gold_batch.py            # silver → gold
│   ├── check_bronze.py
│   ├── check_silver.py
│   ├── check_gold.py            # gold vs Postgres checks
│   └── smoke_test.py
├── sql/
│   └── schema.sql               # Postgres schema
├── .streamlit/config.toml       # dark theme
├── .env                         # secrets, not committed
├── .gitignore
├── docker-compose.yml
└── test_groq.py                 # Groq connection check
```

## Technology

PostgreSQL 16 · Debezium · Redpanda · Apache Spark (Structured Streaming) · Delta Lake · DuckDB · MCP · ChromaDB · Ollama (nomic-embed-text) · Groq (Qwen) · Streamlit · Plotly · Docker / WSL 2

## Known limitations

- Intermediate-hub dwell time cannot be measured, because the source has no arrival event for those hubs.
- Free-tier Groq rate limits (tokens per day and per minute) can interrupt runs. The prompt was trimmed to reduce token use.
- Keyword-based scoring for definition and unanswerable questions needs human review.
- Small evaluation samples. The full 36-question run is the reference result.
