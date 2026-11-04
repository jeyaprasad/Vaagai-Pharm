"""Train the UyirScore Random Forest on the Pima Indians Diabetes dataset.
Run from project root: python ml/train_model.py
Outputs: ml/model.joblib, ml/metrics.json  (reads from ml/data/diabetes.csv)
"""
import json, os, urllib.request
import numpy as np, pandas as pd, joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_predict
from sklearn.metrics import accuracy_score, roc_auc_score, precision_score, recall_score

HERE     = os.path.dirname(os.path.abspath(__file__))   # ml/
DATA_DIR = os.path.join(HERE, "data")
os.makedirs(DATA_DIR, exist_ok=True)
FEATS = ["Pregnancies", "Glucose", "BloodPressure", "SkinThickness", "Insulin", "BMI", "DiabetesPedigreeFunction", "Age"]
COLS = FEATS + ["Outcome"]
MISSING_AS_ZERO = ["Glucose", "BloodPressure", "SkinThickness", "Insulin", "BMI"]   # 0 means "not measured" here
CUTS = [0.35, 0.65]                                                                 # Low | Moderate | High
URLS = ["https://raw.githubusercontent.com/jbrownlee/Datasets/master/pima-indians-diabetes.data.csv",
        "https://raw.githubusercontent.com/plotly/datasets/master/diabetes.csv"]


def load():
    path = os.path.join(DATA_DIR, "diabetes.csv")
    if not os.path.exists(path):
        for u in URLS:
            try:
                urllib.request.urlretrieve(u, path); break
            except Exception as e:
                print("download failed:", u, e)
        else:
            raise SystemExit("Download failed. Save the Pima dataset as diabetes.csv next to this file.")
    df = pd.read_csv(path, header=None, names=COLS).apply(pd.to_numeric, errors="coerce").dropna()
    return df.reset_index(drop=True)


def band(p):
    return "Low" if p < CUTS[0] else "Moderate" if p < CUTS[1] else "High"


df = load()
df[MISSING_AS_ZERO] = df[MISSING_AS_ZERO].replace(0, np.nan)
med = df[FEATS].median()
df = df.fillna(med)
X, y = df[FEATS], df["Outcome"].astype(int)
rf = lambda: RandomForestClassifier(n_estimators=300, max_depth=6, min_samples_leaf=3, class_weight="balanced", random_state=42)

Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, stratify=y, random_state=42)
m = rf().fit(Xtr, ytr)
pred, prob = m.predict(Xte), m.predict_proba(Xte)[:, 1]
metrics = {"accuracy": accuracy_score(yte, pred), "roc_auc": roc_auc_score(yte, prob),
           "precision": precision_score(yte, pred), "recall": recall_score(yte, pred)}

# Out-of-fold probabilities for the whole cohort -> honest numbers for the provider dashboard
oof = cross_val_predict(rf(), X, y, cv=5, method="predict_proba")[:, 1]
df["risk"], df["band"] = oof, [band(p) for p in oof]
bands = [{"band": b, "count": int((df.band == b).sum()), "observed_rate": float(df[df.band == b].Outcome.mean())}
         for b in ["Low", "Moderate", "High"] if (df.band == b).any()]
df["age_group"] = pd.cut(df.Age, [0, 29, 39, 49, 120], labels=["20-29", "30-39", "40-49", "50+"])
ages = [{"group": g, "n": int(len(s)), "avg_risk": float(s.risk.mean()), "observed_rate": float(s.Outcome.mean())}
        for g, s in df.groupby("age_group", observed=True)]

final = rf().fit(X, y)   # production model uses all rows
joblib.dump({"model": final, "medians": med, "ref": df[df.Outcome == 0][FEATS].median(), "feats": FEATS}, os.path.join(HERE, "model.joblib"))
json.dump({"n": len(df), "prevalence": float(y.mean()), "metrics": metrics, "cuts": CUTS, "bands": bands, "age_groups": ages,
           "importances": dict(zip(FEATS, map(float, final.feature_importances_)))},
          open(os.path.join(HERE, "metrics.json"), "w"), indent=2)
print(f"Trained on {len(df)} patients | holdout accuracy {metrics['accuracy']:.2f} | ROC-AUC {metrics['roc_auc']:.2f}")
