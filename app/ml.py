"""Personal next-day strain evaluation. No automatic training or prediction serving."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import numpy as np

POLICY_VERSION = 'next-day-strain-2.0.0'
FEATURES = ['sleep_hours', 'academic_load', 'screen_hours', 'extracurricular_load', 'deadline_pressure', 'recovery', 'reported_strain']
MIN_PAIRS = 120
MIN_SPAN = 150
TEST_DAYS = 14
FOLDS = 3


def cutoff(row):
    return datetime.combine(row['observation_date']+timedelta(days=1), time.min, ZoneInfo(row['timezone'])).astimezone(timezone.utc)


def dataset(revisions):
    """As-of end-of-day features, first timely next-day independent report.

    Revisions recorded after that day's cutoff (including retrospective reports)
    never alter historical features or evaluation labels. Never pool users.
    """
    users = {str(row['user_id']) for row in revisions}
    if len(users) > 1:
        raise ValueError('Personal evaluation cannot pool users')
    days = {}
    for row in revisions:
        if not row['retrospective'] and row['recorded_at'] < cutoff(row):
            days.setdefault(row['observation_date'], []).append(row)
    pairs = []
    for day in sorted(days):
        today = max(days[day], key=lambda r: (r['recorded_at'], r['revision']))
        tomorrow = sorted(days.get(day+timedelta(days=1), []), key=lambda r: (r['recorded_at'], r['revision']))
        tomorrow = [r for r in tomorrow if r['reported_strain'] is not None]
        if any(today.get(name) is None for name in FEATURES) or not tomorrow:
            continue
        target = tomorrow[0]
        # A timezone change must not let a target already known at forecast time
        # masquerade as next-day prediction evidence.
        if target['recorded_at'] < cutoff(today):
            continue
        pairs.append({'date': day, 'decision_at': cutoff(today), 'target_at': target['recorded_at'],
            'x': [float(today[name]) for name in FEATURES], 'y': float(target['reported_strain']),
            'source_revision': today['revision'], 'target_revision': target['revision'],
            'source_questionnaire_version': today.get('questionnaire_version'), 'source_engine_version': today.get('engine_version')})
    return pairs


def ridge(train, test, alpha=10.0):
    x = np.asarray([p['x'] for p in train]); y = np.asarray([p['y'] for p in train])
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale == 0] = 1
    design = np.column_stack([np.ones(len(x)), (x-center)/scale])
    penalty = np.eye(design.shape[1])*alpha
    penalty[0, 0] = 0  # Intercept is not penalized.
    coef = np.linalg.solve(design.T@design+penalty, design.T@y)
    tx = np.column_stack([np.ones(len(test)), (np.asarray([p['x'] for p in test])-center)/scale])
    return np.clip(tx@coef, 0, 10)


def evaluate(pairs):
    span = (pairs[-1]['date']-pairs[0]['date']).days+1 if pairs else 0
    report = {'policy_version': POLICY_VERSION, 'target': 'next-day independently reported strain /10',
        'eligible_pairs': len(pairs), 'calendar_span': span, 'minimum_pairs': MIN_PAIRS,
        'minimum_span': MIN_SPAN, 'serving_enabled': False, 'folds': [],
        'status': 'insufficient_data', 'features': FEATURES, 'candidate': 'standardized ridge, fixed alpha=10'}
    if len(pairs) < MIN_PAIRS or span < MIN_SPAN:
        return report
    differences, fold_wins = [], []
    all_errors = {k: [] for k in ('ridge', 'persistence', 'training_median')}
    for fold in range(FOLDS):
        start = len(pairs)-(FOLDS-fold)*TEST_DAYS
        test = pairs[start:start+TEST_DAYS]
        # One calendar day embargo; training labels must have existed strictly
        # before this held-out block's first source day.
        boundary = test[0]['date']
        train = [p for p in pairs[:start] if p['date']+timedelta(days=1) < boundary and p['target_at'] < test[0]['decision_at']]
        if len(train) < 60:
            report['status'] = 'insufficient_training_folds'
            return report
        y = np.asarray([p['y'] for p in test])
        predictions = {'ridge': ridge(train, test), 'persistence': np.asarray([p['x'][-1] for p in test]),
                       'training_median': np.repeat(np.median([p['y'] for p in train]), len(test))}
        errors = {k: np.abs(v-y) for k, v in predictions.items()}
        metrics = {k: {'mae': float(e.mean()), 'rmse': float(np.sqrt(np.mean((predictions[k]-y)**2)))} for k, e in errors.items()}
        for k in all_errors:
            all_errors[k].extend(errors[k].tolist())
        fold_wins.append(metrics['ridge']['mae'] < min(metrics['persistence']['mae'], metrics['training_median']['mae']))
        report['folds'].append({'train_pairs': len(train), 'test_pairs': len(test),
            'train_last_target_date': str(train[-1]['date']+timedelta(days=1)), 'test_start': str(test[0]['date']),
            'test_end': str(test[-1]['date']), 'metrics': metrics})
    means = {k: float(np.mean(v)) for k, v in all_errors.items()}
    strongest = min(('persistence', 'training_median'), key=lambda k: means[k])
    differences = np.asarray(all_errors[strongest])-np.asarray(all_errors['ridge'])
    # Moving-block bootstrap preserves short-range dependence; no IID-day claim.
    rng = np.random.default_rng(42)
    boot = []
    for _ in range(1000):
        starts = rng.integers(0, len(differences)-6, size=6)
        boot.append(float(np.mean(np.concatenate([differences[s:s+7] for s in starts]))))
    lower, upper = np.quantile(boot, [.025, .975])
    report.update({'mae': means, 'strongest_baseline': strongest, 'improvement_ci95': [float(lower), float(upper)],
                   'gate': '10% lower MAE than both baselines, every fold improves, positive 7-day-block bootstrap lower bound'})
    passed = all(fold_wins) and means['ridge'] < .9*means[strongest] and lower > 0
    report['status'] = 'candidate_passed_review_required' if passed else 'baseline_not_beaten'
    return report
