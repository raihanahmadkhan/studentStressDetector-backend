from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import numpy as np
import skfuzzy as fuzz
from skfuzzy import control as ctrl

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class StressInput(BaseModel):
    sleep: float  
    workload: float  
    screentime: float  
    extracurricular: float

class StressOutput(BaseModel):
    stress_percentage: float
    stress_label: str
    membership_degrees: dict
    input_values: dict
    fuzzy_details: dict

def create_fuzzy_system():
    sleep = ctrl.Antecedent(np.arange(0, 13, 0.1), 'sleep')
    workload = ctrl.Antecedent(np.arange(0, 11, 0.1), 'workload')
    screentime = ctrl.Antecedent(np.arange(0, 17, 0.1), 'screentime')
    extracurricular = ctrl.Antecedent(np.arange(0, 11, 0.1), 'extracurricular')
    stress = ctrl.Consequent(np.arange(0, 101, 1), 'stress')
    sleep['poor'] = fuzz.trapmf(sleep.universe, [0, 0, 4, 6])
    sleep['moderate'] = fuzz.trimf(sleep.universe, [5, 6.5, 8])
    sleep['good'] = fuzz.trapmf(sleep.universe, [7, 8.5, 12, 12])
    workload['low'] = fuzz.trapmf(workload.universe, [0, 0, 2, 4])
    workload['medium'] = fuzz.trimf(workload.universe, [3, 5, 7])
    workload['high'] = fuzz.trapmf(workload.universe, [6, 8, 10, 10])
    screentime['low'] = fuzz.trapmf(screentime.universe, [0, 0, 2, 4])
    screentime['moderate'] = fuzz.trimf(screentime.universe, [3, 6, 9])
    screentime['high'] = fuzz.trapmf(screentime.universe, [8, 12, 16, 16])
    extracurricular['low'] = fuzz.trapmf(extracurricular.universe, [0, 0, 1, 3])
    extracurricular['balanced'] = fuzz.trimf(extracurricular.universe, [2, 5, 8])
    extracurricular['excessive'] = fuzz.trapmf(extracurricular.universe, [7, 9, 10, 10])
    stress['very_low'] = fuzz.trapmf(stress.universe, [0, 0, 10, 25])
    stress['low'] = fuzz.trimf(stress.universe, [15, 30, 45])
    stress['moderate'] = fuzz.trimf(stress.universe, [35, 50, 65])
    stress['high'] = fuzz.trimf(stress.universe, [55, 70, 85])
    stress['very_high'] = fuzz.trapmf(stress.universe, [75, 90, 100, 100])
    rules = [
        ctrl.Rule(sleep['good'] & workload['low'] & screentime['low'] & extracurricular['balanced'], stress['very_low']),
        ctrl.Rule(sleep['good'] & workload['low'] & screentime['moderate'] & extracurricular['balanced'], stress['low']),
        ctrl.Rule(sleep['good'] & workload['high'] & screentime['low'], stress['moderate']),
        ctrl.Rule(sleep['good'] & workload['high'] & screentime['high'], stress['high']),
        ctrl.Rule(sleep['poor'] & workload['low'] & screentime['low'], stress['moderate']),
        ctrl.Rule(sleep['poor'] & workload['medium'], stress['high']),
        ctrl.Rule(sleep['poor'] & workload['high'], stress['very_high']),
        ctrl.Rule(sleep['poor'] & screentime['high'], stress['very_high']),
        ctrl.Rule(sleep['moderate'] & workload['low'] & screentime['low'] & extracurricular['balanced'], stress['low']),
        ctrl.Rule(sleep['moderate'] & workload['medium'] & screentime['moderate'], stress['moderate']),
        ctrl.Rule(sleep['moderate'] & workload['high'] & screentime['high'], stress['high']),
        ctrl.Rule(screentime['high'] & workload['high'], stress['very_high']),
        ctrl.Rule(screentime['high'] & sleep['poor'], stress['very_high']),
        ctrl.Rule(screentime['high'] & workload['medium'] & sleep['moderate'], stress['high']),
        ctrl.Rule(extracurricular['excessive'] & workload['high'], stress['very_high']),
        ctrl.Rule(extracurricular['excessive'] & sleep['poor'], stress['very_high']),
        ctrl.Rule(extracurricular['low'] & workload['low'] & sleep['good'], stress['low']),
        ctrl.Rule(sleep['moderate'] & workload['medium'] & screentime['low'] & extracurricular['balanced'], stress['low']),
        ctrl.Rule(sleep['good'] & workload['medium'] & screentime['moderate'] & extracurricular['balanced'], stress['moderate']),
        ctrl.Rule(workload['low'] & screentime['low'] & extracurricular['low'], stress['low']),
        ctrl.Rule(workload['high'] & screentime['moderate'] & sleep['moderate'], stress['high']),
    ]
    stress_ctrl = ctrl.ControlSystem(rules)
    stress_simulation = ctrl.ControlSystemSimulation(stress_ctrl)
    return stress_simulation, sleep, workload, screentime, extracurricular, stress
fuzzy_system, sleep_var, workload_var, screentime_var, extracurricular_var, stress_var = create_fuzzy_system()
def get_membership_degrees(value, variable):
    """Calculate membership degrees for a given value across all fuzzy sets"""
    degrees = {}
    for label in variable.terms:
        degrees[label] = float(fuzz.interp_membership(variable.universe, variable[label].mf, value))
    return degrees
def get_stress_label(stress_value):
    """Determine stress label based on value"""
    if stress_value < 25:
        return "Very Low Stress"
    elif stress_value < 45:
        return "Low Stress"
    elif stress_value < 65:
        return "Moderate Stress"
    elif stress_value < 85:
        return "High Stress"
    else:
        return "Very High Stress"
@app.post("/api/calculate-stress", response_model=StressOutput)
async def calculate_stress(data: StressInput):
    simulation, sleep_v, workload_v, screentime_v, extracurricular_v, stress_v = create_fuzzy_system()
    simulation.input['sleep'] = data.sleep
    simulation.input['workload'] = data.workload
    simulation.input['screentime'] = data.screentime
    simulation.input['extracurricular'] = data.extracurricular
    simulation.compute()
    stress_value = simulation.output['stress']
    stress_label = get_stress_label(stress_value)
    sleep_degrees = get_membership_degrees(data.sleep, sleep_v)
    workload_degrees = get_membership_degrees(data.workload, workload_v)
    screentime_degrees = get_membership_degrees(data.screentime, screentime_v)
    extracurricular_degrees = get_membership_degrees(data.extracurricular, extracurricular_v)
    stress_degrees = get_membership_degrees(stress_value, stress_v)
    return StressOutput(
        stress_percentage=round(stress_value, 2),
        stress_label=stress_label,
        membership_degrees={
            "sleep": sleep_degrees,
            "workload": workload_degrees,
            "screentime": screentime_degrees,
            "extracurricular": extracurricular_degrees,
            "stress": stress_degrees
        },
        input_values={
            "sleep": data.sleep,
            "workload": data.workload,
            "screentime": data.screentime,
            "extracurricular": data.extracurricular
        },
        fuzzy_details={
            "inference_type": "Mamdani",
            "defuzzification": "centroid",
            "total_rules": 21
        }
    )
@app.get("/")
async def root():
    return {"message": "Student Stress Detector API - Fuzzy Logic System"}
@app.get("/api/health")
async def health():
    return {"status": "healthy", "fuzzy_system": "operational"}
