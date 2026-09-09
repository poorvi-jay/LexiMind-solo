"""
backend/ml/train_classifier.py
One-time training script for the F33 word-difficulty classifier.

Not imported at runtime. It reads the MRC Psycholinguistic Database, derives
Easy/Medium/Hard labels from each word's familiarity rating, trains a
GradientBoostingClassifier on the PRD's three features, evaluates it against
AC-26's two thresholds, and writes backend/ml/classifier.joblib.

    python backend/ml/train_classifier.py
    python backend/ml/train_classifier.py --balance     # if Hard recall is short

AC-26 requires BOTH >= 80% overall accuracy AND >= 85% recall on the Hard class.
The script exits non-zero if either is missed, so a failing model cannot be
mistaken for a passing one just because it wrote a file.

pandas is deliberately not used - Smart App Control blocks one of its binaries
on this machine (see README.md, decision 9). The stdlib csv module is entirely
sufficient for one flat file read once.
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
DEFAULT_CSV = HERE / "data" / "mrc_data.csv"
DEFAULT_OUT = HERE / "classifier.joblib"

# The MRC ships under several mirrors with slightly different headers, so the
# columns are detected rather than hardcoded (README decision, and the build
# guide's own warning). Matching is case-insensitive.
WORD_COLUMNS = ("word", "mrc.word", "wordname", "w", "item")
FAMILIARITY_COLUMNS = (
    "fam",
    "mrc.fam",  # the mllewis/RC MRC_corpus.csv mirror uses dotted names
    "familiarity",
    "fam_rating",
    "familiarity_rating",
)

# Mirrors disagree on how an absent rating is written. The original MRC codes it
# as a numeric 0; R-exported mirrors write "NA". Both mean "not rated" and must
# be dropped, not read as a low familiarity score.
UNRATED_TOKENS = {"", "na", "n/a", "nan", "null", "none", "."}

LABELS = ("Easy", "Medium", "Hard")


def label_for(familiarity: float) -> str:
    """PRD thresholds: > 500 Easy, 300-500 Medium, < 300 Hard."""
    if familiarity > 500:
        return "Easy"
    if familiarity >= 300:
        return "Medium"
    return "Hard"


def _pick_column(fieldnames: list[str], candidates: tuple[str, ...], kind: str) -> str:
    lookup = {name.strip().lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in lookup:
            return lookup[candidate]
    raise SystemExit(
        f"Could not find the {kind} column. Looked for {candidates!r}; the file has "
        f"{fieldnames!r}. Pass the real name with --{kind}-column."
    )


def load_words(csv_path: Path, word_col: str | None, fam_col: str | None):
    """Return {word: mean familiarity} for every usably-rated word."""
    if not csv_path.exists():
        raise SystemExit(
            f"Missing {csv_path}.\n"
            "Download the MRC Psycholinguistic Database CSV to that path first.\n"
            "Cite Coltheart, M. (1981), University of Sussex - free for academic use."
        )

    ratings: dict[str, list[float]] = defaultdict(list)
    skipped_unrated = skipped_unusable = rows = 0

    with csv_path.open(newline="", encoding="utf8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit(f"{csv_path} has no header row.")
        word_col = word_col or _pick_column(reader.fieldnames, WORD_COLUMNS, "word")
        fam_col = fam_col or _pick_column(
            reader.fieldnames, FAMILIARITY_COLUMNS, "familiarity"
        )
        print(f"Using columns: word={word_col!r} familiarity={fam_col!r}")

        for row in reader:
            rows += 1
            word = normalize_word(row.get(word_col) or "")

            # Most rows in the full database carry no familiarity rating at all.
            # Reading those as real values would label tens of thousands of
            # words "Hard" and poison the very class AC-26 is strictest about.
            raw_familiarity = (row.get(fam_col) or "").strip()
            if raw_familiarity.lower() in UNRATED_TOKENS:
                skipped_unrated += 1
                continue
            try:
                familiarity = float(raw_familiarity)
            except ValueError:
                skipped_unusable += 1
                continue
            if familiarity <= 0:  # the original MRC's numeric code for unrated
                skipped_unrated += 1
                continue
            # ASCII-only for training: the syllable tokenizer has no sonority
            # class for accented or non-Latin characters and warns per word.
            # The MRC itself is ASCII, so this drops nothing real - and it is
            # deliberately not enforced in normalize_word(), because at request
            # time OCR can hand us anything and serving must not fall over.
            letters = word.replace("-", "").replace("'", "")
            if not letters.isalpha() or not word.isascii():
                skipped_unusable += 1
                continue

            # The MRC has one row per word per part of speech, so common words
            # appear several times. Average rather than letting a word's
            # row count weight the training set.
            ratings[word].append(familiarity)

    print(
        f"Read {rows:,} rows -> {len(ratings):,} unique rated words "
        f"({skipped_unrated:,} unrated, {skipped_unusable:,} unusable)"
    )
    return {word: sum(values) / len(values) for word, values in ratings.items()}


def build_dataset(ratings: dict[str, float]):
    words = sorted(ratings)
    X = np.array([extract_features(word) for word in words], dtype=float)
    y = np.array([label_for(ratings[word]) for word in words])
    return words, X, y


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--word-column", default=None)
    parser.add_argument("--familiarity-column", default=None)
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--balance",
        action="store_true",
        help="Weight classes inversely to their frequency. The PRD does not ask "
        "for this, so it is off by default - but AC-26 grades Hard recall "
        "separately from accuracy, and a rare Hard class is the likeliest "
        "reason to miss it.",
    )
    args = parser.parse_args()

    ratings = load_words(args.csv, args.word_column, args.familiarity_column)
    if not ratings:
        raise SystemExit("No usable rows - check the column names and the file.")

    words, X, y = build_dataset(ratings)
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

    print("\n" + classification_report(y_test, y_pred, digits=3, zero_division=0))
    print("Confusion matrix (rows = true, cols = predicted)")
    order = [label for label in LABELS if label in set(y_test) | set(y_pred)]
    matrix = confusion_matrix(y_test, y_pred, labels=order)
    print("           " + "".join(f"{label:>9}" for label in order))
    for label, row in zip(order, matrix):
        print(f"{label:>10} " + "".join(f"{value:>9,}" for value in row))

    print("\nAC-26")
    verdict = "PASS" if accuracy >= 0.80 else "FAIL"
    print(f"  overall accuracy {accuracy:.1%}  (needs >= 80.0%)  {verdict}")
    verdict = "PASS" if hard_recall >= 0.85 else "FAIL"
    print(f"  Hard recall      {hard_recall:.1%}  (needs >= 85.0%)  {verdict}")

    print("\nSanity check (placeholder mislabelled all three - README decision 6):")
    for word in ("difficult", "encyclopedia", "semiconductor", "cat", "the"):
        features = np.array([extract_features(word)])
        label = model.predict(features)[0]
        confidence = float(model.predict_proba(features).max())
        print(f"  {word:<15} {label:<7} {confidence:.2f}")

    if accuracy < 0.80 or hard_recall < 0.85:
        print("\nAC-26 not met - model NOT saved.")
        if not args.balance:
            print("Hard is usually the minority class; try again with --balance.")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.out)
    print(f"\nSaved {args.out} ({args.out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
