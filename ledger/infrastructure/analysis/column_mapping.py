"""Identify which column of an uploaded file holds each canonical field.

Real-world files rarely use our column names: a bank export may say "Importe", "Nº de
cuenta" or "Fecha valor". Everything runs locally, with no external service. Every
(field, column) pair gets a score from two independent signals, and then columns are
assigned greedily, best score first:

* **Header name: a small trained model.** `HeaderClassifier` is trained on
  `column_vocabulary.json`, a list of known header names per field. It turns headers into
  TF-IDF vectors of character n-grams (scikit-learn) and finds the nearest known header
  by cosine similarity. Character n-grams let it recognise variations it was never shown
  ("importe_neto_eur", "Fecha de la operación", typos). Headers are normalised first:
  Unidecode strips accents, and case and punctuation are ignored.
* **Values: content profiling.** A sample of the values is checked. ISO 4217 codes
  (pycountry) suggest a currency, valid IBANs (schwifty) an account, parseable dates
  (python-dateutil) a value date, decimals an amount, and unique alphanumeric codes an
  identifier.

To teach it a new header, add the header to the vocabulary file. No code changes needed.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final, Protocol

import numpy as np
import pandas as pd
import pycountry
from dateutil import parser as date_parser
from schwifty import IBAN
from schwifty.exceptions import SchwiftyException
from sklearn.feature_extraction.text import TfidfVectorizer
from unidecode import unidecode

from ledger.domain.value_objects import (
    REQUIRED_COLUMNS,
    ColumnMapping,
    ColumnMatch,
    MatchMethod,
)

VOCABULARY_PATH: Final = Path(__file__).with_name("column_vocabulary.json")

# A (field, column) pair needs at least this score to be matched at all.
MIN_SCORE: Final = 0.5
# Cosine similarity (0-1) to the nearest known header for two names to count as alike.
SIMILARITY_THRESHOLD: Final = 0.6
SAMPLE_SIZE: Final = 50

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_STOPWORDS: Final = frozenset({"de", "del", "la", "el", "of", "the", "n", "nro", "num"})
_ISO_CURRENCIES: Final = frozenset(c.alpha_3 for c in pycountry.currencies)
_DECIMAL = re.compile(r"^[+-]?\d{1,3}(?:[.,\s]?\d{3})*(?:[.,]\d+)?$|^[+-]?\d+(?:[.,]\d+)?$")
_HAS_DECIMALS = re.compile(r"[.,]\d{1,2}$")
_HAS_DIGIT = re.compile(r"\d")
_DATE_SHAPE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$")


class ColumnMapper(Protocol):
    def map(self, columns: Sequence[str], samples: Mapping[str, Sequence[str]]) -> ColumnMapping:
        """`samples` holds a few values per column, as raw strings."""
        ...


def normalise_header(header: str) -> str:
    """'  Nº de Cuenta_Destino ' -> 'no cuenta destino'."""
    words = _NON_ALNUM.sub(" ", unidecode(header).lower()).split()
    return " ".join(w for w in words if w not in _STOPWORDS) or " ".join(words)


@dataclass(frozen=True, slots=True)
class HeaderPrediction:
    field: str
    similarity: float  # cosine similarity to `nearest`, 0-1
    nearest: str  # the known header it resembles the most


class HeaderClassifier:
    """Nearest-neighbour classifier over TF-IDF character n-gram vectors."""

    def __init__(self, vocabulary: Mapping[str, Sequence[str]]) -> None:
        examples = {
            (normalise_header(header), field)
            for field in REQUIRED_COLUMNS
            for header in (field, *vocabulary.get(field, ()))
        }
        self._headers, self._fields = zip(*sorted(examples), strict=True)
        # char_wb: n-grams inside word boundaries, so word order and extra words matter less.
        self._vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        self._vectors = self._vectorizer.fit_transform(self._headers)

    @classmethod
    def from_file(cls, path: Path = VOCABULARY_PATH) -> "HeaderClassifier":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls({field: raw.get(field, []) for field in REQUIRED_COLUMNS})

    def predict(self, header: str) -> dict[str, HeaderPrediction]:
        """Best match per field for `header`."""
        vector = self._vectorizer.transform([normalise_header(header)])
        # Rows are L2-normalised by TfidfVectorizer: the dot product is the cosine.
        similarities = np.asarray((self._vectors @ vector.T).todense()).ravel()
        best: dict[str, HeaderPrediction] = {}
        for index in np.argsort(-similarities, kind="stable"):
            field = self._fields[index]
            if field not in best:
                best[field] = HeaderPrediction(
                    field, float(similarities[index]), self._headers[index]
                )
        return best


@cache
def default_classifier() -> HeaderClassifier:
    return HeaderClassifier.from_file()


@dataclass(frozen=True, slots=True)
class _Candidate:
    score: float
    field: str
    column: str
    method: MatchMethod


class VectorColumnMapper:
    def __init__(self, classifier: HeaderClassifier | None = None) -> None:
        self._classifier = classifier or default_classifier()

    def map(self, columns: Sequence[str], samples: Mapping[str, Sequence[str]]) -> ColumnMapping:
        candidates: list[_Candidate] = []
        for column in columns:
            predictions = self._classifier.predict(column)
            for field in REQUIRED_COLUMNS:
                candidate = _score(field, column, predictions[field], samples.get(column, ()))
                if candidate is not None:
                    candidates.append(candidate)
        # Best score first; ties keep the canonical field order and the file's column order.
        candidates.sort(
            key=lambda c: (-c.score, REQUIRED_COLUMNS.index(c.field), columns.index(c.column))
        )
        matches: dict[str, ColumnMatch] = {}
        used: set[str] = set()
        for c in candidates:
            if c.field in matches or c.column in used:
                continue
            matches[c.field] = ColumnMatch(
                field=c.field, source_column=c.column, method=c.method, confidence=c.score
            )
            used.add(c.column)
        return ColumnMapping(
            matches=tuple(matches[f] for f in REQUIRED_COLUMNS if f in matches),
            ignored_columns=tuple(c for c in columns if c not in used),
        )


def _score(
    field: str, column: str, prediction: HeaderPrediction, values: Sequence[str]
) -> _Candidate | None:
    header = normalise_header(column)
    if header == normalise_header(field):
        return _Candidate(1.0, field, column, MatchMethod.EXACT)
    if header == prediction.nearest:
        name_score, method = 0.95, MatchMethod.VOCABULARY
    elif prediction.similarity >= SIMILARITY_THRESHOLD:
        name_score, method = 0.9 * prediction.similarity, MatchMethod.SIMILARITY
    else:
        name_score, method = 0.0, MatchMethod.SIMILARITY
    content = _content_score(field, values)
    # Values alone are capped at 0.7: they can suggest a field but never prove it.
    # When both signals agree, the values nudge the name score up.
    if name_score >= 0.7 * content:
        score = min(1.0, name_score + 0.1 * content)
    else:
        score, method = 0.7 * content, MatchMethod.CONTENT
    if score < MIN_SCORE:
        return None
    return _Candidate(round(score, 2), field, column, method)


def _content_score(field: str, raw_values: Sequence[str]) -> float:
    values = [v.strip() for v in raw_values[:SAMPLE_SIZE] if v and v.strip()]
    if not values:
        return 0.0
    series = pd.Series(values)
    if field == "currency":
        return float(series.str.upper().isin(_ISO_CURRENCIES).mean())
    if field == "amount":
        numeric = series.str.match(_DECIMAL)
        with_decimals = series.str.contains(_HAS_DECIMALS)
        # Plain integers are as likely to be identifiers: decimals make it an amount.
        return float(numeric.mean() * (0.5 + 0.5 * with_decimals.mean()))
    if field == "value_date":
        return float(series.map(_looks_like_date).mean())
    if field == "account":
        ibans = series.map(_is_iban).mean()
        codes = series.str.contains(_HAS_DIGIT) & (series.str.len() >= 6)
        return float(max(ibans, 0.6 * codes.mean()))
    # external_id: unique, and codes rather than plain words or amounts.
    unique_ratio = series.nunique() / len(series)
    codes = series.str.contains(_HAS_DIGIT) & ~series.str.match(_DECIMAL)
    return float(unique_ratio * (0.4 + 0.4 * codes.mean()))


def _looks_like_date(value: str) -> bool:
    if not _DATE_SHAPE.match(value):
        return False
    try:
        date_parser.parse(value, dayfirst=True)
    except (ValueError, OverflowError):
        return False
    return True


def _is_iban(value: str) -> bool:
    try:
        IBAN(value)
    except SchwiftyException:
        return False
    return True
