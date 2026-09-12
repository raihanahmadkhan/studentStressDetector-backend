"""Targeted local measurements, not a production capacity claim."""
from datetime import date, timedelta
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from app import fuzzy
from test_checkins import signed_in, save, payload


def measure(call, repetitions):
    call()
    samples = []
    for _ in range(repetitions):
        started = perf_counter()
        call()
        samples.append((perf_counter()-started)*1000)
    return {'samples': repetitions, 'p50_ms': round(float(np.median(samples)), 3),
            'p95_ms': round(float(np.quantile(samples, .95)), 3), 'max_ms': round(max(samples), 3)}


def test_targeted_core_latency_on_disposable_data(signed_in):
    client, _ = signed_in
    inputs = {'sleep_hours': 7, 'academic_load': 5, 'screen_hours': 6, 'extracurricular_load': 4, 'deadline_pressure': 5, 'recovery': 5, 'reported_strain': 5}
    for ago in range(1, 29):
        data = payload(); data['observation_date'] = str(date.today()-timedelta(days=ago))
        assert save(client, data).status_code == 201
    def read(path):
        response = client.get(path)
        assert response.status_code == 200
    def scenario():
        assert client.post('/api/scenarios', json={'inputs': inputs}).status_code == 200
    results = {'environment': 'Local FastAPI TestClient and real dedicated PostgreSQL; sequential calls; 28 synthetic daily observations',
        'fuzzy': measure(lambda: fuzzy.evaluate(inputs), 200),
        'history_28_days': measure(lambda: read('/api/check-ins'), 20),
        'patterns_28_days': measure(lambda: read('/api/patterns'), 20),
        'scenario': measure(scenario, 20)}
    root = Path(__file__).resolve().parents[1]/'.local'/'verification'
    root.mkdir(parents=True, exist_ok=True)
    (root/'performance-report.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    # Generous local regression budgets avoid claiming a cloud SLA.
    assert results['fuzzy']['p95_ms'] < 100
    assert max(results[key]['p95_ms'] for key in ('history_28_days', 'patterns_28_days', 'scenario')) < 1000
