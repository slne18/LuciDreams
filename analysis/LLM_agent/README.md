# LLM dream scoring pipeline

Converts free-text dream reports into numeric features for downstream analysis.

Located under `analysis/LLM_agent/`.

## Two workflows

| | **Stream A — API (preferred)** | **Stream B — Batch fallback** |
|---|-------------------------------|----------------------------------|
| **When** | Parley gateway is up | Parley UI works but script fails, or you prefer chat / batch scoring |
| **Scoring** | 1 night × 4 models (`score_dreams.py`) | ≤10 nights per call, 3 passes per metric (`score_dream_batch.txt`) |
| **Automation** | Fully scripted | Semi-manual (Parley chat) or scripted (`score_dreams_batch.py`) |
| **Output** | CSV ready for merge | JSON → CSV via `import_batch_json.py` |
| **Merge** | `merge_scores.py` | `merge_scores.py` (same) |

Both streams produce a **scores CSV** with `row_id` + `awareness_score`, `control_score`,
`cue_incorporation`, `bizarreness_count`.

---

## Configuration (`.env`)

Secrets go in **`analysis/LLM_agent/.env`** (gitignored). Loaded automatically by the API scripts.

```bash
PARLEY_API_KEY=your-parley-key-here
```

Optional Parley ensemble models:

```bash
PARLEY_MODEL_GPT=GPT-5.4 Mini
PARLEY_MODEL_CLAUDE=Claude Sonnet 4.6
PARLEY_MODEL_GEMINI=Gemini 3.1 Pro
PARLEY_MODEL_LLAMA=Llama 4 Maverick 17B
```

Install once from repo root:

```bash
pip3 install -r analysis/LLM_agent/requirements.txt
```

---

## Stream A — Automated API (Parley ensemble)

**1 night = 4 API calls** (GPT + Claude + Gemini + Llama), scores averaged.

### 1. Test

```bash
python3 analysis/LLM_agent/score_dreams.py \
  --provider parley-ensemble \
  --limit 1
```

### 2. Score all 224 nights

```bash
python3 analysis/LLM_agent/score_dreams.py \
  --provider parley-ensemble
```

Output: `output/dream_llm_scores_<timestamp>.csv`

Resume after interruption (same `--output` path):

```bash
python3 analysis/LLM_agent/score_dreams.py \
  --provider parley-ensemble \
  --output analysis/LLM_agent/output/dream_llm_scores_YYYYMMDD_HHMMSS.csv \
  --resume
```

### 3. Merge

```bash
python3 analysis/LLM_agent/merge_scores.py \
  --scores analysis/LLM_agent/output/dream_llm_scores_YYYYMMDD_HHMMSS.csv
```

Output: `output/merged_data_with_llm_scores.xlsx`

**Prompt:** `prompts/score_dream.txt`  
**Default models:** see table in [Default models](#default-models-parley-ensemble) below.

If Parley returns **503 / DEPLOYMENT_PAUSED**, use Stream B or retry later (see [Troubleshooting](#troubleshooting)).

---

## Stream B — Batch fallback (≤10 nights per request)

Use when Stream A is blocked or you score manually in the Parley chat UI.

**Key rule:** never paste all 224 nights at once — **max 10 data rows** per message.

### B1. Export dream text to CSV

```bash
python3 analysis/LLM_agent/export_dream_csv.py \
  --output analysis/LLM_agent/prompts/data_test.csv
```

Columns: `row_id`, `pid`, 5 Qualtrics fields, `dream_text` (224 rows).

### B2a. Manual scoring in Parley chat

1. **System prompt:** paste `prompts/score_dream_batch.txt`
2. **User message:** markdown table with ≤10 rows, e.g.:

```markdown
| row_id | pid | dream_text |
|--------|-----|------------|
| 0 | 269962413216 | ### Main dream narrative\nNothing from my dream is clear |
| 1 | 379882713876 | ### Main dream narrative\nNothing |
```

3. Save each JSON response to a file (`batch_0-9.json`, `batch_10-19.json`, …)
4. Convert JSON → CSV:

```bash
python3 analysis/LLM_agent/import_batch_json.py \
  --input analysis/LLM_agent/output/batch_0-9.json \
  --output analysis/LLM_agent/output/dream_llm_batch_scores.csv

python3 analysis/LLM_agent/import_batch_json.py \
  --input analysis/LLM_agent/output/batch_10-19.json \
  --output analysis/LLM_agent/output/dream_llm_batch_scores.csv \
  --append
```

Repeat `--append` for each batch file until all 224 row_ids are covered.

### B2b. Automated batch API (when Parley gateway is up)

Same batch prompt, but scripted (10 nights per call, ~23 calls total):

```bash
python3 analysis/LLM_agent/score_dreams_batch.py --batch-size 10
```

Output CSV is already compatible with `merge_scores.py`.

### B3. Merge (same as Stream A)

```bash
python3 analysis/LLM_agent/merge_scores.py \
  --scores analysis/LLM_agent/output/dream_llm_batch_scores.csv
```

**Batch JSON format** (compact — from model):

```json
{
  "n": 2,
  "row_id": [0, 1],
  "awareness": [[1, 1, 2], [4, 4, 3]],
  "control": [[1, 1, 1], [2, 3, 2]],
  "cue": [[0, 0, 0], [1, 1, 0]],
  "bizarre": [[0, 0, 0], [1, 2, 1]]
}
```

Main CSV columns (`awareness_score`, etc.) = mean of the 3 passes. Triple columns
(`awareness_score_1`, …) are also saved.

---

## Default models (Parley ensemble — Stream A)

| Label | Model | `.env` variable |
|-------|-------|-----------------|
| `gpt` | GPT-5.4 Mini | `PARLEY_MODEL_GPT` |
| `claude` | Claude Sonnet 4.6 | `PARLEY_MODEL_CLAUDE` |
| `gemini` | Gemini 3.1 Pro | `PARLEY_MODEL_GEMINI` |
| `llama` | Llama 4 Maverick 17B | `PARLEY_MODEL_LLAMA` |

---

## Outputs per night

| Column | Type | Meaning |
|--------|------|---------|
| `awareness_score` | 1–5 | Explicit dream / lucid awareness |
| `control_score` | 1–5 | Intentional dream control |
| `cue_incorporation` | 0/1 | Study cue appeared in dream plot |
| `bizarreness_count` | int ≥ 0 | Count of distinct bizarre events |

Stream A also saves per-model columns (`awareness_score_gpt`, …).  
Stream B saves triple passes (`awareness_score_1`, `_2`, `_3`, …).

---

## Input text fields

From `data_prep/output/analysis_data/merged_data.xlsx` — five Qualtrics free-text columns
concatenated per row. See `dream_text_columns.py`.

---

## Other providers (optional)

Direct OpenAI / Anthropic / Gemini keys in `.env` — use `--provider openai|anthropic|gemini`
with `score_dreams.py`. Legacy 3-key ensemble: `--provider ensemble`.

---

## Troubleshooting

### Parley 503 / DEPLOYMENT_PAUSED

Gateway paused on KSU side — not your key or data. Stream A and `score_dreams_batch.py`
will fail preflight. **Switch to Stream B manual chat**, or retry later.

### `{"scores":[]}` or `{"error":"batch too large..."}`

Too many rows in one batch message. Use **≤10 data rows** per request.

### `merge_scores.py` expects CSV, not JSON

Always convert batch JSON with `import_batch_json.py` (manual) or use output from
`score_dreams.py` / `score_dreams_batch.py` directly.

---

## Files

| File | Role |
|------|------|
| `score_dreams.py` | Stream A — 1 night, 4 models |
| `score_dreams_batch.py` | Stream B — automated batch API |
| `export_dream_csv.py` | Export dream text CSV from merged data |
| `import_batch_json.py` | Stream B — JSON batches → scores CSV |
| `merge_scores.py` | Join scores CSV → enriched merged data |
| `prompts/score_dream.txt` | Stream A system prompt |
| `prompts/score_dream_batch.txt` | Stream B system prompt |
| `build_dream_text.py` | Concatenate text fields |
| `batch_scoring.py` | Batch JSON validation |
| `llm_client.py` | API client |
| `parley.py` | Parley gateway helpers |
| `.env` | API key (gitignored) |

## Example downstream models

Using `merged_data_with_llm_scores.xlsx`:

- `awareness_score ~ condition + (1 | pid)`  
- `cue_incorporation ~ condition + cue_notice + (1 | pid)`  
- `bizarreness_count ~ disruptive_arousal_any + time_asleep + (1 | pid)`  
