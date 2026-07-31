"""Train the three models that ship with AegisFlow.

The corpus is generated from templates so the repo stays self-contained and the
build is reproducible (``--seed``). Point ``--csv`` at a real dataset with
``text,label`` columns to train on your own data instead - nothing else in the
platform changes.

    python ml/train.py                 # train everything into ml/artifacts
    python ml/train.py --csv data.csv  # bring your own labelled data
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

import joblib
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"

OPENERS = ["", "honestly ", "overall ", "to be fair ", "after two weeks ", "for the price "]
SUBJECTS = [
    "the delivery", "the packaging", "customer support", "the fabric", "the app",
    "the checkout flow", "the courier", "the return process", "the product", "the dashboard",
]
POSITIVE = [
    "was excellent and arrived early", "worked perfectly from day one", "is fast and reliable",
    "exceeded what i expected", "was smooth and easy to use", "is great value for money",
    "was handled quickly and politely", "feels premium and well made", "loaded instantly every time",
    "solved my problem in minutes",
]
NEGATIVE = [
    "was terrible and arrived damaged", "broke after three days", "is painfully slow",
    "was a complete waste of money", "keeps crashing on every attempt", "was rude and unhelpful",
    "never turned up at all", "faded and tore after one wash", "froze and lost my order",
    "still has not been refunded",
]
NEUTRAL = [
    "was about what i expected", "is fine, nothing remarkable", "does the job",
    "arrived on the promised date", "looks like the pictures online", "is average for this price range",
    "works as described", "was acceptable overall", "has both good and bad points",
    "is okay but i would compare options first",
]
TAILS = ["", " would order again", " not sure i would repeat", " decent enough", " see the photos attached", " as noted before"]

REAL_SAMPLES = [
    ("the courier called ahead and everything arrived intact, very happy", "positive"),
    ("two weeks late and nobody answers the support email", "negative"),
    ("quality is acceptable, delivery took the usual four days", "neutral"),
    ("app is beautiful and the checkout took seconds", "positive"),
    ("charged me twice and the refund never came", "negative"),
    ("it does what it says, no surprises either way", "neutral"),
    ("best purchase i have made this year, genuinely impressed", "positive"),
    ("stitching came apart immediately, total waste", "negative"),
    ("packaging was plain but the item was fine", "neutral"),
    ("support fixed my issue within ten minutes, brilliant service", "positive"),
]


@dataclass
class Dataset:
    texts: list[str]
    labels: list[str]


def add_noise(text: str, rng: random.Random) -> str:
    """Light typo injection so the character model has something to be good at."""
    if len(text) < 12 or rng.random() > 0.35:
        return text
    chars = list(text)
    index = rng.randrange(len(chars) - 1)
    mode = rng.choice(("swap", "drop", "double"))
    if mode == "swap":
        chars[index], chars[index + 1] = chars[index + 1], chars[index]
    elif mode == "drop":
        chars.pop(index)
    else:
        chars.insert(index, chars[index])
    return "".join(chars)


def synth_dataset(n_per_class: int, seed: int) -> Dataset:
    rng = random.Random(seed)
    texts: list[str] = []
    labels: list[str] = []
    for label, phrases in (("positive", POSITIVE), ("negative", NEGATIVE), ("neutral", NEUTRAL)):
        for _ in range(n_per_class):
            sentence = f"{rng.choice(OPENERS)}{rng.choice(SUBJECTS)} {rng.choice(phrases)}{rng.choice(TAILS)}".strip()
            texts.append(add_noise(sentence, rng))
            labels.append(label)
    for text, label in REAL_SAMPLES:
        texts.append(text)
        labels.append(label)
    order = list(range(len(texts)))
    rng.shuffle(order)
    return Dataset([texts[i] for i in order], [labels[i] for i in order])


def load_csv(path: Path) -> Dataset:
    texts, labels = [], []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            text = (row.get("text") or "").strip()
            label = (row.get("label") or "").strip()
            if text and label:
                texts.append(text)
                labels.append(label)
    if not texts:
        raise SystemExit(f"no usable rows in {path} (expected 'text,label' columns)")
    return Dataset(texts, labels)


def _save(name: str, pipeline, meta: dict) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    artifact = ARTIFACTS / f"{name}.joblib"
    joblib.dump(pipeline, artifact, compress=3)
    (ARTIFACTS / f"{name}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    size_kb = artifact.stat().st_size / 1024
    print(f"  saved {artifact.name} ({size_kb:.0f} KB)")


def train_classifier(name: str, version: str, pipeline: Pipeline, data: Dataset, seed: int) -> dict:
    x_train, x_test, y_train, y_test = train_test_split(
        data.texts, data.labels, test_size=0.2, random_state=seed, stratify=data.labels
    )
    started = time.perf_counter()
    pipeline.fit(x_train, y_train)
    elapsed = time.perf_counter() - started
    predictions = pipeline.predict(x_test)
    accuracy = float(accuracy_score(y_test, predictions))
    f1 = float(f1_score(y_test, predictions, average="macro"))
    meta = {
        "version": version,
        "accuracy": round(accuracy, 4),
        "f1_macro": round(f1, 4),
        "samples": len(data.texts),
        "test_samples": len(x_test),
        "classes": sorted(set(data.labels)),
        "training_seconds": round(elapsed, 3),
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(f"\n{name}: accuracy={accuracy:.4f} f1_macro={f1:.4f} ({elapsed:.2f}s)")
    print(classification_report(y_test, predictions, digits=3, zero_division=0))
    _save(name, pipeline, meta)
    return meta


def train_embedder(name: str, version: str, data: Dataset, dim: int, seed: int) -> dict:
    pipeline = Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
            ("svd", TruncatedSVD(n_components=dim, random_state=seed)),
        ]
    )
    started = time.perf_counter()
    pipeline.fit(data.texts)
    elapsed = time.perf_counter() - started
    explained = float(pipeline.named_steps["svd"].explained_variance_ratio_.sum())
    meta = {
        "version": version,
        "dim": dim,
        "explained_variance": round(explained, 4),
        "samples": len(data.texts),
        "training_seconds": round(elapsed, 3),
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(f"\n{name}: dim={dim} explained_variance={explained:.4f} ({elapsed:.2f}s)")
    _save(name, pipeline, meta)
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Train AegisFlow demo models")
    parser.add_argument("--csv", type=Path, default=None, help="optional text,label CSV")
    parser.add_argument("--per-class", type=int, default=700)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--dim", type=int, default=64)
    args = parser.parse_args()

    data = load_csv(args.csv) if args.csv else synth_dataset(args.per_class, args.seed)
    print(f"corpus: {len(data.texts)} samples, classes={sorted(set(data.labels))}")

    summary = {
        "sentiment_v1": train_classifier(
            "sentiment_v1",
            "sentiment-v1+tfidf-logreg",
            Pipeline(
                [
                    ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
                    ("clf", LogisticRegression(max_iter=1000, C=4.0, class_weight="balanced")),
                ]
            ),
            data,
            args.seed,
        ),
        "sentiment_v2": train_classifier(
            "sentiment_v2",
            "sentiment-v2+char-svm",
            Pipeline(
                [
                    ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)),
                    ("clf", LinearSVC(C=1.0, class_weight="balanced")),
                ]
            ),
            data,
            args.seed,
        ),
        "embed_v1": train_embedder("embed_v1", "embed-v1+tfidf-svd", data, args.dim, args.seed),
    }
    (ARTIFACTS / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nartifacts written to {ARTIFACTS}")


if __name__ == "__main__":
    main()
