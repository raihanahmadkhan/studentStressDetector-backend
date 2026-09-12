"""Calendar-based descriptive policy. Missing reports never become zero."""
from datetime import timedelta
from statistics import median, mean

POLICY_VERSION = 'personal-patterns-2.0.0'
METRICS = {'sleep_hours': 1, 'screen_hours': 2, 'academic_load': 2,
           'extracurricular_load': 2, 'deadline_pressure': 2, 'recovery': 2, 'reported_strain': 2}


def summarize(observations, end):
    days = {row['observation_date']: row['inputs'] for row in observations}
    baseline_dates = [end - timedelta(days=i) for i in range(27, 6, -1)]
    recent_dates = [end - timedelta(days=i) for i in range(6, -1, -1)]
    windows = {'baseline': [baseline_dates[0].isoformat(), baseline_dates[-1].isoformat()],
               'recent': [recent_dates[0].isoformat(), end.isoformat()]}
    metrics = []
    for metric, minimum in METRICS.items():
        def values(dates):
            return [days[d].get(metric) for d in dates if d in days and days[d].get(metric) is not None]
        baseline, recent = values(baseline_dates), values(recent_dates)
        enough = len(baseline) >= 10 and len(recent) >= 4
        center = median(baseline) if len(baseline) >= 10 else None
        mad = median([abs(v-center) for v in baseline]) if center is not None else None
        threshold = max(minimum, 2.5 * 1.4826 * mad) if mad is not None else None
        def direction(d):
            value = days.get(d, {}).get(metric)
            if value is None or center is None or abs(value-center) < threshold:
                return None
            return 'higher' if value > center else 'lower'
        latest = direction(end)
        persistent = latest is not None and all(direction(end-timedelta(days=i)) == latest for i in range(3))
        metrics.append({'id': f'{POLICY_VERSION}:{end}:{metric}', 'metric': metric,
            'baseline_count': len(baseline), 'recent_count': len(recent),
            'baseline_median': center, 'recent_median': median(recent) if len(recent) >= 4 else None,
            'difference': median(recent)-center if enough else None,
            'status': 'available' if enough else 'insufficient_data', 'mad': mad,
            'threshold': threshold, 'latest_deviation': latest, 'persistent_deviation': latest if persistent else None,
            'rule': '4 of 7 recent and 10 of 21 baseline reports; median difference; deviation >= max(minimum, 2.5 × 1.4826 × MAD)',
            'limitation': 'Describes recorded observations only; missing days may bias the comparison.'})
    by_metric = {item['metric']: item for item in metrics}
    cooccurrence = (by_metric['sleep_hours']['latest_deviation'] == 'lower'
                    and by_metric['academic_load']['latest_deviation'] == 'higher')
    series = []
    for d in baseline_dates + recent_dates:
        rolling_dates = [d-timedelta(days=i) for i in range(7)]
        rolling = {}
        for metric in METRICS:
            samples = [days[t].get(metric) for t in rolling_dates if t in days and days[t].get(metric) is not None]
            rolling[metric] = mean(samples) if len(samples) >= 4 else None
        series.append({'date': d.isoformat(), 'inputs': days.get(d), 'rolling_mean': rolling})
    return {'policy_version': POLICY_VERSION, 'windows': windows, 'metrics': metrics, 'series': series,
            'cooccurrence': 'Lower sleep and higher academic demand were recorded together on the selected end date. This does not establish causation.' if cooccurrence else None}
