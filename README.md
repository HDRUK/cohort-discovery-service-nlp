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

## Experimental: LLM-backed query parsing

`POST /llm/parse` uses a **local** LLM, served by [Ollama](https://ollama.com), to turn free text into the query-builder JSON the UI renders.

The split is deliberate. The model decides the **structure** — which terms are OR'd, what nests, what is a demographic rather than a clinical term, whether an age describes the patient or the event. The existing concept resolver decides the **concepts**: the model never sees or invents an OMOP `concept_id`, it emits search terms with the concept slot left blank and `resolver.search()` fills them in.

Nothing here is required to run the service. With `OLLAMA_URL` unset, `/llm/parse` returns `503` and every other endpoint behaves exactly as before. No new dependency: `httpx` is already present for MedCAT.

### Endpoints

`GET /llm/models` — what is pulled, and what is currently resident in memory.

```bash
curl -s localhost:5001/llm/models | python3 -m json.tool
```

```json
{
  "default": "qwen3:8b",
  "total": 3,
  "models": [
    { "name": "qwen3:8b", "size_gb": 5.23, "parameter_size": "8.2B",
      "quantization": "Q4_K_M", "loaded": true }
  ],
  "loaded": [
    { "name": "qwen3:8b", "size_gb": 5.5, "expires_at": "2026-09-22T13:04:11Z" }
  ]
}
```

`POST /llm/parse` — parse a query.

```bash
# structure only: no database calls, every concept left null
curl -s 'localhost:5001/llm/parse?fill_concepts=false' \
  -H 'Content-Type: application/json' \
  -d '{"query":"adults with cancer or diabetes"}' | python3 -m json.tool

# full path: concepts resolved
curl -s localhost:5001/llm/parse \
  -H 'Content-Type: application/json' \
  -d '{"query":"adults with cancer or diabetes"}' | python3 -m json.tool

# compare a different model without restarting
curl -s localhost:5001/llm/parse \
  -H 'Content-Type: application/json' \
  -d '{"query":"adults with cancer or diabetes","model":"qwen3:14b"}'
```

Body: `query` (required), `fill_concepts` (default `true`), `model` (optional override).
Query params: `fill_concepts` (overrides the body field), `threshold`, `phrase_first`, `max_matches` (default 10 — the best match plus up to nine alternatives).

The response returns the raw model output alongside the converted tree, so when something looks wrong you can tell immediately whether the model or the conversion is at fault:

```json
{
  "plan": { "...": "what the model emitted" },
  "tree": { "...": "query-builder JSON" },
  "warnings": [],
  "model": "qwen3:8b",
  "duration_ms": { "llm": 4360.4, "resolve": 304.7 }
}
```

### How it works

```
query string
     │
     ▼  llm/ollama_client.py — POST /api/chat with format=<JSON schema>
   plan      compact: {age, sex, race, death, op, rules}
     │
     ▼  llm/query_plan.py — plan_to_tree(), a pure function
   tree      query-builder JSON: uuids minted, operators interleaved,
             constraints applied, every rule.concept = null
     │
     ▼  llm/concept_filler.py — resolver.search() per leaf, in a thread pool
   tree      rule.concept = best match, .alternatives = the rest
```

The model does **not** emit the query-builder tree directly. It emits a compact intermediate plan which Python converts. UUID minting and infix operator interleaving are mechanical and belong in code; Ollama compiles the response schema into a GBNF grammar where recursive schemas are unreliable but a flat one is not; and a pure conversion function is testable with no model and no database.

### What the plan can express

| Plan field | Becomes | Example query |
|---|---|---|
| `age` | `demographics.age` | "adults with asthma" |
| `sex` | `demographics.sex` (8507/8532) | "women with endometriosis" |
| `race` | `demographics.race` (8516/8527/8515/8657/8557) | "black men over 60" |
| `death` | `demographics.death` | "people who died", "still alive" |
| `op` | operator nodes: `and` / `or` / `followed_by` | "cancer or diabetes" |
| `rules[].term` | a leaf with `rule.concept` blank | "asthma" |
| `rules[].not` | `exclude: true` | "without eczema" |
| `rules[].terms` + `op` | a nested group | "insulin (glargine or detemir)" |
| `rules[].last_months` | `timeConstraint: [now-N, now]` | "in the last 2 years" |
| `rules[].age_min/max` | `ageConstraint` + `≥`/`<` | "under 60 when they fractured a hip" |
| `rules[].value_min/max` | `valueAsNumber` + `≥`/`<`/`↔` | "BMI over 30", "eGFR below 45" |

Not modelled: `location` (geographic filtering), occurrence counts ("2+ courses"), and consecutive-day windows.

### Current age vs age at the event

This is the judgement the prompt spends most effort on, because the two readings give different cohorts:

- **"women aged 18-45 with endometriosis"** — the age describes the *patient*, so it goes in `demographics.age`.
- **"women who were under 60 when they suffered a hip fracture"** — the age is tied to the *event* by "when", so it becomes `ageConstraint` on the hip-fracture rule.

A 70-year-old who broke a hip at 55 matches the second but not the first. When a query genuinely does not say, the plan falls back to current age, matching the existing pipeline.

Either way the endpoint reports which reading it took, in the same style as the Laravel parser's own warnings:

```
Age interpreted as the patient's current age >= 18. Please modify from the query builder if needed.
Age for "hip fracture" interpreted as the patient's age when the event was recorded <= 60, not their current age. Please modify from the query builder if needed.
```

`/llm/parse` also reuses `RuleEngine`'s existing `unsupported_patterns`, so queries mentioning visits, locations, temporal sequencing or measurement values get the same warnings `/extract` already emits.

### Install and start Ollama

```bash
brew install ollama          # macOS; see ollama.com/download for other platforms
ollama serve                 # foreground, or just launch Ollama.app
curl -s localhost:11434/api/version   # confirm it is listening
```

### Pull a model

```bash
ollama pull qwen3:8b         # 5.2 GB — the default
```

`ollama pull` is resumable: if it stalls, re-run the same command and it continues. Be aware that **it exits 0 even when the download fails**, so check `ollama list` or `GET /llm/models` rather than trusting the exit code.

### Manage models

```bash
ollama list                  # models on disk
ollama ps                    # models currently loaded in memory, and when they unload
ollama stop qwen3:8b         # unload from memory now (stays on disk)
ollama rm qwen3:0.6b         # delete from disk
```

Models load into memory on first use and stay resident for `OLLAMA_KEEP_ALIVE` (default `30m` here; Ollama's own default is 5 minutes). A cold load costs 10-30 s on the next request, which is why the service asks for a longer window.

| Model | Size | Notes |
|---|---|---|
| `qwen3:8b` | 5.2 GB | Default. Handles nested groups, negation, age bands, thresholds and time windows. |
| `qwen3:14b` | 9.3 GB | Better on ambiguous phrasing, roughly 2x slower. |
| `qwen3:0.6b` | 0.5 GB | Too small for real use — it misreads age bands. A plumbing smoke test only. |

### Keeping memory under control

Ollama keeps **up to 3 models resident at once** by default, which on a 36 GB machine is how you end up in swap. Cap it at one:

```bash
launchctl setenv OLLAMA_MAX_LOADED_MODELS 1     # Ollama.app
```

For a Homebrew-managed install, add it under `EnvironmentVariables` in `~/Library/LaunchAgents/sh.brew.ollama.plist` and `brew services restart ollama`.

`notebooks/model_manager.py` enforces the same thing per-request and refuses a load that will not fit, rather than letting the machine swap:

```bash
python notebooks/model_manager.py status        # RAM, swap, resident, what would fit
python notebooks/model_manager.py use qwen3:8b  # evict the rest, preload this one
python notebooks/model_manager.py unload        # free everything
```

Generation slows by roughly 3x once the machine is swapping, so a benchmark run taken under pressure is not comparable with one taken without it. `status()` reports swap so you can tell the difference.

### Configuration

```bash
OLLAMA_URL=http://localhost:11434   # unset disables /llm/parse and /llm/models entirely
OLLAMA_MODEL=qwen3:8b               # default model
OLLAMA_TIMEOUT=120                  # seconds
OLLAMA_KEEP_ALIVE=30m               # how long the model stays resident
```

### Speed

Generation dominates. A typical request profiles as ~10 ms model load, ~150 ms prompt eval for ~600 input tokens, and the remainder generating ~35 output tokens. Prompt size is almost free; **output tokens are the cost**. Hence:

1. **Thinking is disabled.** `qwen3` is a hybrid-reasoning model and, left to reason, it spent **112.7 s against 6.1 s for byte-identical output** — 9181 characters of reasoning that changed nothing. The client sends `"think": false` and falls back automatically for models with no thinking mode.
2. **The plan is compact.** The model copies the formatting of the examples it is shown, so pretty-printed examples made it emit whitespace tokens. Compacting them and trimming redundant fields took output from 112 tokens to 35.
3. **`keep_alive`** avoids a 10-30 s cold load on every request after a gap.

Two things measured as *not* worth doing:

- **A smaller model does not help.** `qwen3:4b` emitted *more* tokens than `qwen3:8b` for the same queries (144 vs 85) and was no faster. When generation is the bottleneck, a chattier small model loses.
- **The JSON schema costs nothing.** A full schema, `format: "json"`, and no format at all all measured within noise of each other.

If generation drops below ~20 tok/s on Apple silicon, suspect the machine rather than the model — check `sysctl vm.swapusage`. Under heavy swap the same query measured 3x slower (8 tok/s against 24). The response's `duration_ms` splits LLM time from resolution time so you can see which half is slow.

### Notebook

[`notebooks/llm_endpoints.ipynb`](notebooks/llm_endpoints.ipynb) is a sandbox for both endpoints. It opens with the full set of test queries — simple and advanced, annotated with the known ambiguities and the cases the platform cannot support — then walks through listing models, parsing a single query, the current-age vs age-at-event distinction, running the whole set into a DataFrame, and comparing against `/extract`.

```bash
pip install jupyter pandas
jupyter notebook notebooks/llm_endpoints.ipynb
```

It expects the service on `localhost:5001`.

Section 9 is a **scored** benchmark rather than a timing one: it pins down what the plan should contain for queries whose answer is not in doubt, then reports accuracy alongside speed for every model you have pulled. A fast model that puts the age in the wrong place is worse than a slow one that gets it right, so rank on accuracy first.

### Tests

```bash
pytest tests/test_query_plan.py    # plan_to_tree, no model, no DB
pytest tests/test_llm_router.py    # endpoints, model and resolver both mocked
pytest tests/test_ollama_client.py # request shape, thinking fallback, error handling
```

None of these need Ollama running or a database.
