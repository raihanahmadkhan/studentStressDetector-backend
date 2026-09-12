"""Versioned modular product-sum fuzzy heuristic; reported strain is never an antecedent."""
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256
from numbers import Real
import copy
import json
import math
import numpy as np
import skfuzzy as fuzz
from app.fuzzy_v2 import _round_and_categorize

MODEL_VERSION = 'fuzzy-3.0.0'
QUESTIONNAIRE_VERSION = 'check-in-2.0.0'
_TERMS = ('low', 'medium', 'high')
_RANGES = {'sleep_hours': 12, 'academic_load': 10, 'deadline_pressure': 10,
           'screen_hours': 16, 'extracurricular_load': 10, 'recovery': 10}
_MODULES = (
    ('academic_pressure', 'Academic pressure', 'academic_load', 'deadline_pressure', .45, ((0,1,2),(1,1,2),(2,2,2))),
    ('recovery_deficit', 'Recovery deficit', 'sleep_hours', 'recovery', .40, ((2,2,2),(2,1,1),(2,1,0))),
    ('contextual_pressure', 'Contextual pressure', 'screen_hours', 'extracurricular_load', .15, ((0,0,1),(0,1,2),(1,2,2))),
)
MODEL_SPEC = {'model_version': MODEL_VERSION, 'questionnaire_version': QUESTIONNAIRE_VERSION,
    'inputs': {k: {'range': [0,v], 'terms': {'low': [0,0,v/2], 'medium': [0,v/2,v], 'high': [v/2,v,v]}} for k,v in _RANGES.items()},
    'components': [{'id': k, 'label': label, 'inputs': [a,b], 'weight': w, 'rule_matrix': matrix} for k,label,a,b,w,matrix in _MODULES],
    'output_terms': {'low': [0,15,30], 'medium': [35,50,65], 'high': [70,85,100]},
    'inference': {'type': 'Product-sum (Larsen-style) per component', 'and': 'product', 'implication': 'product scaling', 'aggregation': 'sum', 'defuzzification': 'centroid', 'universe_step': .1},
    'fusion': 'Weighted sum of unrounded component centroids; HALF_UP one decimal; bands 25/45/65/85',
    'limitations': ['Authored heuristic baseline, not clinically validated. Inputs and weights are not validated predictors.', 'Reported strain is an independent observation, never a fuzzy input.', 'Screen duration does not distinguish useful and unwanted use.', 'Longer sleep is treated as greater recovery capacity; sleep quality is not measured.', 'Component centroids and the final index lie between 15 and 85; 0 and 100 are not reachable.']}
_SPEC = copy.deepcopy(MODEL_SPEC)
SPEC_HASH = sha256(json.dumps(MODEL_SPEC, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
_U = np.linspace(0,100,1001)
_OUT = {k: fuzz.trimf(_U,v) for k,v in _SPEC['output_terms'].items()}

def _empty_result(status, reason):
    return dict(status=status, reason=reason, raw_centroid=None, raw_score=None, score=None, category=None,
        model_version=MODEL_VERSION, spec_hash=SPEC_HASH, memberships={}, rules=[], components=[],
        aggregate={'universe': [], 'membership': []}, limitations=list(_SPEC['limitations']))

def evaluate(inputs):
    if not isinstance(inputs, Mapping):
        raise ValueError('Inputs must be a mapping')
    if any(inputs.get(k) is None for k in _RANGES):
        return _empty_result('unsupported', 'missing_current_model_inputs')
    memberships = {}
    for k, maximum in _RANGES.items():
        v = inputs[k]
        if isinstance(v, bool) or not isinstance(v, (Real, Decimal)) or not math.isfinite(float(v)) or not 0 <= v <= maximum:
            raise ValueError(f'{k} must be between 0 and {maximum}')
        memberships[k] = {term: float(fuzz.trimf(np.array([float(v)]), points)[0]) for term,points in _SPEC['inputs'][k]['terms'].items()}
    result = _empty_result('ok', None)
    result['memberships'] = memberships
    for key,label,a,b,weight,matrix in _MODULES:
        aggregate = np.zeros_like(_U)
        rules = []
        for i,ta in enumerate(_TERMS):
            for j,tb in enumerate(_TERMS):
                strength = memberships[a][ta] * memberships[b][tb]
                output = _TERMS[matrix[i][j]]
                aggregate += strength * _OUT[output]
                rules.append(dict(id=f'{key}:{i+1}{j+1}', weight=1.0,
                    antecedents=[dict(variable=k, term=t, degree=memberships[k][t]) for k,t in ((a,ta),(b,tb))],
                    consequent=output, firing_strength=strength))
        if not np.any(aggregate):
            return _empty_result('error', 'insufficient_coverage')
        centroid = float(fuzz.defuzz(_U, aggregate, 'centroid'))
        explanation = f'{label}: {a.replace("_", " ")} ({inputs[a]:g}) and {b.replace("_", " ")} ({inputs[b]:g}) produced {centroid:.2f}/100. Its authored weight is {weight:.0%}; contribution {weight*centroid:.2f} index points.'
        result['components'].append(dict(id=key, label=label, raw_centroid=centroid, weight=weight, contribution=weight*centroid, explanation=explanation, rules=rules, aggregate={'universe': _U.tolist(), 'membership': aggregate.tolist()}))
        result['rules'].extend(rules)
    raw = sum(c['contribution'] for c in result['components'])
    score,category = _round_and_categorize(raw)
    result.update(raw_score=raw, score=score, category=category)
    return result
