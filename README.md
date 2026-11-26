# Vaagai Pharm - Pharmacy & Clinical Intelligence Platform

> **Vaagai** (meaning *"victory tree / banyan"* in Tamil) symbolizes enduring strength, resilience, and holistic healing. **Vaagai Pharm** combines community pharmacy stock management, retrieval-augmented drug intelligence (RAG), and preventive clinical risk screening into a unified, privacy-first platform.

---

## Architecture Overview

Vaagai Pharm operates on a lightweight, dependency-free architecture: **Flask + SQLite + vanilla HTML/CSS/JS** with Groq API for generative intelligence (and deterministic offline fallbacks).

```
vaagai-pharm/
├── app.py                      # Unified Flask web backend and REST API
├── requirements.txt            # Python dependencies
├── .env                        # Environment secrets (GROQ_API_KEY)
├── README.md
│
├── ml/                         # Clinical ML models & knowledge bases
│   ├── model.joblib            # Trained Random Forest classifier
│   ├── metrics.json            # Model evaluation metrics
│   ├── chatbot.py              # Clinical screening chatbot engine
│   └── data/
│       ├── diabetes.csv        # Pima Indians diabetes study reference
│       └── medical_faqs.json   # Deterministic clinical FAQ base
│
├── templates/
│   └── index.html              # Responsive single-page application (4 primary tabs)
│
├── static/
│   └── logo.png                # Brand logo asset
│
└── instance/
    └── vaagaipharm.db          # Unified SQLite database (auto-created & migrated)
```

---

## Core Capabilities

### 1. Pharmacy Stock Management Dashboard (`#dashboard` - Default Landing)
- **Real-Time Inventory Tracking:** Stock levels, active ingredients, minimum required quantities, and batch expiration dates.
- **Rule-Based Stock Alert Badges:**
  - `Low Stock` (red): Current stock &le; minimum required threshold.
  - `Near Expiry` (amber): Expiry date &le; today + 30 days.
  - `OK` (neutral green): Healthy stock and valid expiry.
- **Dynamic Sorting & Instant Filtering:** Search-as-you-type filter by medicine name or active ingredient; sortable columns.
- **Inventory CRUD:** Add, update, and delete medicines with instant live modal forms.
- **Low-Stock CSV Export:** One-click generation and download of low-stock items with suggested reorder quantities:
  `Suggested Reorder Qty = max(0, minimum_required_stock - current_stock)`.

### 2. Drug Information Chatbot — RAG (`#drug-chat`)
- **Strict Retrieval-Augmented Generation:** Uses a 256-dimensional semantic hashing embedder and cosine similarity over chunked medical texts.
- **Anti-Hallucination Guardrails:** If no relevant chunk matches above threshold, VaagaiBot explicitly reports that the drug or query is not present in the ingested knowledge base, refusing to guess.
- **Verified Source Attribution:** Responses link directly to openFDA source records or local PDF monograph titles.
- **Source Management & Ingestion:**
  - One-click ingestion from the official **US openFDA API** (`indications_and_usage`, `dosage_and_administration`, `warnings`, `adverse_reactions`).
  - Multipart **PDF monograph upload** with automated text extraction (`pypdf`), chunking, and embedding.

### 3. Diabetes Risk Screening (`#test`)
- **2-Minute Clinical Risk Wizard:** Evaluates routine metrics (glucose, BMI, blood pressure, age, pregnancies, pedigree) using a Random Forest classifier.
- **Attributable Factor Analysis:** What-if counterfactual explanations identifying personalized lifestyle targets.
- **Downloadable & Printable Clinical Reports:** Plain-language guidance with local storage and instant report sheet printing.

### 4. Clinic Analytics Dashboard (`#clinics`)
- **Cohort Performance:** In-depth evaluation metrics (Recall, Specificity, Brier Score, ROC-AUC) on the reference study.
- **Live Local Audit Log:** Anonymous log of recent screenings performed on the device.

---

## API Endpoints

### Pharmacy Stock (`/api/inventory`)
- `GET /api/inventory` — List all medicines with computed `is_low_stock` and `is_near_expiry` flags.
- `POST /api/inventory` — Add a new medicine item.
- `PUT /api/inventory/<id>` — Update an existing medicine item.
- `DELETE /api/inventory/<id>` — Delete a medicine item.
- `GET /api/inventory/export-low-stock` — Download CSV of low-stock items.

### Drug Information RAG (`/api/drug-*`)
- `POST /api/drug-chat` — RAG Q&A endpoint taking `{ question }` and returning `{ answer, sources }`.
- `GET /api/drug-documents` — List all ingested documents and sources.
- `POST /api/drug-documents/fetch-openfda` — Fetch and ingest drug label from openFDA API.
- `POST /api/drug-documents/upload` — Ingest uploaded clinical PDF.
- `DELETE /api/drug-documents/<id>` — Remove a document and its chunks.

### Clinical Risk & Chat
- `POST /api/predict` — Diabetes risk prediction with counterfactual explanations.
- `GET /api/dashboard` — Clinic analytics cohort statistics.
- `POST /api/chat` — VaagaiBot conversational diabetes guidance.

---

## Database Schema (`vaagaipharm.db`)

1. **`inventory`**: `id`, `medicine_name`, `active_ingredient`, `current_stock`, `minimum_required_stock`, `expiry_date`, `created_at`.
2. **`drug_documents`**: `id`, `title`, `source_type` (`openFDA` / `uploaded_pdf`), `source_url`, `created_at`.
3. **`drug_chunks`**: `id`, `document_id`, `content`, `embedding` (JSON float array).
4. **`assessments`**: Anonymous clinical risk screening records.

*Note: If an older `instance/uyirscore.db` is present, it is automatically migrated into `instance/vaagaipharm.db` on startup.*

---

## Setup & Running Instructions

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.10 – 3.14)

### 2. Environment Configuration
Create or update `.env` in the root folder:
```env
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-120b
```
*(If Groq API key is not supplied, the platform automatically uses built-in clinical explanations and deterministic RAG extractors).*

### 3. Start the Server
```powershell
python app.py
```

### 4. Access the Application
Open your web browser and visit:
```
http://127.0.0.1:5000
```
*(Defaults directly to the `#dashboard` Pharmacy Stock Management tab).*
