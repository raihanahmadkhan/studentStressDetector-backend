"""Trusted facts and authored reflection questions. No client prose or calculations."""
import hashlib
import json
from app.checkins import as_response, find_revision
from app.product import patterns
from app.errors import ApiError

PROMPT_VERSION = 'grounded-selection-1.0.0'
LIMITATION = 'These observations and heuristic rules do not establish causes, diagnose conditions, or predict how you will feel.'
QUESTIONS = {
    'routine': 'Which part of this routine would you like to understand better?',
    'sleep': 'What context would help you understand your recorded sleep duration?',
    'workload': 'Which commitments felt most demanding to you?',
    'strain': 'What else about the day would help put your own strain report in context?',
    'missing': 'Would recording a few more days help you reflect on your routines?',
    'pattern': 'Does this recorded difference match your own experience of the period?',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def bundle(db, user, payload):
    facts = []
    def add(text, metric='routine', source=None):
        facts.append({'id': f'F{len(facts)+1:02}', 'text': text, 'question_ids': [metric, 'routine'] if metric != 'routine' else ['routine'], 'source': source or {}})
    if payload.kind == 'checkin':
        row = find_revision(db, user.id, payload.checkin_id)
        if row is None:
            raise ApiError(404, 'not_found', 'Check-in not found.')
        saved = as_response(*row, user.history_version)
        source = {'date': str(saved.observation_date), 'revision': saved.revision, 'model_version': saved.assessment.model_version}
        if saved.inputs.reported_strain is None:
            add('Self-reported strain was skipped. It is unknown and has not been inferred.', 'missing', source)
        else:
            add(f'Your own reported strain was {saved.inputs.reported_strain} out of 10. This is separate from the routine-based index.', 'strain', source)
        add(f'You recorded {saved.inputs.sleep_hours:g} hours of sleep.', 'sleep', source)
        add(f'You recorded academic demand {saved.inputs.academic_load}/10 and other commitments {saved.inputs.extracurricular_load}/10.', 'workload', source)
        assessment = saved.assessment
        if assessment.status != 'ok':
            add(f'The observation was saved, but the heuristic index is unavailable (status: {assessment.status}).', 'routine', source)
        else:
            add(f'The authored heuristic returned {assessment.score:.1f}/100 in the {assessment.category} index band. It is not a probability or a measured stress level.', 'routine', source)
            for component in assessment.components:
                add(component.explanation, 'routine', {**source, 'component_id': component.id})
            active = sorted((r for r in assessment.rules if r.firing_strength > 0), key=lambda r: (-r.firing_strength, r.id))[:3]
            for rule in active:
                terms = ' AND '.join(f'{a.variable.replace("_", " ")} is {a.term} (membership {a.degree:.3f})' for a in rule.antecedents)
                add(f'Rule {rule.id}: {terms}; output {rule.consequent}; firing strength {rule.firing_strength:.3f}. Strength is not confidence or an additive contribution.', 'routine', source)
        context = {'kind': 'checkin', **source}
    else:
        result = patterns(payload.end_date, user, db)
        context = {'kind': 'weekly', 'windows': result['windows'], 'policy_version': result['policy_version']}
        for metric in result['metrics']:
            name = metric['metric'].replace('_', ' ')
            counts = f'{metric["recent_count"]}/7 recent and {metric["baseline_count"]}/21 baseline reports'
            if metric['difference'] is None:
                add(f'{name}: {counts}; not enough data for a comparison (needs at least 4 and 10 reports respectively). Missing days are not zero.', 'missing', context)
            else:
                unit = 'hours' if metric['metric'].endswith('hours') else 'points on a 0–10 scale'
                add(f'{name}: {counts}; recent median {metric["recent_median"]:g}, baseline median {metric["baseline_median"]:g}, difference {metric["difference"]:+g} {unit}. This describes recorded days only.', 'pattern', context)
            if metric['persistent_deviation']:
                add(f'{name} was {metric["persistent_deviation"]} than its baseline median by at least the deviation threshold on three consecutive calendar days ending {result["windows"]["recent"][1]}.', 'pattern', context)
        if result['cooccurrence']:
            add(result['cooccurrence'], 'pattern', context)
    return {'version': PROMPT_VERSION, 'context': context, 'facts': facts,
            'questions': QUESTIONS, 'limitation': LIMITATION}


def render(selection, facts):
    by_id = {f['id']: f for f in facts['facts']}
    ids = [item.evidence_id for item in selection.highlights]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate evidence')
    items = []
    for item in selection.highlights:
        fact = by_id.get(item.evidence_id)
        if fact is None or item.question_id not in fact['question_ids']:
            raise ValueError('Unverified evidence or unrelated reflection')
        items.append({'evidence_id': fact['id'], 'text': fact['text'], 'source': fact['source'],
                      'reflection': QUESTIONS[item.question_id]})
    return {'highlights': items, 'limitation': LIMITATION}
