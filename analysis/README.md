# Analysis

R scripts for mixed models on `data_prep/output/analysis_data/merged_data.xlsx`.

## Setup

```r
install.packages(c("readxl", "glmmTMB", "broom.mixed", "car", "ordinal"))
```

## Descriptive statistics (all Model A/B/C variables)

```bash
Rscript analysis/descriptive_stats.R
```

Summarises participant characteristics and all numeric/binary/categorical variables used in Models A, B, and C from `merged_data.xlsx`. Writes CSVs and a log to `analysis/results/`.

## Model A — participant factors

```bash
Rscript analysis/modelA/participants_factors.R
```

Outcome: `lucid_state` (binary lucid dream).

Predictors:
- `Age`, `Gender`, `baseline_LD_freq_ord`, `baseline_sleep_qual`
- `rem_minutes`, `time_asleep` (minutes)
- Morning survey items (restlessness, wake-ups, waking difficulty, time to wake, tiredness)

Continuous predictors are log1p-transformed and z-scored; `Gender` is a factor (most common level as reference).

Results are written to `analysis/modelA/results/` (`_summary.txt`, `_coefficients.csv`).

### Condition only (`conditions.R`)

```bash
Rscript analysis/modelA/conditions.R
```

Outcome: `lucid_state`. Single predictor: `condition` (factor; reference = most common level). Formula: `lucid ~ condition + (1 | pid)`.

### Device stimulation (`device_stim.R`)

```bash
Rscript analysis/modelA/device_stim.R
```

Predictors: `cue_notice`, `rem_episode_count`, `condition`, `rem_minutes`, arousal flags, induction volume/cues, `rem_motion_avg`.

Binary predictors are factors (0/1, no z-score); continuous predictors are log1p + z-scored (`*_log_z`); `condition` is a factor.

Also writes collinearity diagnostics (`_collinearity.txt`, `_collinearity_correlation.csv`, `_collinearity_vif.csv`).

Optional: point to another merged file with `LUCIDREAMS_MERGED_DATA=/path/to/merged_data.xlsx`.

## Model B — sleep disruption cost (all conditions)

```bash
Rscript analysis/modelB/sleep_disruption_cost.R
```

Tests whether stimulation `condition` affects subjective morning sleep quality across all recorded nights.

Outcomes (0–5 morning items):
- restlessness, wake-ups, waking difficulty, time to wake, morning tiredness

One **linear** and one **ordinal** GLMM per outcome: `outcome ~ condition + (1 | pid)`, reference = most common condition level.

Results in `analysis/modelB/results/`, plus a combined `_all_coefficients_*.csv`.

## Model C — dream subscales (all conditions, all nights)

```bash
Rscript analysis/modelC/dream_subscales.R
```

Tests whether dream lucidity subscale scores differ by stimulation `condition` across all recorded nights (no lucidity or condition subset).

Outcomes (0–5 subscales):
- dream unreality, self/other-body awareness, no real-world consequences, reality checks, dream characters unreality

One **linear** and one **ordinal** GLMM per outcome: `subscale ~ condition + (1 | pid)`, reference = most common condition level.

Results in `analysis/modelC/results/`, plus `_all_coefficients_*.csv`.

## LLM dream scoring (optional extra features)

See `LLM_agent/README.md`. Scores free-text dream reports into `awareness_score`, `control_score`, `cue_incorporation`, and `bizarreness_count` for additional models.
