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

Copy `.env.example` to `.env` and fill in your database credentials. A minimal setup only needs the DB connection:

```bash
DB_HOST=
DB_PORT=
DB_NAME=
DB_USER=
DB_PASS=

# Optional: OMOP vocabulary DB (concept_synonym lives here). Defaults to DB_NAME.
OMOP_DB_NAME=

APP_ENV=development
```

Every other variable below is optional and has a sensible built-in default, so the service runs unconfigured. See **[Configuration](#configuration)** for the full list.

## Configuration

All variables are read from the environment (via `.env`). Everything except the `DB_*` credentials is optional — each falls back to a default and the service degrades cleanly when a variable (or a dependent table/service) is absent.

### Concept-match scoring tiers

The SQL resolver and `/concepts/search` score each candidate concept with a tiered `CASE` expression: an exact name match beats a "contains" match, which beats a prefix match, with a token-level match and a synonym boost applied on top. These tiers are fully env-configurable so scoring can be tuned without a redeploy.

```bash
CONCEPT_MATCH_SCORE_EXACT=1000    # concept_name equals the query exactly
CONCEPT_MATCH_SCORE_CONTAINS=500  # concept_name contains the query
CONCEPT_MATCH_SCORE_PREFIX=100    # concept_name starts with the query
CONCEPT_MATCH_SCORE_SYNONYM=1000  # boost when matched via a concept_synonym entry
CONCEPT_MATCH_SCORE_TOKEN=50      # per-token LIKE match
```

**Fallback when unset:** each has a hard-coded default in `resolvers/sql_helpers.py`
(`EXACT=10000`, `CONTAINS=500`, `PREFIX=100`, `SYNONYM=1000`, `TOKEN=50`), so scoring works with none of them set. The values in `.env.example` are recommended overrides — note `CONCEPT_MATCH_SCORE_EXACT` in particular sets a lower `1000` than the built-in `10000` default.

> Collection-based scoring (a logarithmic popularity nudge) is quantised into fixed buckets and caps at 80 — deliberately below the prefix tier (100) so popularity only reorders concepts *within* a match tier and never outranks a better text match. This is **not** env-configurable; see `_NCOLLECTIONS_TIERS` / `_COUNT_TIERS` in `resolvers/sql_helpers.py`.

### MedCAT clinical-term expansion (optional)

Point `MEDCAT_URL` at a running [MedCAT](https://github.com/CogStack/MedCAT) service to expand clinical terms before querying (used by both `/extract`'s SQL resolver and `/concepts/search`).

```bash
MEDCAT_URL=https://hdr-gateway-medcat-dev-987760029877.europe-west1.run.app
MEDCAT_MIN_ACC=0.5   # min accuracy for an "Affirmed" annotation to be used
```

**Fallback when unset:** if `MEDCAT_URL` is empty or absent, no MedCAT client is constructed and term expansion is silently skipped — matching proceeds on the raw query text. `MEDCAT_MIN_ACC` defaults to `0.5`.

### Warm-up snapshot cache (dev only)

```bash
STORE_REFRESH_TTL=600   # cache lifetime / background-refresh cadence (seconds)

# Dev only (APP_ENV=development): path for the warm-up snapshot cache. On uvicorn --reload
# the tokenised concepts + synonym/acronym maps are restored from this file instead of
# re-querying MySQL, making reloads near-instant. Rebuilt automatically if deleted.
# It does NOT track code/config changes — delete it after editing the tokeniser, rules.json,
# or the concept load query to force a fresh rebuild. Ignored entirely in production.
CONCEPTS_CACHE_PATH=.concepts_cache.pkl
```

**Fallbacks when unset:** `STORE_REFRESH_TTL` defaults to `60`. `CONCEPTS_CACHE_PATH` defaults to `.concepts_cache.pkl` but is **only** honoured when `APP_ENV=development`; in production it is ignored entirely and concepts are always loaded from MySQL.

### Logging / debug

```bash
APP_ENV=development   # "development" enables the dev warm-up snapshot cache; else "production"
LOG_LEVEL=DEBUG       # INFO (default) or DEBUG — DEBUG adds per-step resolver/warm-up timings
APP_DEBUG=true        # present in .env.example but currently a no-op — see below
```

**Note on `APP_DEBUG`:** this variable is **not read anywhere in the code** and has no effect. Use `LOG_LEVEL=DEBUG` to enable verbose logging (SQL resolver step timings, warm-up phase timings, raw SQL).

For the remaining tuning knobs (`DEFAULT_THRESHOLD`, `FUZZY_*`, `RESOLVER_BACKEND`, `RESOLVER_MAX_MATCHES`, `ACRONYM_ENABLED`, `COLLECTION_BOOST_WEIGHT`), see the annotated `.env.example`.

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
