# Adaptive RAG PDF Question Answering

A simple capstone-ready Adaptive Retrieval-Augmented Generation system.

## What makes it adaptive?

The query router dynamically selects retrieval depth:

- Simple/factual question -> top 3 chunks
- Explanatory question -> top 5 chunks
- Complex/multi-hop/comparison question -> top 8 chunks
- If the best semantic match is weak -> automatically expand the query and run a broader second retrieval pass
- Very weak matches are rejected to reduce hallucinations

The retrieved document passages are then sent to a local Ollama model, which is instructed to answer only from the supplied context and include source references.

## Architecture

PDFs -> PyMuPDF text extraction -> overlapping chunks -> SentenceTransformer embeddings -> FAISS index -> adaptive query router -> semantic retrieval -> confidence check -> optional query expansion -> Ollama -> grounded answer + page sources

## Recommended setup

Use Python 3.10 or 3.11.

### 1. Create virtual environment

Windows:

```bash
python -m venv venv
venv\Scripts\activate
```

macOS/Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install packages

```bash
pip install -r requirements.txt
```

### 3. Install Ollama and download a small model

```bash
ollama pull gemma3:1b
```

Make sure Ollama is running. The local API should be available at `http://localhost:11434`.

You can use another Ollama model by setting the environment variable `OLLAMA_MODEL`.

Windows PowerShell example:

```powershell
$env:OLLAMA_MODEL="gemma3:1b"
python app.py
```

### 4. Run

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Run tests

Install pytest and run the application tests:

```bash
pip install pytest
python -m pytest -q
```

## Main files

- `app.py` - Flask web application and API
- `database.py` - SQLite persistence for users, documents, settings and history
- `adaptive_rag.py` - ingestion, routing, adaptive retrieval and generation
- `templates/` and `static/` - application pages, styles and frontend scripts
- `test_app.py` - application tests
- `requirements.txt` - dependencies

## Good demo questions

Simple:
- What is the main objective of the proposed system?

Explanatory:
- Explain how the proposed approach improves question answering.

Complex:
- Compare the existing method and proposed method and explain why the proposed method is better.

The UI displays which adaptive route was selected for each question.
