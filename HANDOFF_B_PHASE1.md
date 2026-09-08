\# HANDOFF\_B\_PHASE1.md



Track: B

Phase completed: 1



\## Done



\* `track-b-agent/graph.py` — LangGraph state machine with all 8 nodes wired:

&#x20; `intake → plan → tool\_selection → permission\_check → tool\_execution →

&#x20; reflection → approval\_wait → final\_response`, plus `build\_graph()` and

&#x20; `run\_task()` entrypoints

\* `track-b-agent/planner.py` — LLM planning node (uses `ANTHROPIC\_API\_KEY`

&#x20; if set, one retry on JSON parse failure) with a deterministic rule-based

&#x20; offline fallback used when no key is present, so tests/CI never need a

&#x20; key or network access

\* `track-b-agent/tools/calculator.py` — real implementation, safe AST-based

&#x20; arithmetic (no `eval()`)

\* `track-b-agent/tools/file\_reader.py` — real implementation, sandboxed to

&#x20; `track-b-agent/demo\_docs/`, blocks path traversal

\* `track-b-agent/registry\_client.py` — reads Track A's live `GET /tools` if

&#x20; `registry\_base\_url` is passed, else falls back to `contracts.STARTER\_TOOLS`

\* `track-b-agent/permission\_client.py` — `check\_permission\_stub()` mirrors

&#x20; your `POST /permissions/check` logic exactly (same response shape:

&#x20; `decision`, `tool\_name`, `user\_role`, `risk\_level`, `requires\_approval`,

&#x20; `reason`; same 3-check order: tool exists → required args present →

&#x20; role/risk-tier match) so swapping to `check\_permission\_live()` in Phase 2

&#x20; is a one-line change in `graph.py`, not a rewrite

\* `tests/test\_track\_b\_phase1.py` — 15 tests, all passing, fully offline

\* `tests/conftest.py` — adds repo root + both hyphenated track dirs to

&#x20; `sys.path` so pytest works from the repo root



\## Key thing to know: hyphenated directory names



`track-a-guardrail/` and `track-b-agent/` both have hyphens, so neither can

be imported as a dotted Python package (`import track-b-agent` is a syntax

error). Everything in `track-b-agent/` uses plain top-level imports

(`from graph import build\_graph`, not relative dotted imports) and assumes

`track-b-agent/` itself is on `sys.path`. `tests/conftest.py` handles that

automatically for pytest; if you run scripts directly, `cd track-b-agent`

first (same as how you run `uvicorn main:app` from inside

`track-a-guardrail/`).



\## Definition of done — confirmed



> "given a stubbed registry and a sample request ('what's 42 \* 17' or 'read

> me the onboarding doc'), the graph runs start to finish and produces a

> valid tool call and a final answer — no live permission service needed

> yet"



Verified both manually and in `test\_graph\_end\_to\_end\_calculator` /

`test\_graph\_end\_to\_end\_file\_reader`:

