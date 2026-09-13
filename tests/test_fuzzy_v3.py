"""Current hierarchical contract plus legacy provenance regression."""
import copy
import itertools
import json
from hashlib import sha256
from uuid import uuid4
import pytest
from sqlalchemy import select
from app import fuzzy, fuzzy_v2
from app.models import CheckInRevision, EngineVersion
from app.schemas import RoutineInputs
from app.ml import dataset
from test_checkins import signed_in, save, payload

BASE = dict(sleep_hours=6, academic_load=5, deadline_pressure=5, screen_hours=8, extracurricular_load=5, recovery=5, reported_strain=5)

@pytest.mark.parametrize('name,maximum,step', [('sleep_hours',12,.25),('screen_hours',16,.25),('academic_load',10,1),('deadline_pressure',10,1),('extracurricular_load',10,1),('recovery',10,1),('reported_strain',10,1)])
def test_all_questionnaire_boundaries(name, maximum, step):
    for v in (0,maximum):
        assert getattr(RoutineInputs(**{**BASE,name:v}),name) == v
    for v in (-step,maximum+step,True,'5',None):
        if name == 'reported_strain' and v is None:
            continue
        with pytest.raises(ValueError):
            RoutineInputs(**{**BASE,name:v})


def test_cartesian_corners_have_complete_component_coverage():
    for levels in itertools.product((0,1), repeat=6):
        inputs = {k:fuzzy._RANGES[k]*level for k,level in zip(fuzzy._RANGES,levels)}
        result=fuzzy.evaluate(inputs)
        assert result['status']=='ok' and len(result['rules'])==27
        for component in result['components']:
            assert len(component['rules'])==9
            assert sum(r['firing_strength']>0 for r in component['rules'])==1
            assert any(component['aggregate']['membership'])
        assert result['raw_score']==pytest.approx(sum(c['contribution'] for c in result['components']))
        assert result['raw_centroid'] is None


def test_every_supported_input_value_has_membership_and_every_rule_activates():
    # Coverage follows from positive partition coverage + the complete 3x3 matrices.
    for name,maximum in fuzzy._RANGES.items():
        step=.25 if name.endswith('hours') else 1
        for i in range(int(maximum/step)+1):
            result=fuzzy.evaluate({**BASE,name:i*step})
            assert result['status']=='ok'
            assert max(result['memberships'][name].values()) > 0
    activated=set()
    for key,label,a,b,weight,matrix in fuzzy._MODULES:
        for x,y in itertools.product((0,.5,1),repeat=2):
            result=fuzzy.evaluate({**BASE,a:x*fuzzy._RANGES[a],b:y*fuzzy._RANGES[b]})
            activated.update(r['id'] for r in result['rules'] if r['firing_strength']>0)
    assert len(activated)==27


def test_deterministic_independent_outcome_and_spec_identity():
    result=fuzzy.evaluate(BASE)
    assert result==fuzzy.evaluate(dict(reversed(list(BASE.items()))))
    for strain in (None,0,10):
        assert fuzzy.evaluate({**BASE,'reported_strain':strain})==result
    assert result['score']==50
    assert result['spec_hash']==sha256(json.dumps(fuzzy.MODEL_SPEC,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    original=copy.deepcopy(fuzzy.MODEL_SPEC)
    try:
        fuzzy.MODEL_SPEC['inputs']['sleep_hours']['terms']['low']=[0,0,1]
        assert fuzzy.evaluate(BASE)==result
    finally:
        fuzzy.MODEL_SPEC.clear(); fuzzy.MODEL_SPEC.update(original)
    assert fuzzy.evaluate({**BASE,'recovery':None})['reason']=='missing_current_model_inputs'


def test_component_separation_and_actual_explanations():
    before=fuzzy.evaluate(BASE)
    after=fuzzy.evaluate({**BASE,'deadline_pressure':10})
    assert after['components'][0]['raw_centroid']>before['components'][0]['raw_centroid']
    assert after['components'][1:]==before['components'][1:]
    assert 'deadline pressure (10)' in after['components'][0]['explanation']
    assert fuzzy.evaluate({**BASE,'sleep_hours':12,'recovery':10})['score']<before['score']

@pytest.mark.parametrize('field,value', [('sleep_hours',12.25),('screen_hours',16.25),('deadline_pressure',None),('deadline_pressure',11),('deadline_pressure',2.5),('recovery',-1),('recovery',True)])
def test_new_api_validation_rejects_without_writes(signed_in,field,value):
    client,_=signed_in
    data=payload(); data[field]=value
    assert save(client,data).status_code==422
    assert client.get('/api/check-ins').json()['items']==[]


def test_legacy_revision_export_and_scenario_are_not_reinterpreted(signed_in,db):
    client,_=signed_in
    saved=save(client).json()
    revision=db.scalar(select(CheckInRevision))
    legacy=fuzzy_v2.evaluate(saved['inputs'])
    db.add(EngineVersion(version=fuzzy_v2.MODEL_VERSION,kind='fuzzy',spec_hash=fuzzy_v2.SPEC_HASH,specification=fuzzy_v2.MODEL_SPEC,status='active'))
    db.flush()
    revision.questionnaire_version='check-in-1.0.0';revision.engine_version=fuzzy_v2.MODEL_VERSION
    revision.deadline_pressure=None;revision.recovery=None;revision.assessment=legacy
    db.commit()
    old=client.get('/api/check-ins/'+saved['id']).json()
    assert old['inputs']['deadline_pressure'] is None
    assert old['assessment']['model_version']=='fuzzy-2.0.0'
    scenario=client.post('/api/scenarios',json={'inputs':BASE,'reference_id':saved['id']}).json()
    assert scenario['difference'] is None and scenario['reference']['assessment']['status']=='unsupported'
    assert client.get('/api/check-ins/'+saved['id']).json()==old
    edit=client.patch('/api/check-ins/'+saved['id'],json={**BASE,'expected_revision':1},headers={'Idempotency-Key':str(uuid4())})
    assert edit.status_code==200,edit.text
    assert edit.json()['questionnaire_version']=='check-in-2.0.0'
    assert edit.json()['assessment']['model_version']=='fuzzy-3.1.0'
    db.expire_all()
    assert db.scalar(select(CheckInRevision).where(CheckInRevision.revision==1)).assessment==legacy
    exported=client.get('/api/data/export')
    assert exported.status_code==200 and 'fuzzy-2.0.0' in exported.text and 'fuzzy-3.1.0' in exported.text
    assert 'deadline_pressure' in exported.text and 'recovery' in exported.text


def test_missing_legacy_features_are_not_imputed_for_ml():
    from test_ml import row
    from datetime import date,timedelta
    day=date(2026,1,1)
    assert dataset([row(day,deadline_pressure=None),row(day+timedelta(days=1))])==[]

@pytest.mark.parametrize('name', list(fuzzy._RANGES))
def test_direction_consistency_across_component_input_space(name):
    import numpy as np
    module=next(m for m in fuzzy._MODULES if name in m[2:4])
    other=module[3] if name==module[2] else module[2]
    direction=-1 if module[0]=='recovery_deficit' else 1
    for fixed in np.linspace(0,fuzzy._RANGES[other],5):
        previous=None
        for value in np.linspace(0,fuzzy._RANGES[name],41):
            result=fuzzy.evaluate({**BASE,name:float(value),other:float(fixed)})
            current=result['raw_score']
            if previous is not None:
                assert direction*(current-previous)>=-1e-9
            previous=current
            for c in result['components']:
                assert sum(r['firing_strength'] for r in c['rules'])==pytest.approx(1)
                assert min(c['aggregate']['membership'])>=0
                assert max(c['aggregate']['membership'])<=1+1e-12


def test_product_rule_activation_scaled_sum_and_centroid():
    result=fuzzy.evaluate({**BASE,'academic_load':2.5,'deadline_pressure':7.5})
    component=result['components'][0]
    active=[r for r in component['rules'] if r['firing_strength']>0]
    assert len(active)==4 and all(r['firing_strength']==.25 for r in active)
    # Outputs medium/high/medium/high: identical-area symmetric sets at 50/85.
    assert component['raw_centroid']==pytest.approx(67.5)
    assert component['contribution']==pytest.approx(.45*67.5)


def test_reflection_bundle_uses_saved_component_facts(signed_in,db):
    from app.reflections import ReflectionRequest
    from app.reflection_facts import bundle
    client,user=signed_in
    saved=save(client).json()
    facts=bundle(db,user,ReflectionRequest(kind='checkin',checkin_id=saved['id'],expected_history_version=1))
    components={f['source']['component_id']: f for f in facts['facts'] if 'component_id' in f['source']}
    assert set(components)=={'academic_pressure','recovery_deficit','contextual_pressure'}
    for c in saved['assessment']['components']:
        assert components[c['id']]['text']==c['explanation']
        assert components[c['id']]['source']['model_version']=='fuzzy-3.1.0'
    assert len({f['id'] for f in facts['facts']})==len(facts['facts'])


def test_analytics_new_fields_count_missing_legacy_as_missing():
    from app.analytics import summarize
    from datetime import date,timedelta
    end=date(2026,9,12)
    observations=[{'observation_date':end-timedelta(days=i),'inputs':{**BASE,'deadline_pressure':None,'recovery':None}} for i in range(28)]
    for row in observations[:4]:
        row['inputs']['deadline_pressure']=8
        row['inputs']['recovery']=2
    result=summarize(observations,end)
    for metric in result['metrics']:
        if metric['metric'] in ('deadline_pressure','recovery'):
            assert metric['recent_count']==4 and metric['baseline_count']==0
            assert metric['difference'] is None and metric['status']=='insufficient_data'
    assert result['series'][-1]['rolling_mean']['deadline_pressure']==8
    assert result['series'][0]['rolling_mean']['recovery'] is None
