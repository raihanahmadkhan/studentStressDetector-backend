# 🧠 Student Stress Detector - Backend API

FastAPI backend using Mamdani Fuzzy Inference System to analyze student stress levels.

![Python](https://img.shields.io/badge/Python-3.10+-yellow)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-green)

## 🚀 Quick Start

### Local Development

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the server:**
   ```bash
   uvicorn main:app --reload --port 8000
   ```

3. **Test the API:**
   - Health check: http://localhost:8000/api/health
   - API docs: http://localhost:8000/docs
   - Calculate stress: POST to http://localhost:8000/api/calculate-stress

### Deploy to Render

1. **Push to GitHub:**
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/stressed-backend.git
   git push -u origin main
   ```

2. **Deploy on Render:**
   - Go to [Render Dashboard](https://dashboard.render.com)
   - Click "New +" → "Web Service"
   - Connect your GitHub repository
   - Render will auto-detect `render.yaml` and configure everything
   - Click "Create Web Service"

3. **Done!** Your API will be live at: `https://your-app-name.onrender.com`

## 📊 API Endpoints

### GET `/`
Returns API information.

**Response:**
```json
{
  "message": "Student Stress Detector API - Fuzzy Logic System"
}
```

### GET `/api/health`
Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "fuzzy_system": "operational"
}
```

### POST `/api/calculate-stress`
Calculate stress level based on input parameters.

**Request Body:**
```json
{
  "sleep": 7,
  "workload": 5,
  "screentime": 6,
  "extracurricular": 5
}
```

**Response:**
```json
{
  "stress_percentage": 50.0,
  "stress_label": "Moderate Stress",
  "membership_degrees": {
    "sleep": {...},
    "workload": {...},
    "screentime": {...},
    "extracurricular": {...},
    "stress": {...}
  },
  "input_values": {...},
  "fuzzy_details": {
    "inference_type": "Mamdani",
    "defuzzification": "centroid",
    "total_rules": 21
  }
}
```

## 🧮 Fuzzy Logic System

### Input Variables
- **Sleep Hours** (0-12h): Poor, Moderate, Good
- **Academic Workload** (0-10): Low, Medium, High
- **Screen Time** (0-16h): Low, Moderate, High
- **Extracurricular Activities** (0-10): Low, Balanced, Excessive

### Output Variable
- **Stress Level** (0-100%): Very Low, Low, Moderate, High, Very High

### Fuzzy Rules (21 total)
Examples:
- Poor sleep + High workload → Very High Stress
- Good sleep + Low workload + Balanced activities → Very Low Stress
- High screentime + High workload → Very High Stress

### Defuzzification
Uses **centroid method** to convert fuzzy output to crisp stress percentage.

## 📦 Tech Stack

- **FastAPI** - Modern Python web framework
- **scikit-fuzzy** - Fuzzy logic toolkit
- **NumPy** - Numerical computing
- **Pydantic** - Data validation
- **Uvicorn** - ASGI server

## 🔧 Configuration Files

- `main.py` - FastAPI application
- `requirements.txt` - Python dependencies
- `runtime.txt` - Python version for deployment
- `render.yaml` - Render deployment configuration
- `Procfile` - Railway deployment configuration
- `.gitignore` - Git ignore rules

## 🌐 CORS

CORS is enabled for all origins (`allow_origins=["*"]`) to allow frontend access from any domain.

## 📄 License

MIT License

---

Built with ❤️ using FastAPI and Fuzzy Logic
