# POC: Ollama-backed query parsing (`POST /llm/parse`)

A proof of concept that splits query parsing in two:

- a **local LLM** decides the *structure* — which terms are OR'd, what nests inside what, what is a demographic rather than a clinical term
- the **existing concept resolver** decides the *concepts* — the OMOP `concept_id` behind each term

The model never sees or invents a `concept_id`. It emits search terms with the concept slot left blank, and `resolver.search()` — the same call `parsing.py` makes for `/extract` — fills the blanks in.

This is experimental and opt-in. Nothing works differently when `OLLAMA_URL` is unset: the endpoint returns `503` and every other endpoint is untouched.

---

## Setup

Install and start Ollama, then pull a model:

```bash
brew install ollama        # if not already installed
ollama serve &             # or run the Ollama.app
ollama pull qwen3:8b       # 5.2 GB
ollama list                # confirm it landed
```

Add to your `.env`:

```
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
OLLAMA_TIMEOUT=120
```

Then start the service as usual:

```bash
uvicorn app:app --host 0.0.0.0 --port 5001 --reload
```

The endpoint works before concept warm-up finishes — the resolver falls back to reduced mode (LIKE-based SQL, no synonym or acronym enrichment), so matches are worse but not absent.

### Model sizing

Measured on an M3 Pro / 36 GB: `qwen3:8b` answers in 4-6 s and stays resident for five minutes between calls. Larger models are a per-request override, so you can compare without restarting anything:

| Model | Download | Notes |
|---|---|---|
| `qwen3:8b` | 5.2 GB | Default. Fast enough to iterate on the prompt. |
| `qwen3:14b` | 9.3 GB | Better on nested and ambiguous phrasing, roughly 2x slower. |
| `gpt-oss:20b` | ~14 GB | Strong structured output, slower again. |

---

## Using it

```bash
# structure only — no DB lookups, every concept left null
curl -s localhost:5001/llm/parse \
  -H 'Content-Type: application/json' \
  -d '{"query":"adults with cancer or diabetes","fill_concepts":false}' | python3 -m json.tool

# full path — concepts resolved
curl -s localhost:5001/llm/parse \
  -H 'Content-Type: application/json' \
  -d '{"query":"adults with cancer or diabetes"}' | python3 -m json.tool

# compare a bigger model without restarting
curl -s localhost:5001/llm/parse \
  -H 'Content-Type: application/json' \
  -d '{"query":"adults with cancer or diabetes","model":"qwen3:14b"}' | python3 -m json.tool
```

Request body: `query` (required), `fill_concepts` (default `true`), `model` (optional override).
Query params, mirroring `/extract`: `threshold`, `phrase_first`, and `max_matches`
(default 10 — the best match plus up to nine alternatives, matching what the Laravel API
already asks `/extract` for).

The response wraps four things — the raw model output, the tree, the warnings, and timings:

```json
{
  "plan":    { ... },
  "tree":    { ... },
  "warnings": [],
  "model": "qwen3:8b",
  "duration_ms": { "llm": 1840.2, "resolve": 96.4 }
}
```

`plan` is there deliberately. When the output is wrong it tells you immediately whether the model or the conversion is at fault.

---

## How it works

```
query string
     │
     ▼  llm/ollama_client.py — POST /api/chat with format=<JSON schema>
   plan      { demographics, combinator, rules: [term | group] }
     │
     ▼  llm/query_plan.py — plan_to_tree(), a pure function
   tree      query-builder JSON: uuids minted, operators interleaved,
             every rule.concept = null, searchTerm carried on each leaf
     │
     ▼  llm/concept_filler.py — resolver.search() per leaf, in a thread pool
   tree      rule.concept = best match, .alternatives = the rest
```

### Why the intermediate plan

The model does not emit the query-builder tree directly. It emits a smaller, flatter "query plan" which Python converts. That is a deliberate choice:

- Ollama's `format` compiles a JSON Schema into a GBNF grammar. A deliberately non-recursive two-level schema is reliable; recursive `$ref` is not.
- UUID minting, infix operator interleaving and the `constraints`/`valid` envelope are mechanical. Asking the model to do them wastes tokens and invites malformed output.
- `plan_to_tree` is a pure function, so it is unit-tested with no model and no database.

### The output shape

`tree` matches what the Laravel API's `POST /v1/parse-query` returns today — the shape `project-daphne-web` renders, defined by `RuleGroupType` in `src/types/rules.ts`.

Two properties of that shape are easy to get wrong:

- **Operators are infix.** `rules` is a flat array `[leaf, {combinator:"or"}, leaf]`, not a list of leaves with an operator property. Adjacent leaves fail the web validator (`RULE_NEEDS_OPERATOR`), and every operator in one group must share a combinator.
- **Every node needs its own UUID `id`.** The web store only backfills `demographics.id`.

### Speed

Generation dominates: for a typical query the profile is ~10 ms model load, ~150 ms prompt eval for ~600 input tokens, and the rest spent generating ~35 output tokens. Prompt size is almost free; **output tokens are the cost**. Three things follow.

1. **Thinking off** — see below. The single largest win, 18x.
2. **A lean plan** — the model emits a compact plan, not the query-builder tree. Compacting the JSON examples in the prompt (the model copies their formatting) and dropping redundant fields took output from 112 tokens to 35, a 69% cut.
3. **`keep_alive`** — the client sends `keep_alive: "30m"` (override with `OLLAMA_KEEP_ALIVE`). Ollama's default unloads after 5 minutes, and a cold load costs 10-30 s on the next call.

Two things that are *not* worth doing, both measured:

- **A smaller model does not help.** `qwen3:4b` generated *more* tokens than `qwen3:8b` for the same queries (144 vs 85) and was no faster overall. When generation is the bottleneck, a chattier small model loses.
- **The JSON schema costs nothing.** Constrained decoding with a full schema, with `format: "json"`, and with no format at all all measured within noise of each other.

If generation drops below ~20 tok/s on Apple silicon, suspect the machine rather than the model — check `sysctl vm.swapusage`. Under heavy swap the same query measured 3x slower (8 tok/s against 24).

### Thinking is disabled

`qwen3` is a hybrid-reasoning model. Left to think, it spends thousands of tokens reasoning before emitting the JSON: on `women over 60 with type 2 diabetes on insulin (glargine or detemir)` that measured **112.7 s with thinking against 6.1 s without, for byte-identical output** — 9181 characters of reasoning that changed nothing.

So the client sends `"think": false`. Models with no thinking mode reject that field with a 400, so `OllamaClient._post` retries once without it. Both paths are covered in `tests/test_ollama_client.py`.

### The blank concept slot

This already exists in the contract; it is not a POC invention. `ConceptOperator.concept` is typed `Concept | Concept[] | null`, and `web/src/utils/rules.ts` has `isEmptyRule`, `isUnknownRule` and `hasAlternatives` driving the "pick the intended concept" UI.

After filling, each leaf holds the best match with the remaining candidates on `concept.alternatives` — the same convention `RuleBuilderService.php` uses when it groups NLP entities by text span.

When a term resolves to nothing, the leaf gets the placeholder the frontend already renders — `{"concept_id": null, "name": "<term>", ...}`, which `isUnknownRule` picks up — plus the warning `/extract` uses verbatim.

`searchTerm` on each leaf is an extra field the web `Node` type does not declare. JSON tolerates it and it makes the blank-vs-filled diff legible, but it is a POC convenience, not a contract.

---

## Not modelled yet

The plan covers age, sex, death, relative time windows and clinical terms. These fall through:

- `followed_by` sequencing, geography, value-as-number
- race — the Laravel `DemographicsBuilder` hardcodes `[]` too

Nested groups are gated behind the web feature flag `QueryBuilderAllowNestedGroups`, which matters only if this is ever wired to the UI.

---

## Tests

```bash
pytest tests/test_query_plan.py   # plan_to_tree, no model, no DB
pytest tests/test_llm_router.py   # endpoint, model and resolver both mocked
pytest tests/test_ollama_client.py # request shape, thinking fallback, error handling
```

Neither test needs Ollama running or a database. `tests/conftest.py` stubs `app.state`, and `TestClient(app)` is constructed without `with`, so the lifespan never runs.
