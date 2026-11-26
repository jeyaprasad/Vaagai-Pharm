"""Tests for Vaagai Pharm API endpoints.
Tests run using Flask test client with a temporary database, leaving instance/vaagaipharm.db untouched.
"""
import csv
import datetime
import io
import json
import os
import sys
import unittest.mock as mock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import app, db, seed_initial_data


@pytest.fixture
def client(tmp_path):
    test_db = str(tmp_path / "test_vaagaipharm.db")
    app.config["DATABASE"] = test_db
    app.config["TESTING"] = True

    # Seed the temporary database
    seed_initial_data()

    with app.test_client() as client:
        yield client


# =========================================================================
# 1. Inventory Flags & Expiry Status Tests
# =========================================================================
def test_inventory_flags_and_summary(client):
    today = datetime.date.today()
    # Add items with specific dates
    # Expired item: 5 days ago
    client.post("/api/inventory", json={
        "medicine_name": "Expired Drug",
        "current_stock": 20,
        "minimum_required_stock": 10,
        "expiry_date": (today - datetime.timedelta(days=5)).isoformat()
    })
    # Near expiry item: 15 days in future (not expired)
    client.post("/api/inventory", json={
        "medicine_name": "Near Expiry Drug",
        "current_stock": 20,
        "minimum_required_stock": 10,
        "expiry_date": (today + datetime.timedelta(days=15)).isoformat()
    })
    # Fresh item: 100 days in future
    client.post("/api/inventory", json={
        "medicine_name": "Fresh Drug",
        "current_stock": 50,
        "minimum_required_stock": 10,
        "expiry_date": (today + datetime.timedelta(days=100)).isoformat()
    })
    # Both low stock and expired
    client.post("/api/inventory", json={
        "medicine_name": "Low and Expired Drug",
        "current_stock": 2,
        "minimum_required_stock": 10,
        "expiry_date": (today - datetime.timedelta(days=20)).isoformat()
    })

    res = client.get("/api/inventory")
    assert res.status_code == 200
    data = res.get_json()

    items = {i["medicine_name"]: i for i in data["items"]}

    # Expired drug: is_expired True, is_near_expiry True (expires within 30 days)
    assert items["Expired Drug"]["is_expired"] is True
    assert items["Expired Drug"]["is_near_expiry"] is True
    assert items["Expired Drug"]["is_low_stock"] is False

    # Near expiry drug: is_expired False, is_near_expiry True
    assert items["Near Expiry Drug"]["is_expired"] is False
    assert items["Near Expiry Drug"]["is_near_expiry"] is True
    assert items["Near Expiry Drug"]["is_low_stock"] is False

    # Fresh drug: neither near expiry nor expired
    assert items["Fresh Drug"]["is_expired"] is False
    assert items["Fresh Drug"]["is_near_expiry"] is False
    assert items["Fresh Drug"]["is_low_stock"] is False

    # Low and expired drug: both flags True
    assert items["Low and Expired Drug"]["is_low_stock"] is True
    assert items["Low and Expired Drug"]["is_expired"] is True
    assert items["Low and Expired Drug"]["is_near_expiry"] is True

    # Summary checks
    summary = data["summary"]
    assert "expired_count" in summary
    assert "low_stock_count" in summary
    assert "near_expiry_count" in summary
    assert summary["expired_count"] >= 2
    assert summary["total_items"] == len(data["items"])


def test_export_low_stock_csv(client):
    res = client.get("/api/inventory/export-low-stock")
    assert res.status_code == 200
    assert "text/csv" in res.content_type

    text = res.data.decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)

    # Header check
    assert rows[0] == ["Medicine Name", "Active Ingredient", "Current Stock", "Minimum Required", "Suggested Reorder Qty"]

    # Verify each row is low stock and suggested reorder qty = max(0, min - current)
    for row in rows[1:]:
        med_name, ingr, cur_str, min_str, reorder_str = row
        current = int(cur_str)
        minimum = int(min_str)
        reorder = int(reorder_str)
        assert current <= minimum
        assert reorder == max(0, minimum - current)


# =========================================================================
# 2. Inventory Validation Errors & 404s
# =========================================================================
def test_inventory_validation_errors(client):
    # Missing medicine_name
    r = client.post("/api/inventory", json={"current_stock": 10})
    assert r.status_code == 400
    assert "Medicine name is required" in r.get_json()["error"]

    # Missing current_stock
    r = client.post("/api/inventory", json={"medicine_name": "Aspirin"})
    assert r.status_code == 400
    assert "Current stock is required" in r.get_json()["error"]

    # Negative current_stock
    r = client.post("/api/inventory", json={"medicine_name": "Aspirin", "current_stock": -5})
    assert r.status_code == 400
    assert "Current stock cannot be negative" in r.get_json()["error"]

    # Negative minimum_required_stock
    r = client.post("/api/inventory", json={"medicine_name": "Aspirin", "current_stock": 10, "minimum_required_stock": -2})
    assert r.status_code == 400
    assert "Minimum required stock cannot be negative" in r.get_json()["error"]

    # Invalid minimum_required_stock string
    r = client.post("/api/inventory", json={"medicine_name": "Aspirin", "current_stock": 10, "minimum_required_stock": "invalid"})
    assert r.status_code == 400
    assert "Minimum required stock must be a valid integer" in r.get_json()["error"]

    # Default minimum_required_stock when blank/missing defaults to 10
    r = client.post("/api/inventory", json={"medicine_name": "Default Min Drug", "current_stock": 15})
    assert r.status_code == 201
    item_id = r.get_json()["id"]

    inv_res = client.get("/api/inventory")
    item = next(i for i in inv_res.get_json()["items"] if i["id"] == item_id)
    assert item["minimum_required_stock"] == 10

    # Invalid expiry_date
    r = client.post("/api/inventory", json={"medicine_name": "Date Test", "current_stock": 10, "expiry_date": "not-a-date"})
    assert r.status_code == 400
    assert "Expiry date must be a valid date in YYYY-MM-DD format" in r.get_json()["error"]

    r = client.post("/api/inventory", json={"medicine_name": "Date Test", "current_stock": 10, "expiry_date": "2026-02-30"})
    assert r.status_code == 400
    assert "Expiry date must be a valid date in YYYY-MM-DD format" in r.get_json()["error"]


def test_inventory_404s(client):
    # PUT non-existent item
    r = client.put("/api/inventory/999999", json={"medicine_name": "Non Existent", "current_stock": 10})
    assert r.status_code == 404
    assert r.get_json() == {"error": "Item not found"}

    # DELETE non-existent item
    r = client.delete("/api/inventory/999999")
    assert r.status_code == 404
    assert r.get_json() == {"error": "Item not found"}

    # Valid DELETE
    r_add = client.post("/api/inventory", json={"medicine_name": "To Delete", "current_stock": 10})
    del_id = r_add.get_json()["id"]
    r_del = client.delete(f"/api/inventory/{del_id}")
    assert r_del.status_code == 200
    assert r_del.get_json()["ok"] is True

    # Deleting again should return 404
    r_del_again = client.delete(f"/api/inventory/{del_id}")
    assert r_del_again.status_code == 404


# =========================================================================
# 3. Drug RAG Retrieval & Routing Tests
# =========================================================================
def test_rag_unindexed_drug_and_off_topic(client):
    # Unindexed drug
    r = client.post("/api/drug-chat", json={"question": "dosage of aspirin"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["sources"] == []
    assert "not found in the Vaagai Pharm drug knowledge base" in data["answer"]

    # Another unindexed drug with indications intent
    r = client.post("/api/drug-chat", json={"question": "what is ibuprofen used for"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["sources"] == []
    assert "not found in the Vaagai Pharm drug knowledge base" in data["answer"]

    # Off-topic question
    r = client.post("/api/drug-chat", json={"question": "What is the capital of France?"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["sources"] == []
    assert "not found in the Vaagai Pharm drug knowledge base" in data["answer"]

    # Empty question
    r = client.post("/api/drug-chat", json={"question": ""})
    assert r.status_code == 400

    # Whitespace question
    r = client.post("/api/drug-chat", json={"question": "   \n\t  "})
    assert r.status_code == 400


def test_rag_question_without_drug_name(client):
    # Question with no drug name specified must not return an arbitrary chunk
    r1 = client.post("/api/drug-chat", json={"question": "What is the maximum daily dose?"})
    assert r1.status_code == 200
    d1 = r1.get_json()
    assert d1["sources"] == []
    assert "not found in the Vaagai Pharm drug knowledge base" in d1["answer"]

    r2 = client.post("/api/drug-chat", json={"question": "What are the side effects?"})
    assert r2.status_code == 200
    d2 = r2.get_json()
    assert d2["sources"] == []
    assert "not found in the Vaagai Pharm drug knowledge base" in d2["answer"]


def test_rag_brand_name_and_generic_name_matching(client):
    mock_openfda_lisinopril = {
        "results": [
            {
                "openfda": {
                    "generic_name": ["Lisinopril"],
                    "brand_name": ["Prinivil", "Zestril"],
                    "pharm_class_epc": ["Angiotensin Converting Enzyme Inhibitor"]
                },
                "indications_and_usage": [
                    "Lisinopril is indicated for the treatment of hypertension in adult patients and pediatric patients 6 years of age and older."
                ],
                "dosage_and_administration": [
                    "Initial dose for hypertension is 10 mg once daily. Dosage may be increased up to 40 mg daily."
                ],
                "warnings": [
                    "Fetal Toxicity: When pregnancy is detected, discontinue Lisinopril as soon as possible."
                ],
                "adverse_reactions": [
                    "Common adverse reactions are headache, dizziness, and cough."
                ],
                "contraindications": [
                    "Lisinopril is contraindicated in patients with hereditary angioedema."
                ]
            }
        ]
    }

    class MockHTTP:
        def __init__(self, data):
            self.data = json.dumps(data).encode("utf-8")
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    with mock.patch("urllib.request.urlopen", return_value=MockHTTP(mock_openfda_lisinopril)):
        r_fetch = client.post("/api/drug-documents/fetch-openfda", json={"drug_name": "Lisinopril"})
        assert r_fetch.status_code == 201

    # 1. Query by brand name "Zestril"
    r_zestril = client.post("/api/drug-chat", json={"question": "What is the dosage of Zestril?"})
    assert r_zestril.status_code == 200
    d_zestril = r_zestril.get_json()
    assert len(d_zestril["sources"]) > 0
    assert any("Lisinopril" in s["title"] for s in d_zestril["sources"])
    assert "Dosage information must be verified by a pharmacist before dispensing." in d_zestril["answer"]
    assert "Initial dose for hypertension is 10 mg" in d_zestril["answer"]

    # 2. Query by brand name "Prinivil"
    r_prinivil = client.post("/api/drug-chat", json={"question": "What is Prinivil used for?"})
    assert r_prinivil.status_code == 200
    d_prinivil = r_prinivil.get_json()
    assert len(d_prinivil["sources"]) > 0
    assert any("Lisinopril" in s["title"] for s in d_prinivil["sources"])
    assert "treatment of hypertension" in d_prinivil["answer"]

    # 3. Query by generic name "Lisinopril"
    r_lisinopril = client.post("/api/drug-chat", json={"question": "Lisinopril side effects and warnings"})
    assert r_lisinopril.status_code == 200
    d_lisinopril = r_lisinopril.get_json()
    assert len(d_lisinopril["sources"]) > 0
    assert any("Lisinopril" in s["title"] for s in d_lisinopril["sources"])


def test_rag_drug_ingested_later_recognized_immediately(client):
    # Before ingestion, query for Lisinopril should return not found
    r_before = client.post("/api/drug-chat", json={"question": "Lisinopril dosage"})
    assert r_before.status_code == 200
    assert r_before.get_json()["sources"] == []

    mock_openfda_lisinopril = {
        "results": [
            {
                "openfda": {
                    "generic_name": ["Lisinopril"],
                    "brand_name": ["Zestril"],
                    "pharm_class_epc": ["ACE Inhibitor"]
                },
                "indications_and_usage": ["Hypertension."],
                "dosage_and_administration": ["Initial dose is 10 mg daily."],
                "warnings": ["Fetal toxicity."],
                "adverse_reactions": ["Cough."],
                "contraindications": ["Angioedema."]
            }
        ]
    }

    class MockHTTP:
        def __init__(self, data):
            self.data = json.dumps(data).encode("utf-8")
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    with mock.patch("urllib.request.urlopen", return_value=MockHTTP(mock_openfda_lisinopril)):
        r_fetch = client.post("/api/drug-documents/fetch-openfda", json={"drug_name": "Lisinopril"})
        assert r_fetch.status_code == 201

    # Immediately query without restart - must recognize Lisinopril dynamically from DB
    r_after = client.post("/api/drug-chat", json={"question": "Lisinopril dosage"})
    assert r_after.status_code == 200
    d_after = r_after.get_json()
    assert len(d_after["sources"]) > 0
    assert any("Lisinopril" in s["title"] for s in d_after["sources"])
    assert "Initial dose is 10 mg daily." in d_after["answer"]
    assert "Dosage information must be verified by a pharmacist before dispensing." in d_after["answer"]


def test_dosage_warning_with_and_without_groq(client, monkeypatch):
    dosage_query = "What is the dosage for Metformin?"

    # Case 1: Without GROQ_API_KEY (deterministic fallback)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    r1 = client.post("/api/drug-chat", json={"question": dosage_query})
    assert r1.status_code == 200
    ans1 = r1.get_json()["answer"]
    assert "Dosage information must be verified by a pharmacist before dispensing." in ans1

    # Case 2: With GROQ_API_KEY (mocked LLM completion)
    monkeypatch.setenv("GROQ_API_KEY", "fake_test_groq_key")

    mock_llm_response = {
        "choices": [
            {
                "message": {
                    "content": "Metformin starting dose is 500 mg orally twice daily or 850 mg once daily."
                }
            }
        ]
    }

    class MockHTTP:
        def __init__(self, data):
            self.data = json.dumps(data).encode("utf-8")
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    with mock.patch("urllib.request.urlopen", return_value=MockHTTP(mock_llm_response)):
        r2 = client.post("/api/drug-chat", json={"question": dosage_query})
        assert r2.status_code == 200
        ans2 = r2.get_json()["answer"]
        assert "500 mg orally twice daily" in ans2
        assert "Dosage information must be verified by a pharmacist before dispensing." in ans2


def test_rag_right_source_and_dosage_warning(client):
    # Metformin dosage
    r = client.post("/api/drug-chat", json={"question": "Metformin dosage"})
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["sources"]) > 0
    source_titles = [s["title"] for s in data["sources"]]
    assert any("Metformin" in t for t in source_titles)
    # Check dosage warning
    assert "Dosage information must be verified by a pharmacist before dispensing." in data["answer"]
    # Check top content is dosage
    assert "Dosage and Administration" in data["answer"]

    # Generic alternative to Amoxicillin
    r = client.post("/api/drug-chat", json={"question": "generic alternative to Amoxicillin"})
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["sources"]) > 0
    source_titles = [s["title"] for s in data["sources"]]
    assert any("Amoxicillin" in t for t in source_titles)
    # Generic Name and Class should be retrieved
    assert "Generic Name and Class" in data["answer"]
    assert "Amoxicillin" in data["answer"]


def test_rag_no_groq_fallback_format(client, monkeypatch):
    # Ensure GROQ_API_KEY is not set
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    r = client.post("/api/drug-chat", json={"question": "What are the indications for Atorvastatin?"})
    assert r.status_code == 200
    data = r.get_json()
    answer = data["answer"]

    # Fallback format includes "From <Title>:" and disclaimer
    assert "From Atorvastatin Calcium (openFDA Reference):" in answer
    assert "(Reference information only. Always verify with official drug labeling and a registered pharmacist.)" in answer


# =========================================================================
# 4. openFDA Ingestion & Deduplication Tests
# =========================================================================
def test_openfda_ingest_and_deduplication(client):
    mock_openfda_response = {
        "results": [
            {
                "openfda": {
                    "generic_name": ["Lisinopril"],
                    "brand_name": ["Prinivil", "Zestril"],
                    "pharm_class_epc": ["Angiotensin Converting Enzyme Inhibitor"]
                },
                "indications_and_usage": [
                    "Lisinopril is indicated for the treatment of hypertension in adult patients and pediatric patients 6 years of age and older."
                ],
                "dosage_and_administration": [
                    "Initial dose for hypertension is 10 mg once daily. Dosage may be increased up to 40 mg daily."
                ],
                "warnings": [
                    "Fetal Toxicity: When pregnancy is detected, discontinue Lisinopril as soon as possible."
                ],
                "adverse_reactions": [
                    "Most common adverse reactions are headache, dizziness, and cough."
                ],
                "contraindications": [
                    "Lisinopril is contraindicated in patients with a history of angioedema related to previous ACE inhibitor treatment."
                ]
            }
        ]
    }

    class MockHTTPResponse:
        def __init__(self, data):
            self.data = json.dumps(data).encode("utf-8")
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    # 1. First fetch
    with mock.patch("urllib.request.urlopen", return_value=MockHTTPResponse(mock_openfda_response)):
        r1 = client.post("/api/drug-documents/fetch-openfda", json={"drug_name": "Lisinopril"})
        assert r1.status_code == 201
        data1 = r1.get_json()
        assert data1["ok"] is True
        assert "Lisinopril" in data1["title"]
        assert data1.get("refreshed") is False or data1.get("refreshed") is None or data1.get("refreshed") == False
        doc_id_1 = data1["document_id"]

    # Verify generic name chunk and adverse reactions chunk were created
    with db() as c:
        chunks = [r["content"] for r in c.execute("SELECT content FROM drug_chunks WHERE document_id = ?", (doc_id_1,)).fetchall()]
        assert any(ch.startswith("Generic Name and Class for Lisinopril:") for ch in chunks)
        assert any("Brand Name(s): Prinivil, Zestril" in ch for ch in chunks)
        assert any("Adverse Reactions for Lisinopril:" in ch for ch in chunks)

    # 2. Second fetch for same drug should replace previous chunks and return refreshed: True
    with mock.patch("urllib.request.urlopen", return_value=MockHTTPResponse(mock_openfda_response)):
        r2 = client.post("/api/drug-documents/fetch-openfda", json={"drug_name": "Lisinopril"})
        assert r2.status_code == 201
        data2 = r2.get_json()
        assert data2["ok"] is True
        assert data2.get("refreshed") is True
        doc_id_2 = data2["document_id"]

    # Verify no duplicate document in database: only 1 Lisinopril document should exist
    with db() as c:
        docs = c.execute("SELECT id, title FROM drug_documents WHERE LOWER(title) LIKE '%lisinopril%'").fetchall()
        assert len(docs) == 1
        assert docs[0]["id"] == doc_id_2
        # Old doc chunks should be gone
        old_chunks = c.execute("SELECT COUNT(*) as count FROM drug_chunks WHERE document_id = ?", (doc_id_1,)).fetchone()["count"]
        if doc_id_1 != doc_id_2:
            assert old_chunks == 0


# =========================================================================
# 5. Upload Size & Type Limits Tests
# =========================================================================
def test_upload_type_and_header_limits(client):
    # Non-pdf extension
    txt_file = (io.BytesIO(b"Clinical notes"), "notes.txt")
    r = client.post("/api/drug-documents/upload", data={"file": txt_file}, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "Only .pdf files are accepted" in r.get_json()["error"]

    # .pdf extension but lacks %PDF header
    fake_pdf = (io.BytesIO(b"Hello world, fake pdf content"), "fake.pdf")
    r = client.post("/api/drug-documents/upload", data={"file": fake_pdf}, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "File must begin with %PDF header" in r.get_json()["error"]

    # Missing file field
    r = client.post("/api/drug-documents/upload", data={}, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "No file uploaded" in r.get_json()["error"]


def test_upload_size_limit(client):
    # Generate content larger than 10 MB (10 * 1024 * 1024 + 1024 bytes)
    oversized_data = b"%PDF" + (b"0" * (10 * 1024 * 1024 + 1024))
    large_file = (io.BytesIO(oversized_data), "oversized.pdf")

    r = client.post("/api/drug-documents/upload", data={"file": large_file}, content_type="multipart/form-data")
    assert r.status_code == 413
    data = r.get_json()
    assert "File size exceeds" in data["error"]


# =========================================================================
# 6. Admin Key Write Gate Tests
# =========================================================================
def test_admin_key_gate(client, monkeypatch):
    monkeypatch.setenv("VAAGAI_ADMIN_KEY", "super_secret_pharm_key")

    # POST /api/inventory without key -> 401
    r = client.post("/api/inventory", json={"medicine_name": "Locked Drug", "current_stock": 5})
    assert r.status_code == 401
    assert "Unauthorized" in r.get_json()["error"]

    # POST with wrong key -> 401
    r = client.post("/api/inventory", headers={"X-Admin-Key": "wrong_key"}, json={"medicine_name": "Locked Drug", "current_stock": 5})
    assert r.status_code == 401

    # POST with correct key -> 201
    r = client.post("/api/inventory", headers={"X-Admin-Key": "super_secret_pharm_key"}, json={"medicine_name": "Locked Drug", "current_stock": 5})
    assert r.status_code == 201
    item_id = r.get_json()["id"]

    # PUT without key -> 401
    r = client.put(f"/api/inventory/{item_id}", json={"medicine_name": "Locked Drug", "current_stock": 10})
    assert r.status_code == 401

    # PUT with correct key -> 200
    r = client.put(f"/api/inventory/{item_id}", headers={"X-Admin-Key": "super_secret_pharm_key"}, json={"medicine_name": "Locked Drug", "current_stock": 10})
    assert r.status_code == 200

    # DELETE without key -> 401
    r = client.delete(f"/api/inventory/{item_id}")
    assert r.status_code == 401

    # DELETE with correct key -> 200
    r = client.delete(f"/api/inventory/{item_id}", headers={"X-Admin-Key": "super_secret_pharm_key"})
    assert r.status_code == 200

    # GET requests should remain open without admin key
    r = client.get("/api/inventory")
    assert r.status_code == 200
    r = client.get("/api/drug-documents")
    assert r.status_code == 200
