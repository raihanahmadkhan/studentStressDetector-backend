# 🧠 Student Stress Detector - Backend API

A high-performance FastAPI backend utilizing a **Mamdani Fuzzy Inference System** to analyze and quantify student stress levels based on lifestyle and academic metrics.

![Python](https://img.shields.io/badge/Python-3.10+-yellow)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-green)
![License](https://img.shields.io/badge/license-MIT-blue)

## 📋 Overview
This API accepts quantitative inputs regarding a student's daily routine (sleep, workload, screen time, etc.) and processes them through a fuzzy logic engine to determine a crisp stress percentage and a qualitative stress label. It is designed to be easily consumed by frontend applications or mobile apps.

## 🛠️ Tech Stack

- **Framework:** FastAPI (Python)
- **Logic Engine:** scikit-fuzzy, NumPy
- **Validation:** Pydantic
- **Server:** Uvicorn (ASGI)

## 🚀 Installation & Setup

### Prerequisites
- Python 3.10 or higher
- pip (Python Package Installer)

### 1. Install Dependencies
Navigate to the project directory and install the required packages:

```bash
pip install -r requirements.txt
````

### 2\. Run the Server

Start the development server using Uvicorn:

```bash
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.

## 📚 API Documentation

### Health Check

**GET** `/api/health`
Verifies that the API and the Fuzzy System are operational.

```json
{
  "status": "healthy",
  "fuzzy_system": "operational"
}
```

### Calculate Stress

**POST** `/api/calculate-stress`
The core endpoint. Processes input parameters to return analysis.

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
    "sleep": { "low": 0.0, "medium": 0.8, "high": 0.2 },
    "workload": { "low": 0.0, "medium": 1.0, "high": 0.0 },
    "screentime": { "low": 0.2, "medium": 0.8, "high": 0.0 },
    "extracurricular": { "low": 0.0, "balanced": 1.0, "excessive": 0.0 },
    "stress": { "low": 0.0, "moderate": 1.0, "high": 0.0 }
  },
  "fuzzy_details": {
    "inference_type": "Mamdani",
    "defuzzification": "centroid",
    "total_rules": 21
  }
}
```

## 🧮 Fuzzy Logic Architecture

The system uses specific membership functions to categorize continuous input data.

### Input Variables

| Variable | Range | Linguistic Terms |
| :--- | :--- | :--- |
| **Sleep Hours** | 0-12h | Poor, Moderate, Good |
| **Academic Workload** | 0-10 | Low, Medium, High |
| **Screen Time** | 0-16h | Low, Moderate, High |
| **Extracurriculars** | 0-10 | Low, Balanced, Excessive |

### Output Variable

| Variable | Range | Linguistic Terms |
| :--- | :--- | :--- |
| **Stress Level** | 0-100% | Very Low, Low, Moderate, High, Very High |

### Inference & Defuzzification

  - **Rules:** 21 defined fuzzy rules (e.g., *If Sleep is Poor AND Workload is High, THEN Stress is Very High*).
  - **Method:** Centroid defuzzification is used to convert the aggregate fuzzy set into a precise numerical value.

## 📂 Project Structure

  - `main.py`: Entry point for the FastAPI application.
  - `requirements.txt`: List of Python dependencies.
  - `.gitignore`: Standard Git ignore rules.

## 🌐 CORS Configuration

Cross-Origin Resource Sharing (CORS) is configured to allow all origins (`*`) by default, facilitating easy integration with frontend applications hosted on different domains.

## 📄 License

This project is licensed under the **MIT License**.

-----

*Built with ❤️ using FastAPI and Fuzzy Logic*

```
```
