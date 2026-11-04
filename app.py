"""UyirScore backend.  Run: python app.py  ->  http://127.0.0.1:5000
Optional: set GROQ_API_KEY (and GROQ_MODEL) for LLM explanations; without it a built-in fallback is used."""
import json, os, sqlite3, time, urllib.request
import joblib, pandas as pd
from flask import Flask, jsonify, request, send_from_directory

HERE   = os.path.dirname(os.path.abspath(__file__))
ML_DIR = os.path.join(HERE, "ml")
INST   = os.path.join(HERE, "instance")
os.makedirs(INST, exist_ok=True)

# Load .env file if present
_env_path = os.path.join(HERE, ".env")
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

app = Flask(__name__, template_folder="templates", static_folder="static")
B = joblib.load(os.path.join(ML_DIR, "model.joblib"))
MODEL, MED, REF, FEATS = B["model"], B["medians"], B["ref"], B["feats"]
METRICS = json.load(open(os.path.join(ML_DIR, "metrics.json")))
CUTS = METRICS["cuts"]
LIMITS = {"Pregnancies": (0, 20), "Glucose": (40, 300), "BloodPressure": (30, 200), "SkinThickness": (5, 100),
          "Insulin": (10, 900), "BMI": (10, 70), "DiabetesPedigreeFunction": (0.05, 2.5), "Age": (18, 100)}
REQUIRED = {"Glucose", "BMI", "Age"}          # others may be blank -> dataset median is used
LABEL = {"Pregnancies": "Pregnancies", "Glucose": "Glucose", "BloodPressure": "Blood pressure", "SkinThickness": "Skin thickness",
         "Insulin": "Insulin", "BMI": "BMI", "DiabetesPedigreeFunction": "Family history score", "Age": "Age"}
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def db():
    c = sqlite3.connect(os.path.join(INST, "uyirscore.db")); c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS assessments(id INTEGER PRIMARY KEY, ts REAL, label TEXT, risk REAL, band TEXT, age REAL, bmi REAL, glucose REAL)")
    return c


def band(p): return "Low" if p < CUTS[0] else "Moderate" if p < CUTS[1] else "High"
def score(v): return float(MODEL.predict_proba(pd.DataFrame([v], columns=FEATS))[0][1])


def parse(data):
    vals, filled = {}, []
    for f in FEATS:
        raw = data.get(f)
        if raw in (None, ""):
            if f in REQUIRED: raise ValueError(f"{LABEL[f]} is required.")
            vals[f] = float(MED[f]); filled.append(LABEL[f]); continue
        try: x = float(raw)
        except ValueError: raise ValueError(f"{LABEL[f]} must be a number.")
        lo, hi = LIMITS[f]
        if not lo <= x <= hi: raise ValueError(f"{LABEL[f]} should be between {lo} and {hi}.")
        vals[f] = x
    return vals, filled


def drivers(vals, p):
    """What-if: how much does risk drop if this value were the healthy-reference value?"""
    out = []
    for f in FEATS:
        v = dict(vals); v[f] = float(REF[f])
        out.append({"label": LABEL[f], "value": vals[f], "impact": round(p - score(v), 3)})
    return sorted(out, key=lambda d: -abs(d["impact"]))[:5]


def fallback_text(p, b, drv):
    up = [d["label"].lower() for d in drv if d["impact"] > 0.02][:3]
    head = f"Based on your health numbers, your estimated chance of developing diabetes is {p*100:.0f}%, which falls in the {b.lower()} risk range. " + (
        "The numbers that affect your result the most right now are " + ", ".join(up) + "." if up else "None of your single numbers stands out as raising your risk.")
    recs = {"Low": ["Keep enjoying regular daily physical activity (about 150 minutes a week).", "Eat balanced meals with plenty of vegetables, whole grains, and water.", "Get your blood sugar checked every 1 to 3 years.", "Talk to a doctor if you notice any unusual changes in your health."],
            "Moderate": ["Aim for 30 minutes of brisk walking or moderate exercise most days.", "Cut back on sugary drinks, sweets, and refined snacks.", "Losing even a small amount of extra weight can significantly protect your health.", "Ask a doctor about getting a routine blood sugar test."],
            "High": ["See a doctor soon for a formal fasting blood sugar or HbA1c test.", "Start taking daily walks and drinking water instead of sugary drinks.", "Ask your healthcare team about guided lifestyle and healthy eating programmes.", "Keep track of your blood pressure and weight over time."]}[b]
    return head + "\n\nRecommendations:\n" + "\n".join("- " + r for r in recs)


def groq_text(p, b, vals, drv):
    key = os.getenv("GROQ_API_KEY")
    if not key: return None
    facts = ", ".join(f"{LABEL[k]}={v:g}" for k, v in vals.items())
    drv_txt = "; ".join(f"{d['label']} ({'raises' if d['impact'] > 0 else 'lowers'} risk by {abs(d['impact'])*100:.0f} points)" for d in drv)
    body = {"model": GROQ_MODEL, "temperature": 0.3, "max_completion_tokens": 900, "messages": [
        {"role": "system", "content": "You are a friendly, caring health educator writing at a 6th-grade reading level. Explain this diabetes risk estimate in simple, reassuring words. Never mention models, algorithms, machine learning, statistics, or AI. Do NOT diagnose, do NOT mention medication or doses. Write 3-4 short sentences, then a line 'Recommendations:' followed by 4 bullet lines starting with '- ' (healthy habits, everyday changes, and when to see a doctor). No markdown headings, no emojis."},
        {"role": "user", "content": f"Estimated risk: {p*100:.0f}% ({b}). Health numbers: {facts}. Main factors: {drv_txt}."}]}
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "UyirScore/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return (json.load(r)["choices"][0]["message"]["content"] or "").strip() or None
    except Exception as e:
        print("[groq failed]", type(e).__name__, e); return None


@app.get("/")
def home():
    resp = send_from_directory(os.path.join(HERE, "templates"), "index.html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.get("/logo.png")
def logo(): return send_from_directory(os.path.join(HERE, "static"), "logo.png")


@app.post("/api/predict")
def predict():
    data = request.get_json(silent=True) or {}
    try: vals, filled = parse(data)
    except ValueError as e: return jsonify(error=str(e)), 400
    p = score(vals); b = band(p); drv = drivers(vals, p)
    text = groq_text(p, b, vals, drv); source = "groq" if text else "fallback"
    text = text or fallback_text(p, b, drv)
    with db() as c:
        c.execute("INSERT INTO assessments(ts,label,risk,band,age,bmi,glucose) VALUES(?,?,?,?,?,?,?)",
                  (time.time(), str(data.get("label", ""))[:40], p, b, vals["Age"], vals["BMI"], vals["Glucose"]))
    return jsonify(risk=p, band=b, drivers=drv, explanation=text, explanation_source=source, assumed=filled)


@app.get("/api/dashboard")
def dashboard():
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM assessments ORDER BY id DESC LIMIT 200")]
    counts = {k: sum(r["band"] == k for r in rows) for k in ("Low", "Moderate", "High")}
    return jsonify(cohort=METRICS, live={"total": len(rows), "counts": counts, "recent": rows[:8]})


def chatbot_reply(msg):
    key = os.getenv("GROQ_API_KEY")
    if key:
        body = {
            "model": GROQ_MODEL,
            "temperature": 0.4,
            "max_completion_tokens": 500,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are UyirBot, a kind and empathetic health assistant for Uyir Score, a diabetes risk "
                        "assessment platform for Indian families. Answer questions in clear, simple language (6th to 8th grade reading level). "
                        "Provide accurate guidance on diabetes prevention, ICMR blood sugar ranges, HbA1c, BMI, balanced Indian diet "
                        "(millets, whole grains, non-starchy vegetables, cutting sugar), physical activity, and emergency signs. "
                        "Never diagnose disease or prescribe medications/dosages. Always suggest consulting a physician for formal clinical lab tests. "
                        "Keep replies concise (2 to 4 short paragraphs or bullet points). No markdown headings or emojis."
                    )
                },
                {"role": "user", "content": msg}
            ]
        }
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            json.dumps(body).encode(),
            {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "UyirScore/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                ans = (json.load(r)["choices"][0]["message"]["content"] or "").strip()
                if ans:
                    return ans, "groq"
        except Exception as e:
            print("[groq chat error]", type(e).__name__, e)

    m = msg.lower()
    if any(w in m for w in ["sugar", "glucose", "normal", "range", "fasting", "hba1c", "level"]):
        return (
            "Standard blood glucose guidelines (ICMR/WHO):\n\n"
            "- Fasting Blood Sugar: 70-99 mg/dL is normal, 100-125 mg/dL indicates prediabetes, and 126 mg/dL or higher suggests diabetes.\n"
            "- Post-Meal (2 hrs) / Random: Under 140 mg/dL is normal, 140-199 mg/dL indicates prediabetes, and 200+ mg/dL suggests diabetes.\n"
            "- HbA1c (3-month average): Below 5.7% is normal, 5.7%-6.4% is prediabetes, and 6.5%+ indicates diabetes.\n\n"
            "Please consult a physician for formal laboratory blood testing.",
            "fallback"
        )
    if any(w in m for w in ["how", "test", "work", "score", "uyir", "start"]):
        return (
            "The Uyir Score screening test takes about 2 minutes. Answer 9 simple questions on age, BMI, blood sugar, "
            "blood pressure, and family history. You will receive an evidence-based risk estimate (Low, Moderate, or High) "
            "and practical lifestyle steps.",
            "fallback"
        )
    if any(w in m for w in ["food", "diet", "eat", "lower", "reduce", "prevent", "exercise", "walk"]):
        return (
            "Everyday steps to lower diabetes risk:\n\n"
            "- Replace polished white rice with traditional millets (ragi, bajra, foxtail) and whole grains.\n"
            "- Cut back on sugary drinks, sweetened chai, and deep-fried snacks.\n"
            "- Walk briskly for 30 minutes a day, at least 5 days a week.\n"
            "- Losing even 5% to 7% of excess weight can significantly cut risk for people with prediabetes.",
            "fallback"
        )
    if any(w in m for w in ["emergency", "hospital", "urgent", "help", "symptom", "danger", "108"]):
        return (
            "Warning signs needing urgent medical evaluation:\n\n"
            "- Blood sugar over 300 mg/dL with confusion, vomiting, or deep rapid breathing.\n"
            "- Blood sugar under 70 mg/dL with severe trembling, sweating, or dizziness.\n"
            "In India, dial 108 for ambulance or 112 for national emergency services.",
            "fallback"
        )
    return (
        "Hello! I am UyirBot, your health assistant. You can ask me about blood sugar ranges, how to take the Uyir Score screening test, "
        "diet and exercise tips for diabetes prevention, or when to seek medical help.",
        "fallback"
    )


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    msg = str(data.get("message", "")).strip()
    if not msg:
        return jsonify(error="Empty message"), 400
    reply, src = chatbot_reply(msg)
    return jsonify(reply=reply, source=src)


if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 5000)))
