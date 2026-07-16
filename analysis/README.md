# Analysis

R scripts for mixed models on `data_prep/output/analysis_data/merged_data.xlsx`.

## Setup

```r
install.packages(c("readxl", "glmmTMB", "broom.mixed", "car", "ordinal"))
```

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

### Device stimulation (`device_stim.R`)

```bash
Rscript analysis/modelA/device_stim.R
```

Predictors: `cue_notice`, `rem_episode_count`, `condition`, `rem_minutes`, arousal flags, induction volume/cues, `rem_motion_avg`.

Binary predictors are factors (0/1, no z-score); continuous predictors are log1p + z-scored (`*_log_z`); `condition` is a factor.

Also writes collinearity diagnostics (`_collinearity.txt`, `_collinearity_correlation.csv`, `_collinearity_vif.csv`).

Optional: point to another merged file with `LUCIDREAMS_MERGED_DATA=/path/to/merged_data.xlsx`.

## Model B — sleep disruption cost

```bash
Rscript analysis/modelB/sleep_disruption_cost.R
```

Tests whether disruptive stimulation conditions (tactile=1, flashlight=2, audio=3) lower subjective sleep quality vs sham (0).

Outcomes (0–5 morning items):
- restlessness, wake-ups, waking difficulty, time to wake, morning tiredness

One **linear** and one **ordinal** GLMM per outcome: `outcome ~ condition + (1 | pid)`, reference = sham.

Results in `analysis/modelB/results/`, plus a combined `_all_coefficients_*.csv`.

## Model C — dream subscales (condition 4 vs 5, non-lucid nights)

```bash
Rscript analysis/modelC/dream_subscales_4vs5.R
```

Tests whether condition 5 yields higher lucidity-related subscale scores than condition 4 on nights without full lucid control (`lucid_state = 0`).

Outcomes (0–5 subscales):
- dream unreality, self/other-body awareness, no real-world consequences, reality checks, dream characters unreality

One **linear** and one **ordinal** GLMM per outcome: `subscale ~ condition + (1 | pid)`, reference = condition 4.

Results in `analysis/modelC/results/`, plus `_all_coefficients_*.csv`.
