# Project Daphne NLP Service

This is a **FastAPI microservice** that uses **RapidFuzz** to match clinical entities from natural language text, to OMOP concepts.

The service works with no custom rules required, provided you have access to a omop table.

---

## Features

- Extracts clinical entities (PROBLEM, PROCEDURE, etc.) from free-text queries.
- Detects negation for entities.
- Returns structured JSON for easy integration with other services (like Laravel + OMOP tables).

---

## Installation

1. Clone the repository:

```bash
git clone <your-repo-url>
cd <repo-dir>
```

2. Create a Python virtual environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies

```bash
pip install pip --upgrade
pip install -r requirements.txt
```

4. Setup `.env`

```bash
DB_HOST=
DB_PORT=
DB_NAME=
DB_USER=
DB_PASS=


OMOP_VIEW=
APP_ENV=development
APP_DEBUG=true
```

## Running the service

```bash
uvicorn app:app --host=0.0.0.0 --port=5001 --reload
```

- GET `/` - Health check
- POST `/extract` - Endpoint for NLP queries

## Example request

```curl
curl -X POST http://localhost:5001/extract \
  -H "Content-Type: application/json" \
  -d '{"query": "Chronic kidney disease stage 3A due to type 2 diabetes mellitus"}'
```

## Example response

```json
{
  "entities": [
    { "text": "chronic kidney disease", "label": "PROBLEM", "negated": false },
    { "text": "type 2 diabetes mellitus", "label": "PROBLEM", "negated": false }
  ]
}
```
