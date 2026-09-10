"""
backend/ml/train_classifier.py
One-time training script for the F33 word-difficulty classifier.

Not imported at runtime. It reads a word-difficulty norm set, derives
Easy/Medium/Hard labels, trains a GradientBoostingClassifier on the PRD's three
features, evaluates it, and writes backend/ml/classifier.joblib.

    python backend/ml/train_classifier.py                  # AoA (default)
    python backend/ml/train_classifier.py --dataset mrc    # the PRD's dataset

Two datasets are supported:

  aoa  Kuperman, Stadthagen-Gonzalez & Brysbaert (2012) age-of-acquisition
       ratings, 30,102 usable words. Higher rating = learned later = harder.
       This is what ships. AoA is a better-motivated difficulty proxy than
       familiarity and covers 6x more words.

  mrc  MRC Psycholinguistic Database familiarity, the PRD's original choice.
       Retained so the comparison in README.md stays reproducible. Only 4,923
       of its 150,837 rows carry a familiarity rating.

ON THE ACCEPTANCE GATE
AC-26 as written requires >= 80% accuracy AND >= 85% Hard recall. The 80%
accuracy half is not reachable from [syllables, frequency, length] on any
dataset tested, and is actively misleading on a skewed class - a do-nothing
model that finds 1.5% of hard words scores 80.2%. See README.md for the full
evidence. AC-26 was therefore amended: Hard recall is gated, accuracy is
reported. This script fails only on Hard recall.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import classification_report, confusion_matrix, recall_score
from sklearn.model_selection import train_test_split

# Run as a script from the repo root; make `backend` importable either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.services.classifier_service import extract_features, normalize_word

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "classifier.joblib"

LABELS = ("Easy", "Medium", "Hard")

# Mirrors disagree on how an absent rating is written: the MRC codes it as a
# numeric 0, R-exported files write "NA". Both mean "not rated" and must be
# dropped rather than read as a real score.
UNRATED_TOKENS = {"", "na", "n/a", "nan", "null", "none", "."}

# AC-26, as amended. Accuracy is measured and printed but does not gate - see
# the module docstring and README.md.
MIN_HARD_RECALL = 0.85

DATASETS = {
    "aoa": {
        "csv": HERE / "data" / "aoa_data.csv",
        "word_columns": ("word",),
        "value_columns": ("rating.mean", "aoa", "rating_mean"),
        # Age of acquisition in years: a word learned later is harder.
        "higher_is_harder": True,
        # Split on schooling: known before starting school, learned during
        # primary years, learned after. These map onto reading level far more
        # naturally than percentile cuts, and unlike tertiles they do not
        # force a balanced split onto a genuinely skewed vocabulary.
        "easy_cut": 6.0,
        "hard_cut": 10.0,
        "units": "years",
        "citation": "Kuperman, Stadthagen-Gonzalez & Brysbaert (2012)",
    },
    "mrc": {
        "csv": HERE / "data" / "mrc_data.csv",
        "word_columns": ("word", "mrc.word", "wordname", "item"),
        "value_columns": ("fam", "mrc.fam", "familiarity", "fam_rating"),
        # Familiarity 0-700: a more familiar word is easier.
        "higher_is_harder": False,
        "easy_cut": 500.0,
        "hard_cut": 300.0,
        "units": "familiarity",
        "citation": "Coltheart, M. (1981), University of Sussex",
    },
}

# Short passages used as a final qualitative check. Class balance in these norm
# sets is over dictionary *types*, but running text is dominated by common
# words - so the only way to know what a reader actually sees highlighted is to
# label real prose. A model can look alarming by type and behave perfectly here.
SAMPLE_TEXTS = {
    "children's story": (
        "The little cat sat on the mat and looked at the big red ball. She was "
        "very happy to play with her friend in the garden after school."
    ),
    "news prose": (
        "The government announced significant reforms to the education system "
        "yesterday, following months of consultation with teachers, parents "
        "and local authorities across the country."
    ),
    "academic": (
        "The encyclopedia entry describes semiconductor fabrication as a "
        "photolithographic process requiring extraordinarily precise "
        "environmental controls and specialised equipment."
    ),
}


def make_labeller(spec: dict, easy_cut: float, hard_cut: float):
    """Build a value -> Easy/Medium/Hard function for the dataset's direction."""
    if spec["higher_is_harder"]:
        def label_for(value: float) -> str:
            if value <= easy_cut:
                return "Easy"
            if value > hard_cut:
                return "Hard"
            return "Medium"
    else:
        def label_for(value: float) -> str:
            if value > easy_cut:
                return "Easy"
            if value < hard_cut:
                return "Hard"
            return "Medium"
    return label_for


def _pick_column(fieldnames: list[str], candidates: tuple[str, ...], kind: str) -> str:
    lookup = {name.strip().lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    raise SystemExit(
        f"Could not find the {kind} column. Looked for {candidates!r}; the file has "
        f"{fieldnames!r}. Pass the real name with --{kind}-column."
    )


def load_words(csv_path: Path, spec: dict, word_col: str | None, value_col: str | None):
    """Return {word: mean rating} for every usably-rated word."""
    if not csv_path.exists():
        raise SystemExit(
            f"Missing {csv_path}.\n"
            f"Download the dataset to that path first. Cite {spec['citation']}."
        )

    ratings: dict[str, list[float]] = defaultdict(list)
    skipped_unrated = skipped_unusable = rows = 0

    with csv_path.open(newline="", encoding="utf8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit(f"{csv_path} has no header row.")
        word_col = word_col or _pick_column(reader.fieldnames, spec["word_columns"], "word")
        value_col = value_col or _pick_column(reader.fieldnames, spec["value_columns"], "value")
        print(f"Using columns: word={word_col!r} value={value_col!r}")

        for row in reader:
            rows += 1
            word = normalize_word(row.get(word_col) or "")

            # Most rows in the MRC carry no rating at all. Reading those as real
            # values would mislabel tens of thousands of words and poison the
            # very class the acceptance criterion is strictest about.
            raw = (row.get(value_col) or "").strip()
            if raw.lower() in UNRATED_TOKENS:
                skipped_unrated += 1
                continue
            try:
                value = float(raw)
            except ValueError:
                skipped_unusable += 1
                continue
            if value <= 0:  # the MRC's numeric code for unrated
                skipped_unrated += 1
                continue

            # ASCII-only for training: the syllable tokenizer has no sonority
            # class for accented characters and warns per word. Deliberately not
            # enforced in normalize_word(), because at request time OCR can hand
            # us anything and serving must not fall over.
            letters = word.replace("-", "").replace("'", "")
            if not word or not letters.isalpha() or not word.isascii():
                skipped_unusable += 1
                continue

            # Norm sets carry one row per word per sense or part of speech, so
            # common words recur. Average, rather than letting a word's row
            # count weight the training set.
            ratings[word].append(value)

    print(
        f"Read {rows:,} rows -> {len(ratings):,} unique rated words "
        f"({skipped_unrated:,} unrated, {skipped_unusable:,} unusable)"
    )
    return {word: sum(values) / len(values) for word, values in ratings.items()}


def build_dataset(ratings: dict[str, float], label_for):
    words = sorted(ratings)
    X = np.array([extract_features(word) for word in words], dtype=float)
    y = np.array([label_for(ratings[word]) for word in words])
    return words, X, y


def report_prose(model) -> None:
    """Label real passages, because type-level class balance misleads."""
    print("\nHard words found in running text:")
    for name, text in SAMPLE_TEXTS.items():
        tokens = [t for t in text.split() if normalize_word(t)]
        labels = model.predict(np.array([extract_features(t) for t in tokens]))
        hard = [t.strip(".,") for t, label in zip(tokens, labels) if label == "Hard"]
        share = 100 * len(hard) / len(tokens)
        print(f"  {name:<18} {share:>3.0f}% of tokens  {' '.join(hard) or '(none)'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(DATASETS), default="aoa")
    parser.add_argument("--csv", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--word-column", default=None)
    parser.add_argument("--value-column", default=None)
    parser.add_argument("--easy-cut", type=float, default=None)
    parser.add_argument("--hard-cut", type=float, default=None)
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--balance",
        action="store_true",
        help="Weight classes inversely to their frequency. Off by default: on "
        "the shipped AoA split it lowers Hard recall rather than raising it.",
    )
    args = parser.parse_args()

    spec = DATASETS[args.dataset]
    csv_path = args.csv or spec["csv"]
    easy_cut = spec["easy_cut"] if args.easy_cut is None else args.easy_cut
    hard_cut = spec["hard_cut"] if args.hard_cut is None else args.hard_cut
    label_for = make_labeller(spec, easy_cut, hard_cut)

    print(f"Dataset: {args.dataset} ({spec['citation']})")
    direction = "higher = harder" if spec["higher_is_harder"] else "higher = easier"
    print(f"Cuts: easy <= {easy_cut} {spec['units']}, hard > {hard_cut} ({direction})")

    ratings = load_words(csv_path, spec, args.word_column, args.value_column)
    if not ratings:
        raise SystemExit("No usable rows - check the column names and the file.")

    words, X, y = build_dataset(ratings, label_for)
    distribution = Counter(y)
    print("Class distribution:", {label: distribution.get(label, 0) for label in LABELS})
    missing = [label for label in LABELS if not distribution.get(label)]
    if missing:
        raise SystemExit(f"No examples for {missing} - cannot train all three classes.")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=args.test_size, random_state=args.seed, stratify=y
    )
    print(f"Train {len(X_train):,} / test {len(X_test):,} (stratified)")

    sample_weight = None
    if args.balance:
        weights = {
            label: len(y_train) / (3 * count)
            for label, count in Counter(y_train).items()
        }
        sample_weight = np.array([weights[label] for label in y_train])
        # str() because the keys are numpy scalars and repr as np.str_('Easy').
        print("Class weights:", {str(k): round(v, 3) for k, v in weights.items()})

    model = GradientBoostingClassifier(random_state=args.seed)
    model.fit(X_train, y_train, sample_weight=sample_weight)

    # Evaluated only on the held-out split - never on data the model trained on.
    y_pred = model.predict(X_test)
    accuracy = float((y_pred == y_test).mean())
    hard_recall = float(
        recall_score(y_test, y_pred, labels=["Hard"], average="macro", zero_division=0)
    )
    # The score a model gets for always guessing the largest class. Printed
    # beside accuracy because accuracy alone is meaningless on a skewed split.
    baseline = max(Counter(y_test).values()) / len(y_test)

    print("\n" + classification_report(y_test, y_pred, digits=3, zero_division=0))
    print("Confusion matrix (rows = true, cols = predicted)")
    order = [label for label in LABELS if label in set(y_test) | set(y_pred)]
    matrix = confusion_matrix(y_test, y_pred, labels=order)
    print("           " + "".join(f"{label:>9}" for label in order))
    for label, row in zip(order, matrix):
        print(f"{label:>10} " + "".join(f"{value:>9,}" for value in row))

    print("\nAC-26 (amended - see README.md)")
    verdict = "PASS" if hard_recall >= MIN_HARD_RECALL else "FAIL"
    print(f"  Hard recall      {hard_recall:.1%}  (gated, needs >= {MIN_HARD_RECALL:.0%})  {verdict}")
    print(f"  overall accuracy {accuracy:.1%}  (reported; majority baseline {baseline:.1%})")

    print("\nSanity check (the placeholder mislabelled the first three):")
    for word in ("difficult", "encyclopedia", "semiconductor", "cat", "the"):
        features = np.array([extract_features(word)])
        label = model.predict(features)[0]
        confidence = float(model.predict_proba(features).max())
        print(f"  {word:<15} {label:<7} {confidence:.2f}")

    report_prose(model)

    if hard_recall < MIN_HARD_RECALL:
        print("\nHard recall below the gate - model NOT saved.")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.out)
    print(f"\nSaved {args.out} ({args.out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
