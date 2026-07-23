# LLM dream scoring pipeline

Converts free-text dream reports into numeric features for downstream analysis.
**Each night is scored in its own API call(s)** — the model never sees other participants'
reports in the same request.

Located under `analysis/LLM_agent/`.

## Configuration (`.env`)

All secrets and optional settings go in **`analysis/LLM_agent/.env`**. This file is
gitignored — never commit it. `score_dreams.py` loads it automatically on startup (via
`python-dotenv`); you do **not** need `export` in the terminal.

Minimal setup:

```bash
# analysis/LLM_agent/.env
PARLEY_API_KEY=your-parley-key-here
```

Optional model overrides (same file):

```bash
PARLEY_MODEL_GPT=GPT-5.4 Mini
PARLEY_MODEL_CLAUDE=Claude Sonnet 4.6
PARLEY_MODEL_GEMINI=Gemini 3.1 Pro
PARLEY_MODEL_LLAMA=Llama 4 Maverick 17B
```

For non-Parley providers, put the matching keys in the same `.env` file instead:

```bash
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
```

## Quick start (Parley — recommended)

Uses one [Parley](https://keys.theparley.org) API key and **4 models** per night (averaged).

### 1. Install dependencies

From the repo root:

```bash
pip3 install -r analysis/LLM_agent/requirements.txt
```

### 2. Add your API key to `.env`

Open `analysis/LLM_agent/.env` and set `PARLEY_API_KEY`. Save the file — every command
below picks it up automatically.

### 3. Preview (no API calls)

```bash
python3 analysis/LLM_agent/score_dreams.py \
  --provider parley-ensemble \
  --dry-run --limit 1
```

### 4. Test on one night (4 API calls)

```bash
python3 analysis/LLM_agent/score_dreams.py \
  --provider parley-ensemble \
  --limit 1
```

### 5. Score all 224 nights

```bash
python3 analysis/LLM_agent/score_dreams.py \
  --provider parley-ensemble
```

**896 API calls** total (224 nights × 4 models). Outputs go to
`analysis/LLM_agent/output/dream_llm_scores_<timestamp>.csv` and `.jsonl`.

If the run stops midway, resume with the **same output path**:

```bash
python3 analysis/LLM_agent/score_dreams.py \
  --provider parley-ensemble \
  --output analysis/LLM_agent/output/dream_llm_scores_YYYYMMDD_HHMMSS.csv \
  --resume
```

### 6. Merge scores into the analysis table

```bash
python3 analysis/LLM_agent/merge_scores.py \
  --scores analysis/LLM_agent/output/dream_llm_scores_YYYYMMDD_HHMMSS.csv
```

Writes `analysis/LLM_agent/output/merged_data_with_llm_scores.xlsx` (and `.csv`).

---

## Default models (`parley-ensemble`)

| Label | Model | `.env` variable |
|-------|-------|-----------------|
| `gpt` | GPT-5.4 Mini | `PARLEY_MODEL_GPT` |
| `claude` | Claude Sonnet 4.6 | `PARLEY_MODEL_CLAUDE` |
| `gemini` | Gemini 3.1 Pro | `PARLEY_MODEL_GEMINI` |
| `llama` | Llama 4 Maverick 17B | `PARLEY_MODEL_LLAMA` |

Use the **exact model name** from Parley's model picker. To change defaults, uncomment
and edit the lines in `.env`, or pass `--parley-models` on the command line:

```bash
python3 analysis/LLM_agent/score_dreams.py --provider parley-ensemble \
  --parley-models "gpt:GPT-5.4 Mini,claude:Claude Sonnet 4.6,gemini:Gemini 3.1 Pro,llama:Llama 4 Maverick 17B"
```

Single-model mode (cheaper, 224 calls):

```bash
python3 analysis/LLM_agent/score_dreams.py --provider parley --model "GPT-5.4 Mini"
```

---

## Outputs per night

| Column | Type | Meaning |
|--------|------|---------|
| `awareness_score` | 1–5 | Explicit dream / lucid awareness |
| `control_score` | 1–5 | Intentional dream control |
| `cue_incorporation` | 0/1 | Study cue (sound/vibration/light) appeared in dream plot |
| `bizarreness_count` | int ≥ 0 | Count of distinct bizarre/impossible dream events |

In `parley-ensemble` mode, the main columns are **averaged across the 4 models**:

| Main column | Aggregation |
|-------------|-------------|
| `awareness_score`, `control_score` | Mean (2 decimals) |
| `cue_incorporation` | Mean ≥ 0.5 → 1, else 0 (also `cue_incorporation_mean`) |
| `bizarreness_count` | Mean, rounded to nearest integer |

Per-model columns are also saved, e.g. `awareness_score_gpt`, `awareness_score_claude`,
`awareness_score_gemini`, `awareness_score_llama`.

Each CSV row includes `row_id`, `pid`, `condition`, `lucid_state`, `cue_notice`,
`llm_rationale`, and `error` if scoring failed.

---

## Input text fields

Five Qualtrics free-text columns from `data_prep/output/analysis_data/merged_data.xlsx`
are concatenated per row:

1. Main dream narrative  
2. When/how lucidity was recognized  
3. How cues appeared in the dream  
4. How lucidity began  
5. Other sleep thoughts/feelings  

See `dream_text_columns.py` for exact column names.

---

## Other providers (optional)

If you are **not** using Parley, add the provider API keys to `analysis/LLM_agent/.env`
and pick a provider:

| Provider | Flag | `.env` variable | Default model |
|----------|------|-----------------|---------------|
| OpenAI | `--provider openai` | `OPENAI_API_KEY` | `gpt-4o-mini` |
| Claude | `--provider anthropic` | `ANTHROPIC_API_KEY` | `claude-3-5-haiku-latest` |
| Gemini | `--provider gemini` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` | `gemini-2.0-flash` |

```bash
python3 analysis/LLM_agent/score_dreams.py --provider openai
```

### Legacy ensemble (3 separate API keys)

Add all three keys to `.env`, then run:

```bash
python3 analysis/LLM_agent/score_dreams.py --provider ensemble
```

Optional per-provider models (in `.env`): `LLM_DREAM_MODEL_OPENAI`,
`LLM_DREAM_MODEL_ANTHROPIC`, `LLM_DREAM_MODEL_GEMINI`.

---

## Troubleshooting

### Parley returns 503 / `DEPLOYMENT_PAUSED`

If scoring fails before any night is processed, you should see:

```text
Parley preflight check failed — no nights were scored.

Parley gateway is paused (DEPLOYMENT_PAUSED). This is a temporary KSU server outage ...
```

This means **the Parley server is down or paused** — not your API key, model names, or
`merged_data.xlsx`. Wait and retry the same command later.

If a run started before an outage, re-run with `--resume` and the same `--output` path;
failed rows (non-empty `error` column) are retried automatically.

### Empty scores in the CSV with a long `error` column

Same root cause as above when Parley was unavailable mid-run. Check the `error` column —
it now summarizes identical failures across all 4 models in one sentence instead of
repeating the raw API dump four times.

---

## Prompt

Scoring rubrics live in `prompts/score_dream.txt`. Edit to tune scoring; re-run rows you
want to refresh.

---

## Files

| File | Role |
|------|------|
| `score_dreams.py` | Main pipeline |
| `merge_scores.py` | Join scores to merged data |
| `build_dream_text.py` | Concatenate text fields |
| `llm_client.py` | API client + JSON validation |
| `parley.py` | Parley gateway config + ensemble helpers |
| `ensemble.py` | Multi-model averaging |
| `dream_text_columns.py` | Column name constants |
| `.env` | Local API key and settings (gitignored, auto-loaded) |

## Example downstream models

Using `merged_data_with_llm_scores.xlsx`:

- `awareness_score ~ condition + (1 | pid)`  
- `cue_incorporation ~ condition + cue_notice + (1 | pid)`  
- `bizarreness_count ~ disruptive_arousal_any + time_asleep + (1 | pid)`  
