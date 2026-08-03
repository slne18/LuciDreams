# Data prep pipeline

Scripts to export night data from Firebase, clean Qualtrics surveys, and build one analysis-ready table.

## Folders

| Path | Contents |
|---|---|
| `input/` | Raw Qualtrics exports (`onboarding.csv`, `dream_report.csv`) |
| `output/` | Firebase session exports (CSVs used to build hardware data) |
| `output/analysis_data/` | Clean survey tables, filtered hardware nights, merged analysis file |

## 1. Export from Firebase

From the repo root, install Python deps once (use a venv if you like):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r data_prep/requirements.txt
```

In Cursor/VS Code: **Python: Select Interpreter** → choose `.venv/bin/python` so imports like
`pandas` resolve in the editor.

```bash
python3 data_prep/export_night_data.py
```

**Incremental export** (only new Firebase sessions not already in `night_summary.csv`):

```bash
python3 data_prep/export_night_data.py --skip-existing
```

Matches on `session_doc_id`. Skips heavy motion/cue extraction for nights you already have
and **appends** new detail rows to the existing CSVs. Still downloads all sessions from
Firebase first — use `--pid` to limit scope if memory is tight.

This writes to `data_prep/output/`:

- `night_summary.csv` (required for hardware + merge diagnostics)
- `rem_episodes.csv`, `train_events.csv`, `cue_events.csv`, motion series CSVs

**Firebase auth** (pick one):

- Application Default Credentials (default): `gcloud auth application-default login`
- Service account: set `FIREBASE_SERVICE_ACCOUNT_PATH` or pass `--service-account path/to.json`

**Optional:** local JSON instead of Firestore:

```bash
python3 data_prep/export_night_data.py --local-file path/to/sessions.json
```

## 2. Add Qualtrics exports

Place fresh survey exports in `data_prep/input/`:

- `onboarding.csv`
- `dream_report.csv`

## 3. Clean survey data

```bash
python3 data_prep/process_onboarding.py
python3 data_prep/process_dream_report.py
```

Outputs:

- `output/analysis_data/onboarding_clean.csv` — baseline demographics (gender, age, LD frequency, sleep quality)
- `output/analysis_data/dream_report_clean.csv` — morning dream reports (filtered: ≥4 h, REM > 0)

## 4. Build hardware table

Requires step 1 (`night_summary.csv`, `rem_episodes.csv`, etc. in `output/`):

```bash
python3 data_prep/build_hardware_data.py
```

Output: `output/analysis_data/hardware_data.xlsx` — filtered nights (≥4 h, ≥1 REM episode, all native API flags true, deduplicated).

## 5. Merge for analysis

```bash
python3 data_prep/build_merged_data.py
```

Joins dream reports with hardware sessions (by `pid`, `device_time_start`, `rem_minutes`) and adds onboarding baseline by `pid`.

Outputs:

- `output/analysis_data/merged_data.xlsx` — main analysis file (one row per dream report / night)
- `output/analysis_data/merged_data_log.csv` — rows that could not be matched to hardware, with reasons

## Full run (after updating inputs)

```bash
python3 data_prep/export_night_data.py
python3 data_prep/process_onboarding.py
python3 data_prep/process_dream_report.py
python3 data_prep/build_hardware_data.py
python3 data_prep/build_merged_data.py
```

Use `merged_data.xlsx` for statistics. Nights from the same participant share baseline columns; use mixed models or cluster by `pid` when testing night-level outcomes.
