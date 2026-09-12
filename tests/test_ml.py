from datetime import date, datetime, timedelta, timezone
import numpy as np
import pytest
from app.ml import dataset, evaluate, ridge


def row(day, **changes):
    return {'user_id': 'one-user', 'observation_date': day, 'timezone': 'UTC', 'revision': 1,
        'recorded_at': datetime(day.year, day.month, day.day, 20, tzinfo=timezone.utc),
        'retrospective': False, 'sleep_hours': 7, 'academic_load': 5, 'screen_hours': 6,
        'extracurricular_load': 4, 'deadline_pressure': 5, 'recovery': 5, 'reported_strain': 5, **changes}


def test_late_revisions_and_retrospective_reports_cannot_leak():
    day = date(2026, 1, 1)
    records = [row(day), row(day+timedelta(days=1), reported_strain=6)]
    before = dataset(records)
    records += [row(day, revision=2, sleep_hours=0, recorded_at=datetime(2026, 2, 1, tzinfo=timezone.utc)),
                row(day+timedelta(days=1), revision=2, reported_strain=10, recorded_at=datetime(2026, 2, 1, tzinfo=timezone.utc)),
                row(day+timedelta(days=2), retrospective=True)]
    assert dataset(records) == before
    assert before[0]['x'] == [7, 5, 6, 4, 5, 5, 5] and before[0]['y'] == 6


def test_uses_latest_known_features_first_timely_target_and_real_zero():
    day = date(2026, 1, 1)
    records = [row(day), row(day, revision=2, sleep_hours=8, recorded_at=datetime(2026, 1, 1, 21, tzinfo=timezone.utc)),
               row(day+timedelta(days=1), reported_strain=0),
               row(day+timedelta(days=1), revision=2, reported_strain=9, recorded_at=datetime(2026, 1, 2, 21, tzinfo=timezone.utc))]
    result = dataset(records)
    assert result[0]['source_revision'] == 2 and result[0]['target_revision'] == 1
    assert result[0]['x'][0] == 8 and result[0]['y'] == 0


def test_missing_days_labels_and_cross_user_pooling():
    day = date(2026, 1, 1)
    assert dataset([row(day), row(day+timedelta(days=2))]) == []
    assert dataset([row(day), row(day+timedelta(days=1), reported_strain=None)]) == []
    with pytest.raises(ValueError, match='pool users'):
        dataset([row(day), row(day, user_id='other-user')])


def test_insufficient_real_data_never_fits(monkeypatch):
    import app.ml as ml
    monkeypatch.setattr(ml, 'ridge', lambda *args: pytest.fail('Must not train'))
    result = evaluate([])
    assert result['status'] == 'insufficient_data' and not result['serving_enabled']


def test_temporal_embargo_and_baseline_gate_on_synthetic_pipeline_fixture():
    # This fixture tests mechanics, never claims model accuracy on real students.
    start = date(2025, 1, 1)
    pairs = dataset([row(start+timedelta(days=i), reported_strain=5) for i in range(180)])
    report = evaluate(pairs)
    assert len(report['folds']) == 3
    assert report['status'] == 'baseline_not_beaten'
    assert report['mae']['persistence'] == 0 and not report['serving_enabled']
    for fold in report['folds']:
        assert fold['train_last_target_date'] < fold['test_start']
        assert fold['test_pairs'] == 14 and fold['train_pairs'] >= 60


def test_test_labels_cannot_affect_scaling_or_fit():
    train = [{'x': [i, 5, 4, 3, 2], 'y': i/10} for i in range(60)]
    test = [{'x': [20, 5, 4, 3, 2], 'y': 0}]
    prediction = ridge(train, test)
    test[0]['y'] = 10
    np.testing.assert_array_equal(prediction, ridge(train, test))
    assert np.isfinite(prediction).all() and 0 <= prediction[0] <= 10
