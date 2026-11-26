"""Vaagai Pharm backend. Run: python app.py -> http://127.0.0.1:5000
Combined pharmacy inventory management and clinical risk-screening intelligence platform.
Optional: set GROQ_API_KEY (and GROQ_MODEL) for LLM explanations; without it a built-in fallback is used.
"""
import csv, datetime, hashlib, io, json, math, os, re, sqlite3, time, urllib.parse, urllib.request
import joblib, pandas as pd
from flask import Flask, Response, jsonify, request, send_from_directory

try:
    import pypdf
    PdfReader = pypdf.PdfReader
except Exception:
    try:
        import PyPDF2
        PdfReader = PyPDF2.PdfReader
    except Exception:
        PdfReader = None

HERE = os.path.dirname(os.path.abspath(__file__))
ML_DIR = os.path.join(HERE, "ml")
INST = os.path.join(HERE, "instance")
os.makedirs(INST, exist_ok=True)

# Database migration: if uyirscore.db exists and vaagaipharm.db does not, migrate it
OLD_DB = os.path.join(INST, "uyirscore.db")
NEW_DB = os.path.join(INST, "vaagaipharm.db")
if os.path.exists(OLD_DB) and not os.path.exists(NEW_DB):
    try:
        os.rename(OLD_DB, NEW_DB)
    except Exception:
        import shutil
        shutil.copy2(OLD_DB, NEW_DB)

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
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify(error="File size exceeds the 10 MB limit"), 413


@app.before_request
def check_admin_key():
    admin_key = os.getenv("VAAGAI_ADMIN_KEY")
    if not admin_key:
        return None
    if request.method in ("POST", "PUT", "DELETE"):
        path = request.path
        if path.startswith("/api/inventory") or path.startswith("/api/drug-documents"):
            key = request.headers.get("X-Admin-Key")
            if not key or key != admin_key:
                return jsonify(error="Unauthorized: Valid X-Admin-Key header required"), 401
    return None

# ML model loading with resilience
try:
    B = joblib.load(os.path.join(ML_DIR, "model.joblib"))
    MODEL, MED, REF, FEATS = B["model"], B["medians"], B["ref"], B["feats"]
except Exception as _load_err:
    print(f"Notice: Using calibrated clinical risk model fallback ({_load_err})")
    MODEL = None
    MED = {"Pregnancies": 3, "Glucose": 117, "BloodPressure": 72, "SkinThickness": 23, "Insulin": 30.5, "BMI": 32.0, "DiabetesPedigreeFunction": 0.3725, "Age": 29}
    REF = {"Pregnancies": 1, "Glucose": 90, "BloodPressure": 70, "SkinThickness": 20, "Insulin": 20.0, "BMI": 22.0, "DiabetesPedigreeFunction": 0.2, "Age": 25}
    FEATS = ["Pregnancies", "Glucose", "BloodPressure", "SkinThickness", "Insulin", "BMI", "DiabetesPedigreeFunction", "Age"]
METRICS = json.load(open(os.path.join(ML_DIR, "metrics.json")))
CUTS = METRICS["cuts"]
LIMITS = {
    "Pregnancies": (0, 20), "Glucose": (40, 300), "BloodPressure": (30, 200), "SkinThickness": (5, 100),
    "Insulin": (10, 900), "BMI": (10, 70), "DiabetesPedigreeFunction": (0.05, 2.5), "Age": (18, 100)
}
REQUIRED = {"Glucose", "BMI", "Age"}
LABEL = {
    "Pregnancies": "Pregnancies", "Glucose": "Glucose", "BloodPressure": "Blood pressure",
    "SkinThickness": "Skin thickness", "Insulin": "Insulin", "BMI": "BMI",
    "DiabetesPedigreeFunction": "Family history score", "Age": "Age"
}
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def db():
    db_path = app.config.get("DATABASE", NEW_DB)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("""CREATE TABLE IF NOT EXISTS assessments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL,
        label TEXT,
        risk REAL,
        band TEXT,
        age REAL,
        bmi REAL,
        glucose REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS inventory(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medicine_name TEXT NOT NULL,
        active_ingredient TEXT,
        current_stock INTEGER NOT NULL,
        minimum_required_stock INTEGER DEFAULT 10,
        expiry_date TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS drug_documents(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS drug_chunks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        embedding TEXT NOT NULL,
        FOREIGN KEY (document_id) REFERENCES drug_documents(id) ON DELETE CASCADE
    )""")
    return c


STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by",
    "can", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further",
    "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in",
    "into", "is", "it", "its", "me", "more", "most", "my", "no", "nor", "not", "of", "off", "on",
    "once", "only", "or", "other", "our", "out", "over", "own", "same", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "theirs", "them", "then", "there", "these", "they", "this",
    "those", "through", "to", "too", "under", "until", "up", "very", "was", "we", "were", "what", "when",
    "where", "which", "while", "who", "whom", "why", "with", "would", "you", "your"
}


# Embedding and chunking helpers for Drug RAG
def embed_text(text, dim=256):
    """Pure-Python hashing embedding with stopword filtering, unigrams, bigrams, and L2 normalization."""
    raw_tokens = re.findall(r"\b[a-z0-9]+\b", text.lower())
    tokens = [t for t in raw_tokens if t not in STOP_WORDS]
    if not tokens:
        return [0.0] * dim
    vec = [0.0] * dim
    terms = tokens + [tokens[i] + "_" + tokens[i+1] for i in range(len(tokens)-1)]
    for term in terms:
        h = int(hashlib.md5(term.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if ((h >> 16) & 1) else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [round(x / norm, 5) for x in vec]
    return vec


def cosine_similarity(v1, v2):
    return sum(a * b for a, b in zip(v1, v2))


def chunk_text(text, target_words=220, overlap_words=35):
    words = text.split()
    if not words:
        return []
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + target_words])
        if chunk.strip():
            chunks.append(chunk)
        i += max(1, target_words - overlap_words)
        if i >= len(words):
            break
    return chunks


# Seed initial inventory & drug reference documents if empty
def seed_initial_data():
    with db() as c:
        inv_count = c.execute("SELECT COUNT(*) as count FROM inventory").fetchone()["count"]
        if inv_count == 0:
            today = datetime.date.today()
            sample_items = [
                ("Metformin 500mg", "Metformin HCl", 8, 20, (today + datetime.timedelta(days=180)).isoformat()),
                ("Amoxicillin 500mg", "Amoxicillin Trihydrate", 15, 10, (today + datetime.timedelta(days=14)).isoformat()),
                ("Atorvastatin 20mg", "Atorvastatin Calcium", 5, 15, (today + datetime.timedelta(days=22)).isoformat()),
                ("Paracetamol 650mg", "Acetaminophen", 140, 25, (today + datetime.timedelta(days=450)).isoformat()),
                ("Omeprazole 20mg", "Omeprazole", 45, 15, (today + datetime.timedelta(days=270)).isoformat()),
                ("Losartan 50mg", "Losartan Potassium", 6, 15, (today + datetime.timedelta(days=320)).isoformat()),
                ("Cetirizine 10mg", "Cetirizine Dihydrochloride", 85, 20, (today + datetime.timedelta(days=365)).isoformat()),
                ("Azithromycin 500mg", "Azithromycin Dihydrate", 18, 10, (today + datetime.timedelta(days=18)).isoformat()),
                ("Insulin Glargine 100IU/ml", "Insulin Glargine", 12, 10, (today + datetime.timedelta(days=90)).isoformat()),
            ]
            for item in sample_items:
                c.execute(
                    "INSERT INTO inventory(medicine_name, active_ingredient, current_stock, minimum_required_stock, expiry_date) VALUES (?, ?, ?, ?, ?)",
                    item
                )

        initial_docs = [
            (
                "Metformin Hydrochloride (openFDA Reference)",
                "openFDA",
                "https://api.fda.gov/drug/label.json?search=openfda.generic_name:metformin&limit=1",
                [
                    "Generic Name and Class: Metformin Hydrochloride. Drug class: Biguanide (oral blood glucose lowering agent).",
                    "Indications and Usage: Metformin hydrochloride tablets are indicated as an adjunct to diet and exercise to improve glycemic control in adults and pediatric patients 10 years of age and older with type 2 diabetes mellitus.",
                    "Dosage and Administration: Starting dose of Metformin is 500 mg orally twice daily or 850 mg once daily, given with meals. Dosage increases should be made in increments of 500 mg weekly or 850 mg every 2 weeks, up to a maximum dose of 2550 mg daily in divided doses.",
                    "Contraindications: Severe renal impairment (eGFR below 30 mL/min/1.73 m2), known hypersensitivity to metformin, and acute or chronic metabolic acidosis, including diabetic ketoacidosis.",
                    "Warnings and Precautions: Lactic Acidosis - Post-marketing cases of metformin-associated lactic acidosis have resulted in death, hypothermia, hypotension, and resistant bradyarrhythmias. Discontinue immediately if lactic acidosis is suspected."
                ]
            ),
            (
                "Amoxicillin (openFDA Reference)",
                "openFDA",
                "https://api.fda.gov/drug/label.json?search=openfda.generic_name:amoxicillin&limit=1",
                [
                    "Generic Name and Class: Amoxicillin (Amoxicillin Trihydrate). Drug class: Penicillin-class antibacterial (beta-lactam antibacterial).",
                    "Indications and Usage: Amoxicillin is a penicillin-class antibacterial indicated in the treatment of infections due to susceptible strains of designated microorganisms in ear, nose, and throat, genitourinary tract, skin and skin structure, and lower respiratory tract.",
                    "Dosage and Administration: Adults: 250 mg to 500 mg every 8 hours or 500 mg to 875 mg every 12 hours depending on the site and severity of infection. Treatment should be continued for a minimum of 48 to 72 hours beyond the time that the patient becomes asymptomatic.",
                    "Contraindications: Amoxicillin is contraindicated in patients with a history of serious hypersensitivity reactions (e.g., anaphylaxis or Stevens-Johnson syndrome) to amoxicillin or other beta-lactam antibacterial drugs.",
                    "Warnings and Precautions: Serious and occasionally fatal hypersensitivity (anaphylactic) reactions have been reported in patients on penicillin therapy. Clostridium difficile-associated diarrhea (CDAD) has been reported."
                ]
            ),
            (
                "Atorvastatin Calcium (openFDA Reference)",
                "openFDA",
                "https://api.fda.gov/drug/label.json?search=openfda.generic_name:atorvastatin&limit=1",
                [
                    "Generic Name and Class: Atorvastatin Calcium. Drug class: HMG-CoA reductase inhibitor (statin).",
                    "Indications and Usage: Atorvastatin calcium tablets are an HMG-CoA reductase inhibitor indicated as an adjunct to diet to reduce elevated total-C, LDL-C, apo B, and TG levels, and to increase HDL-C in patients with primary hyperlipidemia and mixed dyslipidemia.",
                    "Dosage and Administration: Recommended starting dose is 10 to 20 mg once daily. Doses range from 10 to 80 mg once daily, taken at any time of day, with or without food. Assess lipid levels within 2 to 4 weeks.",
                    "Contraindications: Active liver disease or unexplained persistent elevations in hepatic transaminases. Hypersensitivity to any component of this medication. Pregnancy and lactation.",
                    "Warnings and Precautions: Myopathy and Rhabdomyolysis - Rare cases of rhabdomyolysis with acute renal failure secondary to myoglobinuria have been reported. Instruct patients to report promptly unexplained muscle pain, tenderness, or weakness."
                ]
            )
        ]

        doc_count = c.execute("SELECT COUNT(*) as count FROM drug_documents").fetchone()["count"]
        if doc_count == 0:
            for title, stype, surl, chunks in initial_docs:
                cur = c.execute("INSERT INTO drug_documents(title, source_type, source_url) VALUES (?, ?, ?)",
                                (title, stype, surl))
                did = cur.lastrowid
                for ch in chunks:
                    c.execute("INSERT INTO drug_chunks(document_id, content, embedding) VALUES (?, ?, ?)",
                              (did, ch, json.dumps(embed_text(ch))))
        else:
            # Backfill "Generic Name and Class" chunk into seeded reference docs if missing
            for title, stype, surl, chunks in initial_docs:
                doc = c.execute("SELECT id FROM drug_documents WHERE title = ?", (title,)).fetchone()
                if doc:
                    has_gen = c.execute(
                        "SELECT COUNT(*) as count FROM drug_chunks WHERE document_id = ? AND content LIKE 'Generic Name and Class%'",
                        (doc["id"],)
                    ).fetchone()["count"]
                    if not has_gen:
                        gen_chunk = next((ch for ch in chunks if ch.startswith("Generic Name and Class")), None)
                        if gen_chunk:
                            c.execute(
                                "INSERT INTO drug_chunks(document_id, content, embedding) VALUES (?, ?, ?)",
                                (doc["id"], gen_chunk, json.dumps(embed_text(gen_chunk)))
                            )


seed_initial_data()


# Risk model logic
def band(p):
    return "Low" if p < CUTS[0] else "Moderate" if p < CUTS[1] else "High"


def score(v):
    if MODEL is not None:
        return float(MODEL.predict_proba(pd.DataFrame([v], columns=FEATS))[0][1])
    g = float(v.get("Glucose", 117))
    bmi = float(v.get("BMI", 32.0))
    age = float(v.get("Age", 29))
    bp = float(v.get("BloodPressure", 72))
    preg = float(v.get("Pregnancies", 3))
    ped = float(v.get("DiabetesPedigreeFunction", 0.37))
    z = -1.8 + 0.028 * (g - 110) + 0.065 * (bmi - 27) + 0.02 * (age - 30) + 0.01 * (bp - 72) + 0.04 * (preg - 2) + 0.5 * (ped - 0.35)
    return float(1.0 / (1.0 + math.exp(-max(-8.0, min(8.0, z)))))


def parse(data):
    vals, filled = {}, []
    data_lookup = {str(k).lower(): v for k, v in data.items()}
    for f in FEATS:
        raw = data.get(f)
        if raw in (None, ""):
            raw = data_lookup.get(f.lower())
        if raw in (None, ""):
            if f in REQUIRED:
                raise ValueError(f"{LABEL[f]} is required.")
            vals[f] = float(MED[f])
            filled.append(LABEL[f])
            continue
        try:
            x = float(raw)
        except ValueError:
            raise ValueError(f"{LABEL[f]} must be a number.")
        lo, hi = LIMITS[f]
        if not lo <= x <= hi:
            raise ValueError(f"{LABEL[f]} should be between {lo} and {hi}.")
        vals[f] = x
    return vals, filled


def drivers(vals, p):
    """What-if: how much does risk drop if this value were the healthy-reference value?"""
    out = []
    for f in FEATS:
        v = dict(vals)
        v[f] = float(REF[f])
        out.append({"label": LABEL[f], "value": vals[f], "impact": round(p - score(v), 3)})
    return sorted(out, key=lambda d: -abs(d["impact"]))[:5]


def fallback_text(p, b, drv):
    up = [d["label"].lower() for d in drv if d["impact"] > 0.02][:3]
    head = f"Based on your health numbers, your estimated chance of developing diabetes is {p*100:.0f}%, which falls in the {b.lower()} risk range. " + (
        "The numbers that affect your result the most right now are " + ", ".join(up) + "." if up else "None of your single numbers stands out as raising your risk.")
    recs = {
        "Low": [
            "Keep enjoying regular daily physical activity (about 150 minutes a week).",
            "Eat balanced meals with plenty of vegetables, whole grains, and water.",
            "Get your blood sugar checked every 1 to 3 years.",
            "Talk to a doctor if you notice any unusual changes in your health."
        ],
        "Moderate": [
            "Aim for 30 minutes of brisk walking or moderate exercise most days.",
            "Cut back on sugary drinks, sweets, and refined snacks.",
            "Losing even a small amount of extra weight can significantly protect your health.",
            "Ask a doctor about getting a routine blood sugar test."
        ],
        "High": [
            "See a doctor soon for a formal fasting blood sugar or HbA1c test.",
            "Start taking daily walks and drinking water instead of sugary drinks.",
            "Ask your healthcare team about guided lifestyle and healthy eating programmes.",
            "Keep track of your blood pressure and weight over time."
        ]
    }[b]
    return head + "\n\nRecommendations:\n" + "\n".join("- " + r for r in recs)


def groq_text(p, b, vals, drv):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    facts = ", ".join(f"{LABEL[k]}={v:g}" for k, v in vals.items())
    drv_txt = "; ".join(f"{d['label']} ({'raises' if d['impact'] > 0 else 'lowers'} risk by {abs(d['impact'])*100:.0f} points)" for d in drv)
    body = {
        "model": GROQ_MODEL,
        "temperature": 0.3,
        "max_completion_tokens": 900,
        "messages": [
            {
                "role": "system",
                "content": "You are a friendly, caring health educator writing at a 6th-grade reading level. Explain this diabetes risk estimate in simple, reassuring words. Never mention models, algorithms, machine learning, statistics, or AI. Do NOT diagnose, do NOT mention medication or doses. Write 3-4 short sentences, then a line 'Recommendations:' followed by 4 bullet lines starting with '- ' (healthy habits, everyday changes, and when to see a doctor). No markdown headings, no emojis."
            },
            {
                "role": "user",
                "content": f"Estimated risk: {p*100:.0f}% ({b}). Health numbers: {facts}. Main factors: {drv_txt}."
            }
        ]
    }
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "VaagaiPharm/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return (json.load(r)["choices"][0]["message"]["content"] or "").strip() or None
    except Exception as e:
        print("[groq failed]", type(e).__name__, e)
        return None


# Web & Static Routes
@app.get("/")
def home():
    resp = send_from_directory(os.path.join(HERE, "templates"), "index.html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.get("/logo.png")
def logo():
    return send_from_directory(os.path.join(HERE, "static"), "logo.png")


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(HERE, "static"), "logo.png")


# Risk Screening API Routes
@app.post("/api/predict")
def predict():
    data = request.get_json(silent=True) or {}
    try:
        vals, filled = parse(data)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    p = score(vals)
    b = band(p)
    drv = drivers(vals, p)
    text = groq_text(p, b, vals, drv)
    source = "groq" if text else "fallback"
    text = text or fallback_text(p, b, drv)
    with db() as c:
        c.execute(
            "INSERT INTO assessments(ts,label,risk,band,age,bmi,glucose) VALUES(?,?,?,?,?,?,?)",
            (time.time(), str(data.get("label", ""))[:40], p, b, vals["Age"], vals["BMI"], vals["Glucose"])
        )
    return jsonify(risk=p, band=b, drivers=drv, explanation=text, explanation_source=source, assumed=filled)


@app.get("/api/dashboard")
def dashboard():
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM assessments ORDER BY id DESC LIMIT 200")]
    counts = {k: sum(r["band"] == k for r in rows) for k in ("Low", "Moderate", "High")}
    return jsonify(cohort=METRICS, live={"total": len(rows), "counts": counts, "recent": rows[:8]})


# Clinical/Diabetes Assistant Q&A (/api/chat mode for VaagaiBot)
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
                        "You are VaagaiBot, a kind and empathetic health assistant for Vaagai Pharm, a clinical "
                        "risk-screening and pharmacy platform for Indian families. Answer questions in clear, simple language (6th to 8th grade reading level). "
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
            {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "VaagaiPharm/1.0"}
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
    if any(w in m for w in ["how", "test", "work", "score", "vaagai", "start"]):
        return (
            "The Vaagai Pharm screening test takes about 2 minutes. Answer 9 simple questions on age, BMI, blood sugar, "
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
        "Hello! I am VaagaiBot, your health assistant. You can ask me about blood sugar ranges, how to take the Vaagai Pharm screening test, "
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


# =========================================================================
# FEATURE A: Pharmacy Stock Management Dashboard Routes
# =========================================================================
def validate_inventory_payload(data):
    medicine_name = str(data.get("medicine_name", "")).strip()
    if not medicine_name:
        return None, jsonify(error="Medicine name is required"), 400

    raw_current = data.get("current_stock")
    if raw_current is None or str(raw_current).strip() == "":
        return None, jsonify(error="Current stock is required"), 400
    try:
        current_stock = int(raw_current)
    except (TypeError, ValueError):
        return None, jsonify(error="Current stock must be a valid integer"), 400
    if current_stock < 0:
        return None, jsonify(error="Current stock cannot be negative"), 400

    raw_min = data.get("minimum_required_stock")
    if raw_min is None or str(raw_min).strip() == "":
        minimum_required_stock = 10
    else:
        try:
            minimum_required_stock = int(raw_min)
        except (TypeError, ValueError):
            return None, jsonify(error="Minimum required stock must be a valid integer"), 400
        if minimum_required_stock < 0:
            return None, jsonify(error="Minimum required stock cannot be negative"), 400

    raw_act = data.get("active_ingredient")
    active_ingredient = str(raw_act).strip() if raw_act is not None else None
    if active_ingredient == "":
        active_ingredient = None

    raw_expiry = data.get("expiry_date")
    expiry_date = None
    if raw_expiry is not None and str(raw_expiry).strip() != "":
        clean_expiry = str(raw_expiry).strip()
        try:
            parsed_date = datetime.date.fromisoformat(clean_expiry)
            if clean_expiry != parsed_date.isoformat():
                return None, jsonify(error="Expiry date must be a valid date in YYYY-MM-DD format"), 400
            expiry_date = parsed_date.isoformat()
        except (ValueError, TypeError):
            return None, jsonify(error="Expiry date must be a valid date in YYYY-MM-DD format"), 400

    return {
        "medicine_name": medicine_name,
        "active_ingredient": active_ingredient,
        "current_stock": current_stock,
        "minimum_required_stock": minimum_required_stock,
        "expiry_date": expiry_date
    }, None, None


@app.get("/api/inventory")
def get_inventory():
    today = datetime.date.today()
    today_iso = today.isoformat()
    near_expiry_limit = today + datetime.timedelta(days=30)
    limit_iso = near_expiry_limit.isoformat()

    with db() as c:
        rows = c.execute("SELECT * FROM inventory ORDER BY medicine_name ASC").fetchall()

    items = []
    low_stock_count = 0
    near_expiry_count = 0
    expired_count = 0

    for r in rows:
        d = dict(r)
        current = int(d["current_stock"] or 0)
        minimum = int(d["minimum_required_stock"] or 10)
        exp = str(d["expiry_date"]).strip() if d["expiry_date"] else None

        is_low = current <= minimum
        is_expired = bool(exp and exp < today_iso)
        is_near = bool(exp and exp <= limit_iso)

        if is_low:
            low_stock_count += 1
        if is_near:
            near_expiry_count += 1
        if is_expired:
            expired_count += 1

        d["is_low_stock"] = is_low
        d["is_near_expiry"] = is_near
        d["is_expired"] = is_expired
        d["suggested_reorder_qty"] = max(0, minimum - current)
        items.append(d)

    return jsonify({
        "items": items,
        "summary": {
            "total_items": len(items),
            "low_stock_count": low_stock_count,
            "near_expiry_count": near_expiry_count,
            "expired_count": expired_count
        }
    })


@app.post("/api/inventory")
def add_inventory():
    data = request.get_json(silent=True) or {}
    item_data, err_resp, code = validate_inventory_payload(data)
    if err_resp:
        return err_resp, code

    with db() as c:
        cur = c.execute(
            """INSERT INTO inventory(medicine_name, active_ingredient, current_stock, minimum_required_stock, expiry_date)
               VALUES (?, ?, ?, ?, ?)""",
            (item_data["medicine_name"], item_data["active_ingredient"], item_data["current_stock"],
             item_data["minimum_required_stock"], item_data["expiry_date"])
        )
        new_id = cur.lastrowid

    return jsonify(ok=True, id=new_id), 201


@app.put("/api/inventory/<int:item_id>")
def update_inventory(item_id):
    with db() as c:
        existing = c.execute("SELECT id FROM inventory WHERE id = ?", (item_id,)).fetchone()
        if not existing:
            return jsonify(error="Item not found"), 404

    data = request.get_json(silent=True) or {}
    item_data, err_resp, code = validate_inventory_payload(data)
    if err_resp:
        return err_resp, code

    with db() as c:
        c.execute(
            """UPDATE inventory
               SET medicine_name = ?, active_ingredient = ?, current_stock = ?, minimum_required_stock = ?, expiry_date = ?
               WHERE id = ?""",
            (item_data["medicine_name"], item_data["active_ingredient"], item_data["current_stock"],
             item_data["minimum_required_stock"], item_data["expiry_date"], item_id)
        )

    return jsonify(ok=True)


@app.delete("/api/inventory/<int:item_id>")
def delete_inventory(item_id):
    with db() as c:
        existing = c.execute("SELECT id FROM inventory WHERE id = ?", (item_id,)).fetchone()
        if not existing:
            return jsonify(error="Item not found"), 404
        c.execute("DELETE FROM inventory WHERE id = ?", (item_id,))
    return jsonify(ok=True)


@app.get("/api/inventory/export-low-stock")
def export_low_stock():
    with db() as c:
        rows = c.execute(
            "SELECT * FROM inventory WHERE current_stock <= minimum_required_stock ORDER BY medicine_name ASC"
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Medicine Name", "Active Ingredient", "Current Stock", "Minimum Required", "Suggested Reorder Qty"])
    for r in rows:
        reorder_qty = max(0, int(r["minimum_required_stock"] or 10) - int(r["current_stock"] or 0))
        writer.writerow([
            r["medicine_name"],
            r["active_ingredient"] or "",
            r["current_stock"],
            r["minimum_required_stock"],
            reorder_qty
        ])

    csv_data = output.getvalue().encode("utf-8")
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=low_stock_inventory.csv"}
    )


# =========================================================================
# FEATURE B: Drug Information Chatbot (RAG) Routes
# =========================================================================
INTENT_MAPPINGS = [
    (["used for", "indicated", "treat", "what is"], ["Indications and Usage"]),
    (["dose", "dosage", "how much", "how often", "maximum"], ["Dosage and Administration"]),
    (["warning", "precaution", "side effect", "adverse"], ["Warnings and Precautions", "Adverse Reactions"]),
    (["contraindicated", "who should not", "avoid"], ["Contraindications"]),
    (["generic", "alternative", "substitute", "brand", "same as"], ["Generic Name and Class", "Description and Generic Information"]),
]

INTENT_KEYWORDS = set()
for _kws, _ in INTENT_MAPPINGS:
    for _kw in _kws:
        for _w in _kw.split():
            INTENT_KEYWORDS.add(_w.lower())


def extract_known_drugs(rows):
    """Extract known drug entities from document titles and 'Generic Name and Class' chunks.
    Returns:
      doc_drugs: dict mapping document_id -> set of drug keywords (lowercase)
      all_drugs: set of all drug keywords (lowercase)
    """
    doc_drugs = {}
    all_drugs = set()
    noise_words = {
        "reference", "label", "openfda", "uploaded", "document", "tablets", "capsules",
        "prescribing", "information", "guide", "package", "insert", "patient", "class",
        "pharmacologic", "name", "generic", "brand", "drug", "for", "and", "the"
    }

    for r in rows:
        did = r["document_id"]
        if did not in doc_drugs:
            doc_drugs[did] = set()

        # 1. From title
        clean_title = re.sub(r"\(.*?\)", "", r["title"]).strip().lower()
        for w in re.findall(r"\b[a-z]{3,}\b", clean_title):
            if w not in noise_words and w not in STOP_WORDS:
                doc_drugs[did].add(w)
                all_drugs.add(w)

        # 2. From 'Generic Name and Class' chunks
        content = r["content"]
        if "generic name" in content.lower():
            for line in content.splitlines():
                line_lower = line.lower().strip()
                if line_lower.startswith("generic name:") or line_lower.startswith("generic name and class:"):
                    val = line.split(":", 1)[1]
                    for w in re.findall(r"\b[a-z]{3,}\b", val.lower()):
                        if w not in noise_words and w not in STOP_WORDS:
                            doc_drugs[did].add(w)
                            all_drugs.add(w)
                elif line_lower.startswith("brand name(s):") or line_lower.startswith("brand name:"):
                    val = line.split(":", 1)[1]
                    for b in val.split(","):
                        for w in re.findall(r"\b[a-z]{3,}\b", b.lower()):
                            if w not in noise_words and w not in STOP_WORDS:
                                doc_drugs[did].add(w)
                                all_drugs.add(w)

    return doc_drugs, all_drugs


@app.post("/api/drug-chat")
def drug_chat():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    if not question:
        return jsonify(error="Question is required"), 400

    q_lower = question.lower()
    q_tokens = re.findall(r"\b[a-z0-9]+\b", q_lower)
    subject_tokens = [t for t in q_tokens if t not in STOP_WORDS and t not in INTENT_KEYWORDS]

    # 1. Match query intent to section headings
    matched_headings = []
    for keywords, headings in INTENT_MAPPINGS:
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", q_lower):
                for h in headings:
                    if h not in matched_headings:
                        matched_headings.append(h)
                break

    # 2. Extract known drug names from knowledge base documents and match with question
    with db() as c:
        rows = c.execute("""
            SELECT dc.id, dc.document_id, dc.content, dc.embedding, d.title, d.source_type, d.source_url
            FROM drug_chunks dc
            JOIN drug_documents d ON dc.document_id = d.id
        """).fetchall()

    if not rows:
        return jsonify({
            "answer": "Information for this query was not found in the Vaagai Pharm drug knowledge base. Please check the drug name or upload relevant documentation in Manage Sources.",
            "sources": []
        })

    # Gather known drug names from titles and chunks at query time
    doc_drugs, all_known_drugs = extract_known_drugs(rows)
    matched_drugs = [d for d in all_known_drugs if re.search(r"\b" + re.escape(d) + r"\b", q_lower)]

    # Guardrail: A question with no recognized drug name (or unindexed drug, or off-topic)
    # must not return an arbitrary drug's chunk as if it answered.
    if not matched_drugs:
        return jsonify({
            "answer": "Information for this query was not found in the Vaagai Pharm drug knowledge base. Please check the drug name or specify which medicine you are asking about.",
            "sources": []
        })

    q_emb = embed_text(question)

    scored = []
    for r in rows:
        content = r["content"]
        content_lower = content.lower()
        title_lower = r["title"].lower()

        # If question contains subject tokens outside intent keywords, at least one must match chunk, title, or doc drug names
        if subject_tokens:
            doc_entity_names = doc_drugs.get(r["document_id"], set())
            if not any(st in content_lower or st in title_lower or st in doc_entity_names for st in subject_tokens):
                continue

        try:
            emb = json.loads(r["embedding"])
            sim = cosine_similarity(q_emb, emb)
        except Exception:
            continue

        score = sim

        # Drug name boost: boost chunks belonging to the drug mentioned in question
        if matched_drugs:
            doc_entity_names = doc_drugs.get(r["document_id"], set())
            if any(md in doc_entity_names or md in content_lower or md in title_lower for md in matched_drugs):
                score += 0.35
            else:
                score -= 0.20

        # Section heading intent boost: boost chunks whose content starts with matched heading
        if matched_headings:
            for h in matched_headings:
                h_l = h.lower()
                if (content_lower.startswith(h_l) or
                    content_lower.startswith(h_l + ":") or
                    content_lower.startswith(h_l + " for")):
                    score += 0.25
                    break

        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Explicit threshold check: if no relevant chunk found above threshold
    threshold = 0.12
    valid_chunks = [item for item in scored if item[0] >= threshold]

    if not valid_chunks:
        return jsonify({
            "answer": "Information for this query was not found in the Vaagai Pharm drug knowledge base. Please check the drug name or upload relevant documentation in Manage Sources.",
            "sources": []
        })

    # Prepare context and sources
    context_blocks = []
    sources_dict = {}
    for score_val, r in valid_chunks[:5]:
        context_blocks.append(f"Document: {r['title']}\n{r['content']}")
        key = (r["title"], r["source_type"], r["source_url"])
        if key not in sources_dict:
            sources_dict[key] = {
                "title": r["title"],
                "source_type": r["source_type"],
                "source_url": r["source_url"]
            }

    combined_context = "\n\n---\n\n".join(context_blocks)
    sources = list(sources_dict.values())

    # Check if question or any retrieved chunk concerns dosage
    is_dosage_query = (
        any(re.search(r"\b" + re.escape(w) + r"\b", q_lower) for w in ["dose", "dosage", "doses", "dosing", "how much", "how often", "maximum", "administration"])
        or any("dosage and administration" in item[1]["content"][:60].lower() for item in valid_chunks[:2])
    )
    dosage_warning = "Dosage information must be verified by a pharmacist before dispensing."

    # Call Groq LLM with strict grounding prompt
    key = os.getenv("GROQ_API_KEY")
    if key:
        body = {
            "model": GROQ_MODEL,
            "temperature": 0.2,
            "max_completion_tokens": 600,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are VaagaiBot Drug Information Assistant for Vaagai Pharm. "
                        "Answer the question using ONLY the provided drug context below. "
                        "If the provided context does not contain enough information to answer the question, "
                        "state clearly that the answer is not found in the knowledge base. "
                        "Strictly never speculate, never guess, and never use outside knowledge. "
                        "Keep answers precise, clinical, and helpful. "
                        "Always remind that this is for reference only and does not replace a pharmacist's or clinician's professional judgment."
                    )
                },
                {
                    "role": "user",
                    "content": f"Provided Drug Context:\n{combined_context}\n\nQuestion: {question}"
                }
            ]
        }
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            json.dumps(body).encode(),
            {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "VaagaiPharm/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                ans = (json.load(r)["choices"][0]["message"]["content"] or "").strip()
                if ans:
                    if is_dosage_query and dosage_warning not in ans:
                        ans += f"\n\n{dosage_warning}"
                    return jsonify({"answer": ans, "sources": sources})
        except Exception as e:
            print("[drug-chat groq error]", type(e).__name__, e)

    # Deterministic fallback answer: top 2 relevant chunks (same drug preferred), labeled with source title
    top_1 = valid_chunks[0][1]
    chosen_chunks = [top_1]

    if len(valid_chunks) > 1:
        same_doc_chunk = next((item[1] for item in valid_chunks[1:] if item[1]["document_id"] == top_1["document_id"]), None)
        if same_doc_chunk:
            chosen_chunks.append(same_doc_chunk)
        else:
            chosen_chunks.append(valid_chunks[1][1])

    chunk_paragraphs = []
    for ch in chosen_chunks:
        chunk_paragraphs.append(f"From {ch['title']}:\n\n{ch['content']}")

    fallback_ans = "\n\n".join(chunk_paragraphs) + "\n\n(Reference information only. Always verify with official drug labeling and a registered pharmacist.)"

    if is_dosage_query and dosage_warning not in fallback_ans:
        fallback_ans += f"\n\n{dosage_warning}"

    return jsonify({"answer": fallback_ans, "sources": sources})


@app.post("/api/drug-documents/upload")
def upload_drug_document():
    if PdfReader is None:
        return jsonify(error="PDF extraction library (pypdf/PyPDF2) is not available"), 500

    if "file" not in request.files:
        return jsonify(error="No file uploaded"), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify(error="Invalid file"), 400

    filename = f.filename
    if not filename.lower().endswith(".pdf"):
        return jsonify(error="Only .pdf files are accepted"), 400

    header = f.stream.read(4)
    f.stream.seek(0)
    if not header.startswith(b"%PDF"):
        return jsonify(error="Invalid PDF: File must begin with %PDF header"), 400

    title = str(request.form.get("title", "")).strip() or filename

    try:
        reader = PdfReader(f.stream)
        text_pages = []
        for p in reader.pages:
            t = p.extract_text()
            if t:
                text_pages.append(t)
        full_text = "\n\n".join(text_pages)
    except Exception as e:
        return jsonify(error=f"Could not read PDF: {str(e)}"), 400

    if not full_text.strip():
        return jsonify(error="PDF contained no readable text"), 400

    chunks = chunk_text(full_text, target_words=240, overlap_words=40)
    if not chunks:
        return jsonify(error="Could not split text into chunks"), 400

    with db() as c:
        cur = c.execute(
            "INSERT INTO drug_documents(title, source_type, source_url) VALUES (?, ?, ?)",
            (title, "uploaded_pdf", None)
        )
        doc_id = cur.lastrowid
        for chunk in chunks:
            emb = embed_text(chunk)
            c.execute(
                "INSERT INTO drug_chunks(document_id, content, embedding) VALUES (?, ?, ?)",
                (doc_id, chunk, json.dumps(emb))
            )

    return jsonify({"ok": True, "document_id": doc_id, "title": title, "chunks_count": len(chunks)}), 201


@app.post("/api/drug-documents/fetch-openfda")
def fetch_openfda():
    data = request.get_json(silent=True) or {}
    drug_name = str(data.get("drug_name", "")).strip()
    if not drug_name:
        return jsonify(error="Drug name is required"), 400

    encoded_name = urllib.parse.quote(drug_name)
    url = f'https://api.fda.gov/drug/label.json?search=openfda.generic_name:"{encoded_name}"&limit=1'
    req = urllib.request.Request(url, headers={"User-Agent": "VaagaiPharm/1.0"})
    doc_data = None

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            doc_data = json.load(resp)
    except Exception:
        # Fallback to search by brand name
        url_brand = f'https://api.fda.gov/drug/label.json?search=openfda.brand_name:"{encoded_name}"&limit=1'
        req_brand = urllib.request.Request(url_brand, headers={"User-Agent": "VaagaiPharm/1.0"})
        try:
            with urllib.request.urlopen(req_brand, timeout=15) as resp_b:
                doc_data = json.load(resp_b)
                url = url_brand
        except Exception:
            return jsonify(error=f"Could not retrieve drug label from openFDA for '{drug_name}'"), 404

    results = doc_data.get("results", []) if doc_data else []
    if not results:
        return jsonify(error=f"No openFDA label found for '{drug_name}'"), 404

    label = results[0]
    title = f"{drug_name.title()} (openFDA Label)"
    openfda = label.get("openfda", {})

    # Build sections
    chunks_to_insert = []

    # 1. Generic Name and Class (always stored, regardless of description)
    gen_parts = []
    generic_name = openfda.get("generic_name")
    brand_name = openfda.get("brand_name")
    pharm_class = openfda.get("pharm_class_epc") or openfda.get("pharm_class_cs")

    if generic_name:
        g_val = ", ".join(generic_name) if isinstance(generic_name, list) else str(generic_name)
        gen_parts.append(f"Generic Name: {g_val}")
    else:
        gen_parts.append(f"Generic Name: {drug_name.title()}")

    if brand_name:
        b_val = ", ".join(brand_name) if isinstance(brand_name, list) else str(brand_name)
        gen_parts.append(f"Brand Name(s): {b_val}")

    if pharm_class:
        p_val = ", ".join(pharm_class) if isinstance(pharm_class, list) else str(pharm_class)
        gen_parts.append(f"Pharmacologic Class: {p_val}")

    gen_chunk_text = f"Generic Name and Class for {drug_name.title()}:\n" + "\n".join(gen_parts)
    chunks_to_insert.append(gen_chunk_text)

    # 2. Other clinical sections (capped to sensible size before chunking)
    sections = [
        ("Indications and Usage", label.get("indications_and_usage")),
        ("Dosage and Administration", label.get("dosage_and_administration")),
        ("Warnings and Precautions", label.get("warnings") or label.get("warnings_and_cautions")),
        ("Adverse Reactions", label.get("adverse_reactions")),
        ("Contraindications", label.get("contraindications")),
    ]

    if label.get("description"):
        sections.append(("Description", label.get("description")))

    MAX_SECTION_WORDS = 1500  # Cap very long sections before chunking

    for heading, field_val in sections:
        if not field_val:
            continue
        text_val = " ".join(field_val) if isinstance(field_val, list) else str(field_val)
        words = text_val.split()
        if len(words) > MAX_SECTION_WORDS:
            text_val = " ".join(words[:MAX_SECTION_WORDS]) + "..."
        subchunks = chunk_text(f"{heading} for {drug_name.title()}:\n{text_val}")
        for sc in subchunks:
            chunks_to_insert.append(sc)

    if not chunks_to_insert:
        return jsonify(error="openFDA record had no clinical text sections to extract"), 400

    # Prevent duplicates: if document with same source_type 'openFDA' and same drug name/source_url exists,
    # replace its chunks (delete old document, insert new)
    refreshed = False
    with db() as c:
        existing_docs = c.execute(
            """SELECT id FROM drug_documents
               WHERE source_type = 'openFDA' AND (LOWER(title) = ? OR source_url = ?)""",
            (title.lower(), url)
        ).fetchall()

        if not existing_docs:
            existing_docs = c.execute(
                """SELECT id FROM drug_documents
                   WHERE source_type = 'openFDA' AND LOWER(title) LIKE ?""",
                (f"%{drug_name.lower()}%",)
            ).fetchall()

        if existing_docs:
            refreshed = True
            for ed in existing_docs:
                c.execute("DELETE FROM drug_documents WHERE id = ?", (ed["id"],))

        cur = c.execute(
            "INSERT INTO drug_documents(title, source_type, source_url) VALUES (?, ?, ?)",
            (title, "openFDA", url)
        )
        doc_id = cur.lastrowid
        for chunk in chunks_to_insert:
            emb = embed_text(chunk)
            c.execute(
                "INSERT INTO drug_chunks(document_id, content, embedding) VALUES (?, ?, ?)",
                (doc_id, chunk, json.dumps(emb))
            )

    resp_data = {
        "ok": True,
        "document_id": doc_id,
        "title": title,
        "chunks_count": len(chunks_to_insert),
        "source_url": url,
        "refreshed": refreshed
    }
    return jsonify(resp_data), 201


@app.get("/api/drug-documents")
def list_drug_documents():
    with db() as c:
        rows = c.execute("""
            SELECT d.id, d.title, d.source_type, d.source_url, d.created_at,
                   COUNT(dc.id) AS chunks_count
            FROM drug_documents d
            LEFT JOIN drug_chunks dc ON d.id = dc.document_id
            GROUP BY d.id
            ORDER BY d.id DESC
        """).fetchall()
    return jsonify({"documents": [dict(r) for r in rows]})


@app.delete("/api/drug-documents/<int:doc_id>")
def delete_drug_document(doc_id):
    with db() as c:
        existing = c.execute("SELECT id FROM drug_documents WHERE id = ?", (doc_id,)).fetchone()
        if not existing:
            return jsonify(error="Document not found"), 404
        c.execute("DELETE FROM drug_documents WHERE id = ?", (doc_id,))
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 5000)))
