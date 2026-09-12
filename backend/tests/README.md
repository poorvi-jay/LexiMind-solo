# M3 test suite

The checks behind the M3 acceptance criteria, kept here so they can be re-run
rather than trusted.

## Running them

    backend/venv/Scripts/python.exe -m pytest backend/tests -v

Unit tests need nothing running. Integration tests need the backend serving,
and **skip** (not fail) when it is not, so a fresh clone still runs green.

    # start the backend first — see .claude/launch.json
    set LEXIMIND_API=http://127.0.0.1:8001    # only if it is not on port 8000
    backend/venv/Scripts/python.exe -m pytest backend/tests -v

`pytest` is a test-only dependency; everything else the suite needs is already
in `backend/requirements.txt`.

## What is here

| File | Needs a server | Covers |
|---|---|---|
| `test_sm2.py` | no | F50 — SM-2 state transitions against the PRD formula, mastery thresholds |
| `test_classifier.py` | no | F33/F34 — feature extraction, the trained model's labels, AC-27 latency |
| `test_spacy_refusals.py` | no | the Smart App Control loader: simulated refusals of each spaCy extension |
| `test_sessions.py` | yes | F37/F38 — session logging, the 30-second rule, replay counts |
| `test_analytics.py` | yes | F39-F41 — summary maths, last-20 ordering, empty state, cross-user isolation |
| `test_wordbank.py` | yes | F49/F50 — promotion at three replays, the drill queue and grading |
| `test_wordbank_stats.py` | yes | F51 — stats, the practice streak, drill-due across a date boundary |

## Two things worth knowing

**The `api` fixture checks it is talking to this project.** Another LexiMind
build (the four-person team repo) also serves on port 8000, with different
routes and a smaller `/health` payload. Tests skip with an explanation rather
than producing confusing failures against the wrong backend.

**Integration tests write to the configured database.** They register a
throwaway account per test, so counts start at zero and no test can see
another's rows — but point them at a development database, never a real one.
