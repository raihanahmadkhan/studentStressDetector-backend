"""Canonical, stateless Mamdani routine index.

The index is an explicitly versioned heuristic, not a clinical measurement.
The public specification is a JSON-serializable snapshot; evaluation uses the
private immutable definitions compiled from it once when this module loads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
import math
from numbers import Real

import numpy as np
import skfuzzy as fuzz


MODEL_VERSION = "fuzzy-2.0.0"
MODEL_SPEC = {
    "model_version": MODEL_VERSION,
    "questionnaire_version": "check-in-1.0.0",
    "name": "Routine-based index",
    "inputs": {
        "sleep_hours": {
            "observational_range": [0, 24],
            "model_range": [0, 12],
            "terms": {
                "short": {"shape": "trapezoid", "points": [0, 0, 4, 6]},
                "intermediate": {"shape": "triangle", "points": [5, 6.5, 8]},
                "longer": {"shape": "trapezoid", "points": [7, 8.5, 12, 12]},
            },
        },
        "academic_load": {
            "observational_range": [0, 10],
            "model_range": [0, 10],
            "terms": {
                "low": {"shape": "trapezoid", "points": [0, 0, 2, 4]},
                "medium": {"shape": "triangle", "points": [3, 5, 7]},
                "high": {"shape": "trapezoid", "points": [6, 8, 10, 10]},
            },
        },
        "screen_hours": {
            "observational_range": [0, 24],
            "model_range": [0, 16],
            "terms": {
                "high": {"shape": "trapezoid", "points": [8, 12, 16, 16]},
            },
        },
        "extracurricular_load": {
            "observational_range": [0, 10],
            "model_range": [0, 10],
            "terms": {
                "high": {"shape": "trapezoid", "points": [7, 9, 10, 10]},
            },
        },
    },
    "output": {
        "range": [0, 100],
        "step": 0.1,
        "terms": {
            "very_low": {"shape": "trapezoid", "points": [0, 0, 10, 25]},
            "low": {"shape": "triangle", "points": [15, 30, 45]},
            "moderate": {"shape": "triangle", "points": [35, 50, 65]},
            "high": {"shape": "triangle", "points": [55, 70, 85]},
            "very_high": {"shape": "trapezoid", "points": [75, 90, 100, 100]},
        },
        "categories": [
            {"upper_exclusive": 25, "label": "Very low"},
            {"upper_exclusive": 45, "label": "Low"},
            {"upper_exclusive": 65, "label": "Moderate"},
            {"upper_exclusive": 85, "label": "High"},
            {"upper_exclusive": None, "label": "Very high"},
        ],
    },
    "inference": {
        "type": "Mamdani",
        "and": "minimum",
        "implication": "minimum_clipping",
        "aggregation": "maximum",
        "defuzzification": "centroid",
        "rounding": {"decimal_places": 1, "mode": "ROUND_HALF_UP"},
        "category_uses": "rounded_score",
    },
    "rules": [
        {"id": "B01", "antecedents": [["sleep_hours", "longer"], ["academic_load", "low"]], "consequent": "very_low", "weight": 1.0},
        {"id": "B02", "antecedents": [["sleep_hours", "longer"], ["academic_load", "medium"]], "consequent": "moderate", "weight": 1.0},
        {"id": "B03", "antecedents": [["sleep_hours", "longer"], ["academic_load", "high"]], "consequent": "high", "weight": 1.0},
        {"id": "B04", "antecedents": [["sleep_hours", "intermediate"], ["academic_load", "low"]], "consequent": "low", "weight": 1.0},
        {"id": "B05", "antecedents": [["sleep_hours", "intermediate"], ["academic_load", "medium"]], "consequent": "moderate", "weight": 1.0},
        {"id": "B06", "antecedents": [["sleep_hours", "intermediate"], ["academic_load", "high"]], "consequent": "high", "weight": 1.0},
        {"id": "B07", "antecedents": [["sleep_hours", "short"], ["academic_load", "low"]], "consequent": "moderate", "weight": 1.0},
        {"id": "B08", "antecedents": [["sleep_hours", "short"], ["academic_load", "medium"]], "consequent": "high", "weight": 1.0},
        {"id": "B09", "antecedents": [["sleep_hours", "short"], ["academic_load", "high"]], "consequent": "very_high", "weight": 1.0},
        {"id": "C01", "antecedents": [["screen_hours", "high"], ["academic_load", "high"]], "consequent": "very_high", "weight": 1.0},
        {"id": "C02", "antecedents": [["screen_hours", "high"], ["sleep_hours", "short"]], "consequent": "very_high", "weight": 1.0},
        {"id": "C03", "antecedents": [["extracurricular_load", "high"], ["academic_load", "high"]], "consequent": "very_high", "weight": 1.0},
        {"id": "C04", "antecedents": [["extracurricular_load", "high"], ["sleep_hours", "short"]], "consequent": "very_high", "weight": 1.0},
    ],
    "limitations": [
        "This routine-based index is a heuristic baseline, not a medically validated stress measurement.",
        "It does not measure or replace self-reported strain.",
        "Rule activations explain the calculation; they do not establish causes of wellbeing changes.",
        "Sleep above 12 hours or screen time above 16 hours is outside this model's supported domain.",
    ],
}
SPEC_HASH = sha256(
    json.dumps(MODEL_SPEC, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class _Term:
    name: str
    shape: str
    points: tuple[float, ...]


@dataclass(frozen=True)
class _Variable:
    name: str
    observational_range: tuple[float, float]
    model_range: tuple[float, float]
    terms: tuple[_Term, ...]


@dataclass(frozen=True)
class _Rule:
    id: str
    antecedents: tuple[tuple[str, str], ...]
    consequent: str
    weight: float


def _terms(spec: Mapping) -> tuple[_Term, ...]:
    return tuple(
        _Term(name, definition["shape"], tuple(definition["points"]))
        for name, definition in spec.items()
    )


_VARIABLES = tuple(
    _Variable(
        name,
        tuple(definition["observational_range"]),
        tuple(definition["model_range"]),
        _terms(definition["terms"]),
    )
    for name, definition in MODEL_SPEC["inputs"].items()
)
_OUTPUT_TERMS = _terms(MODEL_SPEC["output"]["terms"])
_RULES = tuple(
    _Rule(
        rule["id"],
        tuple(tuple(condition) for condition in rule["antecedents"]),
        rule["consequent"],
        rule["weight"],
    )
    for rule in MODEL_SPEC["rules"]
)
_CATEGORIES = tuple(
    (category["upper_exclusive"], category["label"])
    for category in MODEL_SPEC["output"]["categories"]
)
_LIMITATIONS = tuple(MODEL_SPEC["limitations"])
_UNIVERSE = np.linspace(0.0, 100.0, 1001)
_UNIVERSE.setflags(write=False)


def _membership(values: np.ndarray, term: _Term) -> np.ndarray:
    if term.shape == "triangle":
        return fuzz.trimf(values, term.points)
    if term.shape == "trapezoid":
        return fuzz.trapmf(values, term.points)
    raise ValueError(f"Unsupported membership shape: {term.shape}")


def _validate_specification(rules: tuple[_Rule, ...]) -> None:
    """Reject malformed/duplicate rules and prove baseline coverage.

    Each base input's terms cover every piecewise-linear segment. All nine
    sleep/load term pairs have base rules, so at least one base rule fires at
    every supported point regardless of the two contextual inputs.
    """
    terms = {variable.name: {term.name for term in variable.terms} for variable in _VARIABLES}
    outputs = {term.name for term in _OUTPUT_TERMS}
    ids: set[str] = set()
    signatures: set[tuple] = set()
    pairs: set[tuple[str, str]] = set()

    for variable in _VARIABLES:
        for term in variable.terms:
            expected = 3 if term.shape == "triangle" else 4 if term.shape == "trapezoid" else 0
            if len(term.points) != expected or sorted(term.points) != list(term.points):
                raise ValueError(f"Invalid membership definition: {variable.name}.{term.name}")
            if not all(math.isfinite(point) for point in term.points):
                raise ValueError("Membership points must be finite")

    for rule in rules:
        if rule.id in ids:
            raise ValueError(f"Duplicate rule ID: {rule.id}")
        ids.add(rule.id)
        if not rule.antecedents or len({variable for variable, _ in rule.antecedents}) != len(rule.antecedents):
            raise ValueError(f"Invalid antecedents for {rule.id}")
        if any(term not in terms.get(variable, set()) for variable, term in rule.antecedents):
            raise ValueError(f"Unknown antecedent in {rule.id}")
        if rule.consequent not in outputs or rule.weight != 1.0:
            raise ValueError(f"Invalid consequent or weight for {rule.id}")
        signature = (tuple(sorted(rule.antecedents)), rule.consequent, rule.weight)
        if signature in signatures:
            raise ValueError(f"Duplicate logical rule: {rule.id}")
        signatures.add(signature)
        antecedents = dict(rule.antecedents)
        if set(antecedents) == {"sleep_hours", "academic_load"}:
            pairs.add((antecedents["sleep_hours"], antecedents["academic_load"]))

    expected_pairs = {
        (sleep, load)
        for sleep in terms["sleep_hours"]
        for load in terms["academic_load"]
    }
    if pairs != expected_pairs:
        raise ValueError("Base rules do not cover every sleep/load term pair")

    for variable in _VARIABLES:
        if variable.name not in {"sleep_hours", "academic_load"}:
            continue
        knots = sorted({*variable.model_range, *(point for term in variable.terms for point in term.points)})
        samples = np.array(sorted({*knots, *((left + right) / 2 for left, right in zip(knots, knots[1:]))}))
        coverage = np.maximum.reduce([_membership(samples, term) for term in variable.terms])
        if np.any(coverage <= 0):
            raise ValueError(f"Membership functions leave a gap in {variable.name}")


_validate_specification(_RULES)
_OUTPUT_MEMBERSHIPS = tuple((term.name, _membership(_UNIVERSE, term)) for term in _OUTPUT_TERMS)
for _name, _values in _OUTPUT_MEMBERSHIPS:
    _values.setflags(write=False)
del _name, _values


def _round_and_categorize(raw_centroid: float) -> tuple[float, str]:
    score = float(Decimal(str(raw_centroid)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    for upper, label in _CATEGORIES:
        if upper is None or score < upper:
            return score, label
    raise RuntimeError("Output categories are incomplete")


def _empty_result(status: str, reason: str) -> dict:
    return {
        "status": status,
        "raw_centroid": None,
        "score": None,
        "category": None,
        "model_version": MODEL_VERSION,
        "spec_hash": SPEC_HASH,
        "memberships": {},
        "rules": [],
        "aggregate": {"universe": [], "membership": []},
        "reason": reason,
        "limitations": list(_LIMITATIONS),
    }


def _infer(memberships: dict[str, dict[str, float]], rules: tuple[_Rule, ...]) -> tuple[np.ndarray, list[dict]]:
    """Max aggregation makes ordering and repeated rule application immaterial."""
    aggregate = np.zeros_like(_UNIVERSE)
    output_memberships = dict(_OUTPUT_MEMBERSHIPS)
    trace = []
    for rule in rules:
        antecedents = [
            {"variable": variable, "term": term, "degree": memberships[variable][term]}
            for variable, term in rule.antecedents
        ]
        strength = min(antecedent["degree"] for antecedent in antecedents) * rule.weight
        np.maximum(aggregate, np.minimum(strength, output_memberships[rule.consequent]), out=aggregate)
        trace.append({
            "id": rule.id,
            "antecedents": antecedents,
            "consequent": rule.consequent,
            "weight": rule.weight,
            "firing_strength": strength,
        })
    return aggregate, trace


def evaluate(inputs: Mapping[str, object]) -> dict:
    """Calculate one isolated assessment, ignoring non-model input fields.

    Observations outside the questionnaire domain raise ValueError. Valid
    observations outside the narrower fuzzy domain produce ``unsupported``;
    they are never clipped, defaulted to 50, or represented as a zero index.
    """
    if not isinstance(inputs, Mapping):
        raise ValueError("Inputs must be a mapping")
    values: dict[str, float] = {}
    for variable in _VARIABLES:
        value = inputs.get(variable.name)
        if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
            raise ValueError(f"{variable.name} must be a finite number")
        try:
            number = float(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"{variable.name} must be a finite number") from exc
        minimum, maximum = variable.observational_range
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f"{variable.name} must be between {minimum} and {maximum}")
        values[variable.name] = number

    if any(not variable.model_range[0] <= values[variable.name] <= variable.model_range[1] for variable in _VARIABLES):
        return _empty_result("unsupported", "outside_fuzzy_domain")

    memberships = {
        variable.name: {
            term.name: float(_membership(np.array([values[variable.name]]), term)[0])
            for term in variable.terms
        }
        for variable in _VARIABLES
    }
    aggregate, trace = _infer(memberships, _RULES)
    if not np.any(aggregate > 0):
        result = _empty_result("error", "insufficient_coverage")
        result.update(memberships=memberships, rules=trace)
        return result

    raw_centroid = float(fuzz.defuzz(_UNIVERSE, aggregate, "centroid"))
    score, category = _round_and_categorize(raw_centroid)
    return {
        "status": "ok",
        "raw_centroid": raw_centroid,
        "score": score,
        "category": category,
        "model_version": MODEL_VERSION,
        "spec_hash": SPEC_HASH,
        "memberships": memberships,
        "rules": trace,
        "aggregate": {"universe": _UNIVERSE.tolist(), "membership": aggregate.tolist()},
        "reason": None,
        "limitations": list(_LIMITATIONS),
    }
