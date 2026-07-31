"""Model runtime.

The platform is model agnostic: a model is anything that can be loaded once and
called with a dict. Three scikit-learn pipelines ship with the repo (trained by
``ml/train.py``) so the system is demonstrable end to end, and any of them can
be swapped for an ONNX / torch / remote model by editing ``_PREDICTORS``.

If artifacts are missing the runtime falls back to a deterministic heuristic and
flags the answer as ``degraded`` instead of failing the request - graceful
degradation is a first-class behaviour here, not an afterthought.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings
from .resilience import PermanentError

MAX_TEXT_CHARS = 4000

MODEL_SPECS: dict[str, dict[str, Any]] = {
    "sentiment-v1": {
        "task": "text-classification",
        "artifact": "sentiment_v1.joblib",
        "description": "TF-IDF word n-grams + logistic regression. Primary production model.",
        "input": {"text": "string"},
        "output": {"label": "positive|negative|neutral", "score": "float"},
    },
    "sentiment-v2": {
        "task": "text-classification",
        "artifact": "sentiment_v2.joblib",
        "description": "Character n-gram SVM, more robust to typos. Used for canary traffic.",
        "input": {"text": "string"},
        "output": {"label": "positive|negative|neutral", "score": "float"},
    },
    "embed-v1": {
        "task": "embedding",
        "artifact": "embed_v1.joblib",
        "description": "TF-IDF + truncated SVD, 64-dim sentence vectors for retrieval.",
        "input": {"text": "string"},
        "output": {"vector": "float[64]", "dim": "int"},
    },
}

_POSITIVE = {
    "good", "great", "excellent", "love", "loved", "amazing", "fast", "smooth", "perfect", "happy",
    "recommend", "reliable", "beautiful", "helpful", "cheap", "worth", "brilliant", "solid", "quick",
}
_NEGATIVE = {
    "bad", "terrible", "awful", "hate", "broken", "slow", "late", "worst", "poor", "refund",
    "damaged", "waste", "useless", "rude", "expensive", "buggy", "crash", "disappointed", "never",
}


@dataclass
class Prediction:
    result: dict[str, Any]
    model_version: str
    degraded: bool
    compute_ms: float


def _require_text(payload: dict[str, Any]) -> str:
    text = payload.get("text") or payload.get("input") or ""
    if not isinstance(text, str) or not text.strip():
        raise PermanentError("input.text must be a non-empty string")
    if len(text) > MAX_TEXT_CHARS:
        raise PermanentError(f"input.text exceeds {MAX_TEXT_CHARS} characters")
    return text.strip()


class ModelRegistry:
    def __init__(self, artifacts_dir: str | None = None) -> None:
        self.dir = Path(artifacts_dir or settings.artifacts_dir)
        self._pipelines: dict[str, Any] = {}
        self._meta: dict[str, dict] = {}
        self.loaded_at: float | None = None

    # ---- lifecycle ------------------------------------------------------
    def load(self) -> None:
        try:
            import joblib
        except Exception:
            joblib = None  # type: ignore[assignment]
        for name, spec in MODEL_SPECS.items():
            path = self.dir / str(spec["artifact"])
            meta_path = path.with_suffix(".json")
            if joblib is not None and path.exists():
                try:
                    self._pipelines[name] = joblib.load(path)
                    if meta_path.exists():
                        self._meta[name] = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    self._pipelines.pop(name, None)
        self.loaded_at = time.time()

    def warmup(self) -> None:
        for name in MODEL_SPECS:
            try:
                self.predict(name, {"text": "warmup ping"})
            except Exception:
                continue

    # ---- introspection --------------------------------------------------
    def has(self, name: str) -> bool:
        return name in MODEL_SPECS

    def version(self, name: str) -> str:
        meta = self._meta.get(name) or {}
        if name in self._pipelines:
            return str(meta.get("version") or f"{name}+sklearn")
        return f"{name}+fallback"

    def catalog(self) -> list[dict[str, Any]]:
        out = []
        for name, spec in MODEL_SPECS.items():
            meta = self._meta.get(name, {})
            out.append(
                {
                    "name": name,
                    "task": spec["task"],
                    "description": spec["description"],
                    "input": spec["input"],
                    "output": spec["output"],
                    "loaded": name in self._pipelines,
                    "version": self.version(name),
                    "metrics": {
                        k: meta.get(k)
                        for k in ("accuracy", "f1_macro", "samples", "trained_at", "training_seconds")
                        if meta.get(k) is not None
                    },
                }
            )
        return out

    def validate(self, name: str, payload: dict[str, Any]) -> None:
        """Cheap edge validation so junk never becomes a queued job."""
        if not self.has(name):
            raise PermanentError(f"unknown model '{name}'")
        task = MODEL_SPECS[name]["task"]
        if task in ("text-classification", "embedding"):
            _require_text(payload)

    # ---- prediction -----------------------------------------------------
    def predict(self, name: str, payload: dict[str, Any]) -> Prediction:
        if not self.has(name):
            raise PermanentError(f"unknown model '{name}'")
        started = time.perf_counter()
        pipeline = self._pipelines.get(name)
        fn: Callable[[Any, dict], dict] = _PREDICTORS[MODEL_SPECS[name]["task"]]
        if pipeline is None:
            result = _FALLBACKS[MODEL_SPECS[name]["task"]](payload)
            degraded = True
        else:
            result = fn(pipeline, payload)
            degraded = False
        compute_ms = (time.perf_counter() - started) * 1000
        return Prediction(result=result, model_version=self.version(name), degraded=degraded, compute_ms=compute_ms)


# ---- concrete predictors -------------------------------------------------
def _predict_classification(pipeline: Any, payload: dict) -> dict:
    text = _require_text(payload)
    label = str(pipeline.predict([text])[0])
    scores: dict[str, float] = {}
    if hasattr(pipeline, "predict_proba"):
        probabilities = pipeline.predict_proba([text])[0]
        scores = {str(c): round(float(p), 4) for c, p in zip(pipeline.classes_, probabilities, strict=False)}
    elif hasattr(pipeline, "decision_function"):
        raw = pipeline.decision_function([text])
        values = raw[0] if getattr(raw, "ndim", 1) > 1 else [float(raw[0])]
        exp = [math.exp(min(20.0, max(-20.0, float(v)))) for v in values]
        total = sum(exp) or 1.0
        classes = list(pipeline.classes_) if len(exp) == len(getattr(pipeline, "classes_", [])) else [label]
        scores = {str(c): round(e / total, 4) for c, e in zip(classes, exp, strict=False)}
    return {
        "label": label,
        "score": round(float(scores.get(label, 1.0)), 4),
        "probabilities": scores,
        "chars": len(text),
    }


def _predict_embedding(pipeline: Any, payload: dict) -> dict:
    text = _require_text(payload)
    vector = pipeline.transform([text])[0]
    values = [round(float(v), 6) for v in vector]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return {"dim": len(values), "vector": [round(v / norm, 6) for v in values], "norm": round(norm, 6)}


def _fallback_classification(payload: dict) -> dict:
    text = _require_text(payload)
    tokens = [t.strip(".,!?;:()\"'").lower() for t in text.split()]
    pos = sum(1 for t in tokens if t in _POSITIVE)
    neg = sum(1 for t in tokens if t in _NEGATIVE)
    if pos == neg:
        label, score = "neutral", 0.5
    elif pos > neg:
        label, score = "positive", min(0.95, 0.55 + 0.1 * (pos - neg))
    else:
        label, score = "negative", min(0.95, 0.55 + 0.1 * (neg - pos))
    return {
        "label": label,
        "score": round(score, 4),
        "probabilities": {label: round(score, 4)},
        "chars": len(text),
        "engine": "lexicon-fallback",
    }


def _fallback_embedding(payload: dict) -> dict:
    text = _require_text(payload)
    digest = hashlib.sha256(text.lower().encode()).digest()
    raw = [((digest[i % len(digest)] + i * 7) % 255) / 255 - 0.5 for i in range(64)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return {"dim": 64, "vector": [round(v / norm, 6) for v in raw], "norm": 1.0, "engine": "hash-fallback"}


_PREDICTORS: dict[str, Callable[[Any, dict], dict]] = {
    "text-classification": _predict_classification,
    "embedding": _predict_embedding,
}
_FALLBACKS: dict[str, Callable[[dict], dict]] = {
    "text-classification": _fallback_classification,
    "embedding": _fallback_embedding,
}

registry = ModelRegistry()
