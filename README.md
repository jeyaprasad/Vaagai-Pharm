# Uyir Score - AI-Based Disease Risk Prediction Using Clinical Data Analytics

## Overview
Diabetes is often diagnosed late, after metabolic damage or microvascular complications have already begun. Uyir Score is a lightweight, privacy-preserving clinical screening and analytics platform that identifies elevated diabetes risk early using routine clinical measurements.

A Random Forest classifier trained on the Pima Indians Diabetes dataset estimates individualized diabetes risk from fasting glucose, body mass index (BMI), age, blood pressure, and related clinical metrics. Attributable risk factors are extracted via what-if counterfactual analysis, and a built-in clinical guidance engine generates clear, plain-language explanations and actionable preventive lifestyle guidance. If a Groq API key is configured, it uses that for enhanced explanations.

For healthcare organisations and clinicians, an interactive Provider Dashboard provides cohort-level analytics, live assessment tracking, clinical use cases, and an illustrative early-screening savings estimator.

---

## Project Structure

```
viral-health/
├── app.py                  # Flask web server (entry point)
├── requirements.txt        # Python dependencies
├── .env                    # Secret keys — never committed (see below)
├── .gitignore
├── README.md
│
├── ml/                     # Machine learning artefacts & training code
│   ├── train_model.py      # Training script
│   ├── model.joblib        # Trained model bundle
│   ├── metrics.json        # Holdout & OOF evaluation metrics
│   └── data/
│       └── diabetes.csv    # Raw Pima Indians Diabetes dataset
│
├── static/                 # Static assets served by Flask
│   └── logo.png
│
├── templates/              # HTML templates served by Flask
│   └── index.html          # Single-page frontend application
│
└── instance/               # Runtime data — never committed
    └── uyirscore.db        # SQLite assessment log (auto-created)
```

---

## Limitations
- **Dataset Specificity:** The classification model is trained on the Pima Indians Diabetes dataset (768 adult females of Pima heritage, aged 21 and older). Risk patterns may not generalize accurately to other ethnic groups, male patients, or broader demographic cohorts.
- **Demo Data:** All baseline analytics, cohort figures, and savings scenarios are illustrative demo figures. They must not be treated as actual institutional, financial, or population health forecasts.
- **Screening Tool Only:** Uyir Score is a statistical risk screening tool, not a diagnostic medical device. It should never substitute for formal clinical evaluations, laboratory diagnostics, or professional clinician judgment.

---

## Step-by-Step Run Instructions (Windows PowerShell)

1. **Create the Python virtual environment:**
   ```powershell
   python -m venv venv
   ```

2. **Allow script execution for the current session (if blocked by execution policy):**
   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   ```

3. **Activate the virtual environment:**
   ```powershell
   .\venv\Scripts\activate
   ```

4. **Install required dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

5. **Train or verify the model and metrics (optional — model and metrics are pre-packaged):**
   ```powershell
   python ml/train_model.py
   ```

6. **Configure environment variables:**
   Create a `.env` file in the project root folder with the following placeholders:
   ```env
   GROQ_API_KEY=gsk_your_groq_api_key_here
   GROQ_MODEL=openai/gpt-oss-120b
   ```
   *(Replace `gsk_your_groq_api_key_here` with your valid Groq API key. If omitted, Uyir Score gracefully defaults to the built-in clinical explanation fallback).*

7. **Start the backend server:**
   ```powershell
   python app.py
   ```

8. **Access the application:**
   Open your browser and navigate to:
   ```
   http://127.0.0.1:5000
   ```
