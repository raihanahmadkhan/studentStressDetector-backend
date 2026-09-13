from copy import deepcopy
import pytest
from app.fuzzy import evaluate
from app.guidance import recommend

BASE = dict(sleep_hours=6, academic_load=5, deadline_pressure=5, screen_hours=8, extracurricular_load=5, recovery=5, reported_strain=5)

@pytest.mark.parametrize('changes,expected', [
    ({'academic_load': 0, 'deadline_pressure': 10}, 'deadline_pressure'),
    ({'academic_load': 10, 'deadline_pressure': 0}, 'academic_load'),
    ({'sleep_hours': 12, 'recovery': 0}, 'recovery'),
    ({'sleep_hours': 0, 'recovery': 10}, 'sleep_hours'),
    ({'screen_hours': 0, 'extracurricular_load': 10}, 'extracurricular_load'),
    ({'screen_hours': 16, 'extracurricular_load': 0}, 'screen_hours'),
])
def test_specific_actions_use_only_active_adverse_rules(changes, expected):
    inputs = BASE | changes
    assessment = evaluate(inputs)
    original = deepcopy(assessment)
    suggestions = recommend(inputs, assessment)
    assert expected in [s['id'] for s in suggestions]
    assert 1 <= len(suggestions) <= 3
    assert len({s['component_id'] for s in suggestions}) == len(suggestions)
    for suggestion in suggestions:
        component = next(c for c in assessment['components'] if c['id'] == suggestion['component_id'])
        active_ids = {r['id'] for r in component['rules'] if r['firing_strength'] > 0 and r['consequent'] in {'medium', 'high'}}
        assert set(suggestion['rule_ids']) <= active_ids
    assert recommend(inputs, assessment) == suggestions
    assert assessment == original
    assert recommend(inputs | {'reported_strain': 10}, assessment) == suggestions

def test_low_inputs_offer_maintenance_without_inventing_a_pressure():
    inputs = BASE | dict(sleep_hours=12, recovery=10, academic_load=0, deadline_pressure=0, screen_hours=0, extracurricular_load=0)
    suggestions = recommend(inputs, evaluate(inputs))
    assert [s['id'] for s in suggestions] == ['maintain-routine']

@pytest.mark.parametrize('change', [{'status': 'error'}, {'status': 'unsupported'}, {'model_version': 'fuzzy-2.0.0'}])
def test_unavailable_or_unknown_models_have_no_guidance(change):
    assert recommend(BASE, evaluate(BASE) | change) == []
