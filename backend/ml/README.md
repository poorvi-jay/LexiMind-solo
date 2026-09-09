# M3 — word difficulty classifier

Home of the F33 training pipeline and the trained artifact that
`services/classifier_service.py` loads at startup.

    train_classifier.py     one-time training script, never imported at runtime
    classifier.joblib       trained model, loaded once at process start
    data/mrc_data.csv       MRC Psycholinguistic Database, training input only

`data/` is gitignored — the CSV is ~4 MB of third-party data that anyone can
re-download, and it is read only by the training script. `classifier.joblib` IS
committed: the running API needs it, and regenerating it requires the CSV.

Cite Coltheart, M. (1981), *The MRC Psycholinguistic Database*, University of
Sussex, in the project paper. Free for academic use.

---

## Decisions taken before starting (M3 phase 0)

The M3 build guide was written against the four-person team repo. This solo repo
diverges in ways that would have broken its instructions if followed literally,
so each difference is settled here.

### 1. There is no `models_temp.py`

The guide repeatedly says to add tables to `backend/models_temp.py` on its shared
`Base`. This repo split that concern in two from the start:

  - `backend/database.py`  — engine, `Base`, `SessionLocal`, `get_db`, `init_db`
  - `backend/models.py`    — the ORM classes

M3's three new tables (`reading_sessions`, `word_repeat_log`, `word_bank`) go
into `models.py` on the same `Base` imported from `database.py`. The guide's
underlying rule still holds and is the part that matters: one `Base`, one
`metadata`, one `create_all` — do not introduce a second models module.

### 2. `get_current_user` lives in `dependencies.py`, not `routers/auth.py`

    from backend.dependencies import get_current_user   # correct here
    from backend.routers.auth import get_current_user   # the guide's import; wrong here

It was deliberately moved out of the auth router so the M1 routers could depend
on it without importing the auth router back through `main.py`. Every M3
endpoint uses the `dependencies.py` one. Do not define a second.

### 3. The database is synchronous

The guide's section 11 pseudocode is async SQLAlchemy:

    due = await db.execute(select(WordBank).where(...))
    due_list = due.scalars().all()

This repo uses a synchronous `Session` from `sessionmaker`. Every M3 query is
written sync (`db.query(...)` / `db.execute(...)` without `await`), matching
`routers/writing.py` and `routers/auth.py`. Route handlers may still be
`async def` — that is what the existing routers do — but the DB calls inside
them are not awaited.

### 4. New columns need `_ADDED_COLUMNS`; new tables do not

`init_db()` calls `create_all()`, which creates missing *tables* but never
missing *columns* on tables that already exist. M3's three tables are entirely
new, so `create_all()` handles them against an existing `leximind.db`. But if
any M3 work later adds a column to `users`, `saved_documents` or
`writing_sessions`, it must also be registered in `_ADDED_COLUMNS` in
`database.py` or existing databases will raise "no such column" at query time.

### 5. `/classify` currently has no authentication — M3 adds it

`routers/classify.py` today has no `Depends(get_current_user)` at all; it is the
only endpoint in the backend still unauthenticated. The guide requires auth on
every M3 endpoint, so phase 2 adds it.

Verified safe before committing to this: `frontend/src/utils/api.js` attaches
`Authorization: Bearer <token>` to every request, and `/reading` is behind
`RequireAuth` in `App.jsx`, so the sole caller is always authenticated. No
frontend change is needed — but the endpoint will start returning 401 to
anonymous callers, which is the intended behaviour change, not a regression.

### 6. The classifier has a second consumer — the complexity badge

The guide frames F33 as only feeding `/classify`. In this repo
`classifier_service` has two consumers:

    routers/classify.py            -> per-word labels (F34)
    services/simplification_service -> hard_word_pct() -> /reading/complexity
                                       and /reading/simplify (F42/F43)

`classifier_service.py` was written as the single source of truth precisely so
these two could not disagree. Replacing the heuristic with the trained model
therefore also changes the hard-word percentages and complexity badge shown on
the Reading page. That is correct and desirable — a better classifier should
improve both — but it means:

  - `hard_word_pct()` must keep working and keep sharing the model; do not
    fork a separate code path for it.
  - The complexity badge's numbers will shift after phase 2. Expected, not a bug.
  - `hard_word_pct()` classifies every word of a passage, so per-word inference
    has to stay cheap enough for whole-document calls, not just the 200-word
    AC-27 budget.

### 7. Feature extraction must strip punctuation; the placeholder did not have to

`hard_word_pct()` splits on whitespace and passes raw tokens straight in, so
`extract_features` will receive `"encyclopedia,"` and `'"The'`, not clean words.
Measured on this machine:

    word_frequency('encyclopedia,', 'en') == word_frequency('encyclopedia', 'en')

`wordfreq` normalises punctuation internally, which is why the frequency-only
placeholder got away with never cleaning its input. The trained model will not:
`len("encyclopedia,")` is 13 rather than 12, and `SyllableTokenizer` on `"cat."`
is not the same as on `"cat"`. Two of the three features are corrupted by
punctuation that the old one silently absorbed.

`extract_features()` therefore strips non-alphabetic characters and lowercases
before computing anything. This is an addition to the guide's literal function
body, not a substitution of its feature set — the features remain exactly
`[syllables, frequency, length]` in that order.

### 8. `SyllableTokenizer` does not need `cmudict`

The guide's setup step says to run `nltk.download('cmudict')` for syllable
counts. It is not required: `nltk.tokenize.SyllableTokenizer` implements the
Sonority Sequencing Principle and uses no corpus at all: its module,
`nltk.tokenize.sonority_sequencing`, contains no reference to cmudict, and
`SyllableTokenizer().tokenize('encyclopedia')` returns 5 syllables in this venv.

cmudict happens to already be downloaded here, because `routers/reading.py` uses
it for its own separate syllable counting — so this venv cannot itself prove the
negative. The source inspection does: nothing in the SSP tokenizer's code path
loads a corpus, so a fresh clone without cmudict will train and serve fine.
`routers/reading.py`'s dependency on it is unaffected either way.

### 9. pandas is not used — Smart App Control blocks it

The guide's step 1 is "load the MRC CSV with pandas." Windows Smart App Control
on this machine blocks `pandas/_libs/reshape*.pyd`:

    ImportError: DLL load failed while importing reshape:
    An Application Control policy has blocked this file.

Same class of OS-level block as the spaCy `.pyd` worked around in
`nlp_service.py`. pandas was installed, confirmed broken, and uninstalled again.
`train_classifier.py` reads the CSV with the stdlib `csv` module and builds the
feature matrix as a numpy array. scikit-learn, joblib, numpy and scipy all
import and run fine — train, predict and a joblib save/load round-trip were all
verified in this venv.

### 10. recharts is already installed

`npm install recharts` (guide step 1.3) is unnecessary — `package.json` already
carries `recharts@^3.8.1`. Note it is v3; the guide predates it, so check the v3
API when writing the charts in phase 4.
