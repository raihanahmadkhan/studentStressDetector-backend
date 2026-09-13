"""Small presentation policy over saved fuzzy traces; never recalculates stress."""
VERSION = 'guidance-1.0.0'
SUPPORTED_MODELS = {'fuzzy-3.0.0', 'fuzzy-3.1.0'}
# Order is the deterministic tie-breaker within each component.
ACTIONS = {
    'academic_pressure': [
        ('deadline_pressure', 'Deadline pressure', 'Prioritize one deadline', 'List your upcoming deadlines, choose the nearest one, and write down its next small task.'),
        ('academic_load', 'Academic workload', 'Make the workload smaller', 'Choose one academic task for your next work block and split it into a step you can finish.'),
    ],
    'recovery_deficit': [
        ('recovery', 'Recovery / relaxation', 'Protect a recovery block', 'Set aside a short break after your next task for an activity you find restful.'),
        ('sleep_hours', 'Sleep duration', 'Make room for sleep', 'Choose a realistic stopping time for work tonight and leave time to wind down before bed.'),
    ],
    'contextual_pressure': [
        ('extracurricular_load', 'Other commitments', 'Review one commitment', 'Pick one nonessential commitment you could postpone or ask someone to help with.'),
        ('screen_hours', 'Screen time', 'Review optional screen use', 'Identify one optional screen activity you could shorten to make room for a break; keep time needed for study or other essentials.'),
    ],
}


def recommend(inputs, assessment):
    if assessment['status'] != 'ok' or assessment['model_version'] not in SUPPORTED_MODELS:
        return []
    suggestions = []
    components = sorted(assessment.get('components', []), key=lambda c: (-c['contribution'], c['id']))
    for component in components:
        candidates = []
        for variable, label, title, action in ACTIONS.get(component['id'], []):
            if inputs.get(variable) is None:
                continue
            adverse = {'low', 'medium'} if component['id'] == 'recovery_deficit' else {'medium', 'high'}
            evidence = [rule for rule in component['rules']
                        if rule['firing_strength'] > 0 and rule['consequent'] in {'medium', 'high'}
                        and any(a['variable'] == variable and a['term'] in adverse for a in rule['antecedents'])]
            if evidence:
                candidates.append((sum(r['firing_strength'] for r in evidence), variable, label, title, action, evidence))
        if not candidates:
            continue
        _, variable, label, title, action, evidence = max(candidates, key=lambda candidate: candidate[0])
        units = 'hours' if variable.endswith('hours') else '/ 10'
        suggestions.append(dict(id=variable, policy_version=VERSION, component_id=component['id'],
            rule_ids=[r['id'] for r in evidence], title=title, action=action,
            reason=f"Your recorded {label.lower()} was {inputs[variable]:g} {units}. It appeared in active rules producing medium or high {component['label'].lower()} in this estimate."))
    if not suggestions and components:
        component = components[0]
        evidence = [r['id'] for r in component['rules'] if r['firing_strength'] > 0 and r['consequent'] == 'low']
        if evidence:
            suggestions.append(dict(id='maintain-routine', policy_version=VERSION, component_id=component['id'],
                rule_ids=evidence, title='Keep what is working',
                action='Keep your current balance of tasks and breaks in mind when planning tomorrow, then check in again to see how it felt.',
                reason='The active component rules produced low outputs for this recorded routine. No specific pressure-reduction step was identified.'))
    return suggestions[:3]
