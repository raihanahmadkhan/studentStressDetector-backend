"""Independent mathematical and product-policy checks for fuzzy-3.1.0."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from fractions import Fraction
import itertools
import random

import numpy as np
import pytest
from sqlalchemy import select
from uuid import uuid4

from app import fuzzy, fuzzy_v3
from app.models import CheckInRevision, EngineVersion
from test_checkins import signed_in, save

BASE = dict(sleep_hours=6, recovery=5, academic_load=5, deadline_pressure=5,
            screen_hours=8, extracurricular_load=5, reported_strain=5)


def partition(value, maximum):
    x = Fraction(str(value)) * 2 / maximum
    return (1-x, x, 0) if x <= 1 else (0, 2-x, x-1)


def test_every_supported_component_pair_against_independent_rational_oracle():
    """1375 pairs cover every discrete combination inside the separable modules.

    Ordered rows/columns + positive partition weights + positive fusion weights
    prove full six-dimensional monotonicity without enumerating 46m check-ins.
    """
    visited = 0
    for key, _, a, b, weight, matrix in fuzzy._MODULES:
        xs = np.arange(0, fuzzy._RANGES[a]+.01, .25 if a.endswith('hours') else 1)
        ys = np.arange(0, fuzzy._RANGES[b]+.01, .25 if b.endswith('hours') else 1)
        grid = np.zeros((len(xs), len(ys)))
        for i, x in enumerate(xs):
            for j, y in enumerate(ys):
                result = fuzzy.evaluate({**BASE, a: float(x), b: float(y)})
                assert result['status'] == 'ok'
                component = next(c for c in result['components'] if c['id'] == key)
                px, py = partition(float(x), fuzzy._RANGES[a]), partition(float(y), fuzzy._RANGES[b])
                oracle = sum(px[r]*py[s]*(15,50,85)[matrix[r][s]] for r in range(3) for s in range(3))
                assert component['raw_centroid'] == pytest.approx(float(oracle), abs=1e-10)
                raw = Fraction(str(weight))*oracle + (1-Fraction(str(weight)))*50
                rounded_tenths = raw*10 + Fraction(1,2)
                assert result['score'] == (rounded_tenths.numerator // rounded_tenths.denominator)/10
                assert sum(r['firing_strength'] for r in component['rules']) == pytest.approx(1)
                assert 15 <= component['raw_centroid'] <= 85
                grid[i,j] = component['raw_centroid']
                visited += 1
        direction = -1 if key == 'recovery_deficit' else 1
        assert np.all(direction*np.diff(grid, axis=0) >= -1e-10)
        assert np.all(direction*np.diff(grid, axis=1) >= -1e-10)
    assert visited == 1375


def test_commitments_cannot_be_masked_by_low_screen_use():
    for screen in (0, 4, 8, 12, 16):
        for commitments, minimum in ((5,50),(10,85)):
            result = fuzzy.evaluate({**BASE, 'screen_hours':screen, 'extracurricular_load':commitments})
            assert result['components'][2]['raw_centroid'] >= minimum
    assert fuzzy.evaluate({**BASE, 'screen_hours':16, 'extracurricular_load':0})['components'][2]['raw_centroid'] == 50


def test_halfway_rounding_is_mathematical_half_up():
    result = fuzzy.evaluate({**BASE, 'academic_load':0, 'deadline_pressure':0})
    assert result['raw_score'] == 34.25
    assert result['score'] == 34.3
    rng = random.Random(73)
    for _ in range(100):
        inputs = {name: rng.randrange(maximum*4+1)/4 if name.endswith('hours') else rng.randrange(maximum+1)
                  for name,maximum in fuzzy._RANGES.items()}
        result = fuzzy.evaluate(inputs)
        raw = Fraction(0)
        for _,_,a,b,weight,matrix in fuzzy._MODULES:
            px,py = partition(inputs[a],fuzzy._RANGES[a]),partition(inputs[b],fuzzy._RANGES[b])
            raw += Fraction(str(weight))*sum(px[i]*py[j]*(15,50,85)[matrix[i][j]] for i,j in itertools.product(range(3),repeat=2))
        # Nonnegative rational HALF_UP to tenths, independent of Decimal/NumPy.
        expected = ((raw*10 + Fraction(1,2)).numerator // (raw*10 + Fraction(1,2)).denominator)/10
        assert result['score'] == expected


def test_all_category_boundaries_and_partition_knots_are_continuous():
    for cutoff in (25,45,65,85):
        below = fuzzy._round_and_categorize(cutoff-.1)
        at = fuzzy._round_and_categorize(cutoff)
        assert below[1] != at[1]
    for name, maximum in fuzzy._RANGES.items():
        knot = maximum/2
        left = fuzzy.evaluate({**BASE,name:knot-1e-7})['raw_score']
        right = fuzzy.evaluate({**BASE,name:knot+1e-7})['raw_score']
        assert abs(right-left) < 1e-5


@pytest.mark.parametrize('value', [True, '5', float('nan'), float('inf'), Decimal('NaN'), Decimal('sNaN'), 10**1000, -1])
def test_invalid_numeric_engine_inputs_fail_cleanly(value):
    with pytest.raises(ValueError):
        fuzzy.evaluate({**BASE, 'sleep_hours':value})


def test_nonfinite_centroid_fails_closed(monkeypatch):
    monkeypatch.setattr(fuzzy.fuzz, 'defuzz', lambda *args: float('nan'))
    result = fuzzy.evaluate(BASE)
    assert result['status'] == 'error' and result['score'] is None


def test_concurrent_evaluation_and_trace_mutation_do_not_change_model():
    expected = fuzzy.evaluate(BASE)
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(value == expected for value in executor.map(fuzzy.evaluate, [BASE]*16))
    altered = fuzzy.evaluate(BASE)
    altered['components'][0]['aggregate']['membership'][0] = 999
    altered['rules'][0]['antecedents'][0]['degree'] = 999
    assert fuzzy.evaluate(BASE) == expected


def test_v30_history_survives_current_model_edit_and_scenario(signed_in, db):
    client, _ = signed_in
    saved = save(client).json()
    old = fuzzy_v3.evaluate(saved['inputs'])
    db.add(EngineVersion(version=fuzzy_v3.MODEL_VERSION, kind='fuzzy', spec_hash=fuzzy_v3.SPEC_HASH,
                         specification=fuzzy_v3.MODEL_SPEC, status='active'))
    db.flush()
    revision = db.scalar(select(CheckInRevision))
    revision.engine_version = fuzzy_v3.MODEL_VERSION
    revision.assessment = old
    db.commit()
    reference = client.get('/api/check-ins/'+saved['id']).json()
    scenario = client.post('/api/scenarios', json={'inputs':BASE,'reference_id':saved['id']}).json()
    assert scenario['reference']['assessment']['model_version'] == fuzzy.MODEL_VERSION
    assert client.get('/api/check-ins/'+saved['id']).json() == reference
    updated = client.patch('/api/check-ins/'+saved['id'], json={**BASE,'expected_revision':1},
                           headers={'Idempotency-Key':str(uuid4())}).json()
    assert updated['assessment']['model_version'] == 'fuzzy-3.1.0'
    db.expire_all()
    assert db.scalar(select(CheckInRevision).where(CheckInRevision.revision==1)).assessment == old
    export = client.get('/api/data/export').json()
    assert {r['assessment']['model_version'] for r in export['revisions']} == {'fuzzy-3.0.0','fuzzy-3.1.0'}
