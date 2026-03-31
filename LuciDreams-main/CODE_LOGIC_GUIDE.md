# LuciDreams Code Logic Guide

This document explains the implementation logic of the LuciDreams app in practical terms:
- what each major block does,
- how cue delivery works,
- why specific design choices were made,
- and how to safely modify behavior.

It is written as a maintainer guide for:
- `templates/luci.html` (source app logic),
- `cap-app/www/index.html` (Capacitor web bundle copy).

---

## 1) High-Level Runtime Flow

The app is a state-machine-driven sleep session controller:

1. Participant enters PID.
2. Training/voice setup runs.
3. Motion capture starts.
4. REM gate waits for first 4 hours (`REM_GATE_SECONDS`).
5. REM stillness logic detects an eligible period.
6. A train starts:
   - disruptive phase (2 minutes),
   - then induction phase (repeated cues every ~5s while still in induction logic).
7. Cues continue until arousal is detected or train/episode constraints end.
8. Session is saved to Firestore.

---

## 2) Two Files You Must Keep Aligned

- `templates/luci.html`: main source used by the web app.
- `cap-app/www/index.html`: Capacitor copy used for iOS packaging.

Whenever behavior changes in one, mirror it in the other unless a platform-specific difference is intentional.

---

## 3) Audio Model (Current)

### Beep-only cue behavior
Cue playback uses NativeAudio beep assets (no TTS path in active cue logic).

### Induction beep sequence
Induction steps follow this exact level list:

`[0.01, 0.02, 0.03, 0.05, 0.08, 0.25, 0.5, 0.75, 1.0]`

This maps to files:

`beep_v1`, `beep_v2`, `beep_v3`, `beep_v5`, `beep_v8`, `beep_v25`, `beep_v50`, `beep_v75`, `beep_v100`

### Disruptive beep
Condition 1 disruptive uses fixed `beep_v75`.

---

## 4) Asset Directories and Source of Truth

Important sound folders:
- `cap-app/www/sounds`
- `static/sounds`
- `cap-app/ios/App/App/sounds`
- (plus mirrored iOS root copies under `cap-app/ios/App/App`)

Current project state was aligned so these contain the same induction-relevant beep set.

---

## 5) REM / Train Control Logic

### Gate and stillness
- REM logic is enabled after `REM_GATE_SECONDS` (4h).
- Stillness compares current smoothed motion to evolving current-series distribution.

### 80% stillness cutoff details (exact logic)

The REM stillness gate is percentile-based, not a fixed absolute threshold.

1. Every second, raw motion is computed.
2. A rolling average is maintained over `REM_SMOOTH_WINDOW_SEC`.
   - Current code value is `180` seconds (3 minutes).
   - If you expected 2 minutes, that would be `120`.
3. Once the rolling window is full, `smoothedMotionCurrent` is appended to `smoothedMotionSeries`.
4. The dynamic cutoff is computed from the sorted current series using:
   - `REM_STILLNESS_PERCENT_OF_SERIES_MEAN = 0.8`
   - index: `floor((1 - 0.8) * (N - 1))` => approximately the 20th percentile.
5. Current stillness decision uses:
   - `belowCount = count(v in series where smoothedMotionCurrent < v)`
   - `isStillSmoothed = (belowCount / N) >= 0.8`

Interpretation:
- The current smoothed motion must be lower than about 80% of the evolving nightly distribution to be considered still enough.
- This is adaptive over time (series grows through the night), not fixed to only first-4h values.

Related fields persisted for analysis:
- `motion80pctCutoffCurrent` (latest dynamic cutoff),
- `motion80pctCutoffSeries` (history of cutoff values),
- `motion_per_second_series` in exported dynamic thresholds payload.

### How motion value is computed each second

Per-second motion is built from device accelerometer samples:

1. On each `devicemotion` event, the app reads `x, y, z` from `accelerationIncludingGravity`.
2. It computes frame-to-frame delta:
   - `diff = |x - oldX| + |y - oldY| + |z - oldZ|`
3. This `diff` is accumulated into `state.motionThisSecond` until the 1-second timer fires.
4. At each 1-second tick:
   - `motion = state.motionThisSecond`
   - `state.motionThisSecond` is reset to 0
5. A tilt/gravity robustness term is added:
   - `motionFromTilt = lastMagnitude < 2 ? lastMagnitude : abs(lastMagnitude - 9.81)`
   - where `lastMagnitude = sqrt(x^2 + y^2 + z^2)`
6. Final per-second motion is:
   - `motionPerSecond = motion + motionFromTilt`
7. That value is appended to:
   - `state.motionSamplesForBackend` (saved/exported),
   - and passed into REM detection pipeline (`processMotionPerSecond`).

Why this formulation:
- The delta term captures movement/change over short intervals.
- The tilt term helps capture orientation/gravity deviation effects and avoids underestimating movement in some postures.

### Train lifecycle
When train starts:
- `trainPhase = 'disruptive'`.
- disruptive behavior depends on condition.

After disruptive duration:
- `trainPhase = 'induction'`.
- induction cue starts immediately.

During induction:
- next cue check runs after `AROUSAL_CHECK_WINDOW_SEC` (5s),
- if still eligible and no arousal threshold reached for the train, volume steps to next induction level,
- when no next allowed step (or cap exceeded), train ends.

---

## 6) Arousal and Cap Logic

When arousal is detected during induction:
- last cue is marked `arousal_detected = true`,
- `state.arousal_threshold` is set to current induction volume,
- next train gets one-shot cap:
  - `nextTrainInductionCap = min(1, currentVolume * 0.95)`,
  - lower bounded by induction start volume.

This cap is consumed at next train start and reset.

---

## 7) Condition Behavior Summary

- `0` Control: no stimulation train output.
- `1` Disruptive beep train (`v75`) then induction sequence.
- `2` Disruptive vibration pattern then induction.
- `3` Disruptive light pulse then induction.
- `4` Voice condition:
  - disruptive phase still follows condition handling,
  - induction uses recorded voice variants mapped to the same step sequence as beeps.
- `5` Beep-focused cue condition (induction beep path).
- `6` Theta disruptive wave (fixed low wave volume) then induction.
- `7` Gamma disruptive wave (fixed low wave volume) then induction.

---

## 8) Voice Variant Logic

For condition 4 induction:
- the recorded voice blob is transformed into gain variants using the same step list as induction levels.
- step selection is nearest-match by volume level.
- variants are generated at runtime and cached in memory.

This keeps voice and beep conditions comparable in progression.

---

## 9) Why This Design

### Why file-mapped volume steps?
- Deterministic cue levels (`v1 -> v100`) are easier to audit and replicate.
- Avoids hidden behavior from changing global device volume.
- Easier offline validation against exported cue logs.

### Why keep fallback paths?
- Native plugin preload failures can happen.
- Fallback paths improve reliability in field runs.

---

## 10) How to Modify Safely

If changing stimulation behavior:

1. Edit `templates/luci.html`.
2. Mirror same logic in `cap-app/www/index.html`.
3. Ensure required beep assets exist in all active sound dirs.
4. Re-check condition-specific branch behavior.
5. Validate cue logs (`cue_events.csv` pipeline) against expected sequence.

---

## 11) Quick Reference (Most Important Knobs)

- `REM_GATE_SECONDS`: gate before REM logic starts.
- `REM_SMOOTH_WINDOW_SEC`: rolling window for smoothed motion.
- `REM_STILLNESS_PERCENT_OF_SERIES_MEAN`: stillness percentile rule (currently 0.8).
- `AROUSAL_CHECK_WINDOW_SEC`: induction cue spacing / arousal check interval.
- `INDUCTION_VOLUME_LEVELS`: induction step sequence.
- `DISRUPTIVE_PERIOD_SEC`: disruptive duration before induction.
- `NIGHT_MAX_TRAINS`, `REM_EPISODE_MAX_TRAINS`: caps.

---

## 12) Manual Firestore Retention Cleanup (Spark)

If Firestore TTL is not enabled (e.g., Spark plan), use:

- `cleanup_expired_firestore.py`

It deletes docs in collection group `sessions` where:
- `expires_at <= now`

Current retention window in code is **48h**.

Default mode is dry-run.

Examples:

```bash
cd "/Users/solenenoize/Desktop/LuciDreams/LuciDreams-main"

# Dry-run all participants
python3 cleanup_expired_firestore.py

# Apply delete all participants
python3 cleanup_expired_firestore.py --apply

# Dry-run one participant only
python3 cleanup_expired_firestore.py --pid Sole

# Apply delete one participant only
python3 cleanup_expired_firestore.py --pid Solene --apply
```

Optional flags:
- `--project lucidreans`
- `--service-account /path/to/service-account.json`
- `--batch-size 400`


