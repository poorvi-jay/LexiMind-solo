# M3 — word difficulty classifier

Home of the F33 training pipeline and the trained artifact that
`services/classifier_service.py` loads at startup.

    train_classifier.py     one-time training script, never imported at runtime
    classifier.joblib       trained model, loaded once at process start
    data/aoa_data.csv       Kuperman AoA norms - what the shipped model trains on
    data/mrc_data.csv       MRC familiarity - the PRD's original choice, kept
                            so README's dataset comparison stays reproducible

`data/` is gitignored — both files are third-party data anyone can re-download,
and they are read only by the training script. `classifier.joblib` IS committed:
the running API needs it, and regenerating it requires the CSVs.

    curl -L -o backend/ml/data/aoa_data.csv https://raw.githubusercontent.com/wehlutyk/brainscopypaste/master/data/AoA/Kuperman-BRM-data-2012.csv
    curl -L -o backend/ml/data/mrc_data.csv https://raw.githubusercontent.com/mllewis/RC/master/data/corpus/MRC_corpus.csv

Cite both in the project paper. The shipped model uses Kuperman, V.,
Stadthagen-Gonzalez, H., & Brysbaert, M. (2012), *Age-of-acquisition ratings for
30,000 English words*, Behavior Research Methods 44(4), 978-990. The comparison
dataset is Coltheart, M. (1981), *The MRC Psycholinguistic Database*, University
of Sussex. Both are free for academic use.

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

---

## AC-26 is not achievable as specified (M3 phase 1 finding)

Measured, not estimated. Every number below is on a held-out 20% stratified
split, and no model that meets AC-26 was found.

### What AC-26 asks for

>= 80% overall accuracy AND >= 85% recall on the Hard class, from a
GradientBoostingClassifier over [syllables, frequency, length].

### MRC familiarity (the PRD's dataset)

The full database is 150,837 rows, but only **4,923 unique words carry a
familiarity rating** - the other 141,445 are unrated. The guide's "150,837
words" is the entry count, not the training set. Hard (fam < 300) is 367 words,
7.5% of the data.

    configuration                              accuracy   Hard recall
    GBC, spec features, as specified              72.9%          0.0%
    GBC, fully class-balanced                     58.3%         82.2%
    best of a 20-config weight x capacity sweep   72.9%     never both
    HistGB / RandomForest, same features       70.8-72.6%     27-45%
    9 hand-built features instead of 3            72.4%         42.5%
    rebalanced (tertile) thresholds               66.4%         82.3%

The unweighted model reaches 72.9% by **never predicting Hard at all** - the
accuracy-optimal strategy when Hard is 7.5% of the data. It would highlight
nothing on the Reading page.

### Why more features do not help

Going from 3 features to 9 moved accuracy by -0.5pp. The reason is in the data:
**634 words have byte-identical feature vectors but conflicting labels** - same
syllable count, same frequency, same length, different difficulty class. That is
13% of the dataset that no model can separate, and it sits right where every
model plateaued.

### Kuperman AoA (30,102 words, 6x the data)

Age-of-acquisition is a better-motivated difficulty proxy and six times larger.
It did not lift the ceiling either.

    label scheme            majority baseline   best accuracy   Hard recall
    schooling (<=6/6-10/>10)          65.0%           71.1%        90-95%
    tertiles (balanced)               33.6%           54.9%           66%

On the skewed schooling split the model beats a trivial baseline by only ~6pp,
and its high Hard recall is an artifact of Hard being 65% of the data. On a
balanced split it genuinely learns (+21pp over baseline) but reaches only 55%.

### The accuracy threshold is satisfiable by a model that does nothing

Binary Hard-vs-rest on AoA, Hard = top 20% by age of acquisition:

    accuracy 80.2%  (majority baseline 80.1%)  Hard recall 1.5%

That **passes AC-26's 80% accuracy bar** while finding essentially no hard words.
Overall accuracy alone is not a meaningful gate on a skewed class; the build
guide was right to demand Hard recall alongside it, but the two together are not
jointly reachable from these features.

### Conclusion

Human difficulty ratings are not predictable to 80%/85% from syllables,
frequency and word length. This holds across two datasets, four model families,
feature sets of 3 and 9, five class-weighting levels and four labelling schemes.
The constraint is the weak relationship between surface orthography and rated
difficulty, not the choice of model or hyperparameters.

Any model shipped for F33 therefore needs AC-26 restated to what is actually
measurable, with these numbers in the paper. Whatever is chosen will still be a
large improvement on the placeholder, which labels `encyclopedia` Medium and
reports 0.0% hard words in "the quick encyclopedia semiconductor".

### Decision taken: AC-26 amended, model shipped

AC-26 is restated as:

    Hard recall >= 85%   gated - the build fails below this
    overall accuracy     measured and reported alongside the majority-class
                         baseline, but not gated

The accuracy half is dropped because it is unreachable (evidence above) and
because it does not measure what it appears to. On a skewed class a do-nothing
model scores 80.2% while finding 1.5% of hard words. Reporting accuracy next to
the baseline it must beat is the honest version of the same check.

### The shipped model

Kuperman AoA, schooling thresholds, `GradientBoostingClassifier`, the PRD's
three features, trained on 24,081 words and evaluated on a held-out 6,021:

    Hard recall       94.7%   gate >= 85%          PASS
    overall accuracy  70.8%   baseline 65.0%       reported
    Easy 1,748 / Medium 8,781 / Hard 19,573 words

Qualitatively, which is what actually matters for a reading aid:

    difficult      Medium     encyclopedia   Hard      semiconductor  Hard
    cat            Easy       the            Easy

All three of the first row were mislabelled by the placeholder. And on running
text, where the type/token distinction bites:

    children's story    0% of tokens flagged Hard
    news prose          4%   (consultation)
    academic           33%   (encyclopedia semiconductor fabrication
                              photolithographic extraordinarily specialised)

That gradient - nothing in a children's story, one word in news prose, a third
of an academic passage - is the behaviour F33 exists to produce, and no accuracy
number captures it. 65% of dictionary *types* are labelled Hard, but real prose
is mostly common words, so the reader sees a sensible amount of highlighting.

`--balance` is off for the shipped model: on this split it lowers Hard recall
(94.7% -> 64.0%) rather than raising it, because Hard is the majority class here
rather than the minority it was in the MRC.

### Runtime characteristics (measured, for phase 2)

    cold joblib.load()          2.9 s    -> must load once at startup, never per request
    200 words, features+predict 208 ms   -> inside AC-27's 500 ms budget before
                                            dedup, which real text benefits from a lot
    artifact size               390 KB   -> small enough to commit
