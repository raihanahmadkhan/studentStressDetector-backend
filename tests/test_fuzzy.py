"""Numerical and isolation tests for the canonical routine-based index."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import itertools
import json

import numpy as np
import pytest

from app import fuzzy_v2 as fuzzy


def observation(sleep=7, workload=5, screen=6, activities=5, **extra):
    return {
        "sleep_hours": sleep,
        "academic_load": workload,
        "screen_hours": screen,
        "extracurricular_load": activities,
        **extra,
    }


def reference_membership(x, shape, points):
    """Independent piecewise formula including closed shoulder endpoints."""
    a, b, *rest = points
    c, d = (b, rest[0]) if shape == "triangle" else rest
    if x < a or x > d:
        return 0.0
    if b <= x <= c:
        return 1.0
    if a <= x < b:
        return (x - a) / (b - a)
    if c < x <= d:
        return (d - x) / (d - c)
    return 0.0


def reference_centroid(inputs, step=0.01):
    """Fine-grid trapezoidal integration, independent of engine defuzzification."""
    spec = fuzzy.MODEL_SPEC
    output = spec["output"]["terms"]
    x = np.linspace(0, 100, int(100 / step) + 1)
    aggregate = np.zeros_like(x)
    for rule in spec["rules"]:
        degrees = []
        for variable, term in rule["antecedents"]:
            definition = spec["inputs"][variable]["terms"][term]
            degrees.append(reference_membership(inputs[variable], definition["shape"], definition["points"]))
        strength = min(degrees)
        definition = output[rule["consequent"]]
        curve = np.fromiter(
            (reference_membership(value, definition["shape"], definition["points"]) for value in x),
            dtype=float,
            count=len(x),
        )
        aggregate = np.maximum(aggregate, np.minimum(strength, curve))
    widths = np.diff(x)
    moment = x * aggregate
    area = np.sum(widths * (aggregate[:-1] + aggregate[1:]) / 2)
    first_moment = np.sum(widths * (moment[:-1] + moment[1:]) / 2)
    return float(first_moment / area)


@pytest.mark.parametrize(
    "inputs,centroid,score,category,active",
    [
        (observation(), 50, 50.0, "Moderate", {"B05"}),
        (observation(9, 1, 2, 5), 65 / 7, 9.3, "Very low", {"B01"}),
        (observation(6.5, 1, 2, 5), 30, 30.0, "Low", {"B04"}),
        (observation(4, 5, 2, 5), 70, 70.0, "High", {"B08"}),
        (observation(4, 10, 2, 5), 635 / 7, 90.7, "Very high", {"B09"}),
    ],
)
def test_golden_single_consequents(inputs, centroid, score, category, active):
    result = fuzzy.evaluate(inputs)
    assert result["status"] == "ok"
    assert result["raw_centroid"] == pytest.approx(centroid, abs=1e-9)
    assert result["score"] == score
    assert result["category"] == category
    assert {rule["id"] for rule in result["rules"] if rule["firing_strength"] > 0} == active


@pytest.mark.parametrize("inputs", [
    observation(4, 3.5, 10, 5),
    observation(5.7, 6.4, 10.3, 8.4),
    observation(7.4, 3.6, 11.7, 9.1),
    observation(12, 10, 16, 10),
    observation(8.1, 6.9, 8.3, 7.2),
])
def test_centroid_matches_independent_finer_reference(inputs):
    result = fuzzy.evaluate(inputs)
    assert result["raw_centroid"] == pytest.approx(reference_centroid(inputs), abs=0.01)


def test_true_rule_strengths_clipping_and_aggregation():
    result = fuzzy.evaluate(observation(4, 3.5, 10, 5))
    active = {rule["id"]: rule["firing_strength"] for rule in result["rules"] if rule["firing_strength"] > 0}
    assert active == {"B07": 0.25, "B08": 0.25, "C02": 0.5}
    for rule in result["rules"]:
        assert rule["firing_strength"] == min(antecedent["degree"] for antecedent in rule["antecedents"])
        assert rule["weight"] == 1.0
    aggregate = result["aggregate"]
    assert len(aggregate["universe"]) == len(aggregate["membership"]) == 1001
    assert aggregate["universe"][0] == 0
    assert aggregate["universe"][-1] == 100
    assert aggregate["membership"][500] == 0.25
    assert aggregate["membership"][700] == 0.25
    assert aggregate["membership"][950] == 0.5


def test_shoulders_are_closed_and_boundaries_have_coverage():
    minimum = fuzzy.evaluate(observation(0, 0, 0, 0))
    maximum = fuzzy.evaluate(observation(12, 10, 16, 10))
    assert minimum["memberships"]["sleep_hours"]["short"] == 1
    assert minimum["memberships"]["academic_load"]["low"] == 1
    assert maximum["memberships"]["sleep_hours"]["longer"] == 1
    assert maximum["memberships"]["academic_load"]["high"] == 1
    assert maximum["memberships"]["screen_hours"]["high"] == 1
    assert maximum["memberships"]["extracurricular_load"]["high"] == 1
    for sleep, workload in itertools.product([0, 4, 5, 6, 6.5, 7, 8, 8.5, 12], [0, 2, 3, 4, 5, 6, 7, 8, 10]):
        result = fuzzy.evaluate(observation(sleep, workload, 0, 0))
        assert result["status"] == "ok"
        assert any(rule["firing_strength"] > 0 for rule in result["rules"])


def test_fixed_seed_supported_samples_have_coverage():
    random = np.random.default_rng(2048)
    for values in random.uniform([0, 0, 0, 0], [12, 10, 16, 10], size=(100, 4)):
        result = fuzzy.evaluate(observation(*values))
        assert result["status"] == "ok"
        assert 0 <= result["score"] <= 100


def test_rule_order_and_duplicate_application_do_not_change_max_aggregation():
    memberships = fuzzy.evaluate(observation(5.7, 6.4, 10.3, 8.4))["memberships"]
    original, _ = fuzzy._infer(memberships, fuzzy._RULES)
    reversed_rules, _ = fuzzy._infer(memberships, tuple(reversed(fuzzy._RULES)))
    duplicated, _ = fuzzy._infer(memberships, fuzzy._RULES + fuzzy._RULES)
    assert np.array_equal(original, reversed_rules)
    assert np.array_equal(original, duplicated)


def test_specification_rejects_duplicate_rules_and_missing_coverage():
    duplicate_id = fuzzy._RULES + (fuzzy._RULES[0],)
    with pytest.raises(ValueError, match="Duplicate rule ID"):
        fuzzy._validate_specification(duplicate_id)
    duplicate_logic = fuzzy._RULES + (replace(fuzzy._RULES[0], id="extra", antecedents=tuple(reversed(fuzzy._RULES[0].antecedents))),)
    with pytest.raises(ValueError, match="Duplicate logical rule"):
        fuzzy._validate_specification(duplicate_logic)
    with pytest.raises(ValueError, match="cover every"):
        fuzzy._validate_specification(fuzzy._RULES[1:])


@pytest.mark.parametrize("inputs", [observation(12.1), observation(screen=16.1), observation(24, 10, 24, 10)])
def test_valid_observations_outside_fuzzy_domain_are_unsupported(inputs):
    result = fuzzy.evaluate(inputs)
    assert result["status"] == "unsupported"
    assert result["reason"] == "outside_fuzzy_domain"
    assert result["raw_centroid"] is result["score"] is result["category"] is None
    assert result["memberships"] == {}
    assert result["rules"] == []
    assert result["aggregate"] == {"universe": [], "membership": []}


@pytest.mark.parametrize("variable,value", [
    ("sleep_hours", -0.1), ("sleep_hours", 24.1),
    ("academic_load", 10.1), ("screen_hours", 24.1),
    ("extracurricular_load", -1), ("sleep_hours", float("nan")),
    ("screen_hours", float("inf")), ("academic_load", "5"),
    ("sleep_hours", True), ("sleep_hours", None),
])
def test_invalid_inputs_raise_instead_of_clipping(variable, value):
    inputs = observation()
    inputs[variable] = value
    with pytest.raises(ValueError, match=variable):
        fuzzy.evaluate(inputs)


def test_missing_input_is_rejected_and_strain_is_not_a_model_input():
    with pytest.raises(ValueError, match="sleep_hours"):
        fuzzy.evaluate({})
    assert fuzzy.evaluate(observation(reported_strain=0)) == fuzzy.evaluate(observation(reported_strain=10))


@pytest.mark.parametrize("raw,score,category", [
    (24.94, 24.9, "Very low"), (24.95, 25.0, "Low"),
    (44.95, 45.0, "Moderate"), (64.95, 65.0, "High"),
    (84.95, 85.0, "Very high"), (50.05, 50.1, "Moderate"),
])
def test_half_up_rounding_and_category_use_the_same_score(raw, score, category):
    assert fuzzy._round_and_categorize(raw) == (score, category)


def test_specification_and_results_are_json_serializable_and_versioned():
    expected = sha256(json.dumps(fuzzy.MODEL_SPEC, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    result = fuzzy.evaluate(observation())
    assert fuzzy.SPEC_HASH == result["spec_hash"] == expected
    assert result["model_version"] == "fuzzy-2.0.0"
    assert len(fuzzy.MODEL_SPEC["rules"]) == 13
    json.dumps(result, allow_nan=False)


def test_returned_objects_and_public_spec_cannot_mutate_engine_state():
    inputs = observation()
    expected = fuzzy.evaluate(inputs)
    altered = fuzzy.evaluate(inputs)
    altered["aggregate"]["membership"][0] = 99
    altered["rules"][0]["antecedents"][0]["degree"] = 99
    altered["memberships"]["sleep_hours"]["short"] = 99
    saved = deepcopy(fuzzy.MODEL_SPEC)
    try:
        fuzzy.MODEL_SPEC["rules"][0]["consequent"] = "high"
        fuzzy.MODEL_SPEC["inputs"]["sleep_hours"]["terms"]["intermediate"]["points"][1] = 0
        assert fuzzy.evaluate(inputs) == expected
    finally:
        fuzzy.MODEL_SPEC.clear()
        fuzzy.MODEL_SPEC.update(saved)


def test_concurrent_evaluations_are_isolated():
    inputs = [observation(), observation(0, 0, 0, 0), observation(12, 10, 16, 10), observation(5.7, 6.4, 10.3, 8.4)] * 6
    expected = [fuzzy.evaluate(values) for values in inputs]
    with ThreadPoolExecutor(max_workers=8) as executor:
        actual = list(executor.map(fuzzy.evaluate, inputs))
    assert actual == expected


def test_unexpected_no_coverage_returns_explicit_error(monkeypatch):
    monkeypatch.setattr(fuzzy, "_RULES", ())
    result = fuzzy.evaluate(observation())
    assert result["status"] == "error"
    assert result["reason"] == "insufficient_coverage"
    assert result["score"] is None
