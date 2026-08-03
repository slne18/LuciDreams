#!/usr/bin/env python3
"""
Estimate eye-movement activity from Muse frontal EEG (AF7/AF8 EOG proxy) and
compare pre-REM, intra-REM, and post-REM windows.

For each REM episode:
  - intra: [REM start, REM end]
  - pre:   same duration immediately before REM (matched window)
  - post:  same duration immediately after REM (matched window)

Metrics (per period):
  - active_seconds: 1 Hz bins where EOG envelope exceeds a robust threshold
  - active_fraction: active_seconds / period_duration

Outputs:
  - CSV with per-episode stats
  - PNG bar charts (per episode + session summary)
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
from plot_eeg_rem import estimate_sampling_hz  # noqa: E402
PROJECT_ROOT = os.path.dirname(BASE_DIR)
DEFAULT_DATA_DIR = os.path.join(PROJECT_ROOT, "data_prep", "output")
DEFAULT_REM_CSV = os.path.join(DEFAULT_DATA_DIR, "rem_episodes.csv")
EEG_DATA_DIR = os.path.join(BASE_DIR, "EEG")
OUTPUT_DIR = os.path.join(BASE_DIR, "eye_movement_plots")
SECONDS_PER_DAY = 24 * 3600

DEFAULT_EOG_BAND_LOW_HZ = 0.5
DEFAULT_EOG_BAND_HIGH_HZ = 4.0
DEFAULT_THRESHOLD_MAD_MULT = 2.5
DEFAULT_MIN_REM_SEC = 5.0
DEFAULT_CONTEXT_SEC = 900.0


def read_csv_robust(path: str, **kwargs: Any) -> pd.DataFrame:
    last_err: Optional[BaseException] = None
    for attempt in range(3):
        try:
            return pd.read_csv(path, **kwargs)
        except OSError as exc:
            last_err = exc
            if getattr(exc, "errno", None) != 89:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise OSError(
        f"Could not read {path} (Operation canceled / errno 89). "
        "Ensure the file is downloaded from iCloud, then retry."
    ) from last_err


def parse_hms_to_seconds(value: str) -> Optional[float]:
    if not value:
        return None
    try:
        dt = datetime.strptime(str(value)[:8], "%H:%M:%S")
        return float(dt.hour * 3600 + dt.minute * 60 + dt.second)
    except ValueError:
        return None


def sec_of_day_to_elapsed(sec_of_day: float, ref_sec_of_day: float) -> float:
    if sec_of_day >= ref_sec_of_day:
        return sec_of_day - ref_sec_of_day
    return sec_of_day + SECONDS_PER_DAY - ref_sec_of_day


def safe_slug(value: Optional[str]) -> str:
    s = "" if value is None else str(value)
    s = s.replace(":", "")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s or "unknown"


def choose_rem_session(
    rem_df: pd.DataFrame,
    pid: str,
    session_start_boston: Optional[str],
    night_number: Optional[int],
) -> pd.DataFrame:
    d = rem_df[rem_df["pid"].astype(str) == pid].copy()
    if "night_number" in d.columns:
        d["night_number"] = pd.to_numeric(d["night_number"], errors="coerce")
    if night_number is not None and "night_number" in d.columns:
        d = d[d["night_number"] == int(night_number)].copy()
    if session_start_boston:
        return d[d["session_start_boston"].astype(str) == session_start_boston].copy()
    if d.empty:
        return d
    if "night_number" in d.columns:
        grp_n = d.groupby("night_number").size().sort_index()
        if len(grp_n) > 0:
            d = d[d["night_number"] == int(grp_n.index.max())].copy()
    grp = d.groupby("session_start_boston").size().sort_values(ascending=False)
    sel = str(grp.index[0])
    return d[d["session_start_boston"].astype(str) == sel].copy()


def parse_rem_episodes(value: Optional[str]) -> Optional[List[int]]:
    if value is None or not str(value).strip():
        return None
    out: List[int] = []
    for part in str(value).split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out or None


def bandpass_filter(series: np.ndarray, fs_hz: float, low_hz: float, high_hz: float) -> np.ndarray:
    vals = np.asarray(series, dtype=float)
    finite = np.isfinite(vals)
    if finite.sum() < 10:
        return vals
    idx = np.arange(len(vals))
    interp = np.interp(idx, idx[finite], vals[finite])
    try:
        from scipy import signal
    except Exception:
        return vals
    nyq = fs_hz * 0.5
    lo = max(0.001, float(low_hz))
    hi = min(float(high_hz), nyq * 0.95)
    if hi <= lo:
        return vals
    b, a = signal.butter(4, [lo, hi], btype="bandpass", fs=float(fs_hz))  # type: ignore[misc]
    filt = signal.filtfilt(b, a, interp, method="pad")
    filt[~finite] = np.nan
    return filt


def envelope_1hz(elapsed_sec: np.ndarray, signal_1d: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Max |Hilbert envelope| per 1-second bin."""
    elapsed = np.asarray(elapsed_sec, dtype=float)
    sig = np.asarray(signal_1d, dtype=float)
    ok = np.isfinite(elapsed) & np.isfinite(sig)
    elapsed = elapsed[ok]
    sig = sig[ok]
    if len(sig) < 10:
        return np.array([]), np.array([])
    try:
        from scipy import signal as sp_signal
        env = np.abs(sp_signal.hilbert(sig))
    except Exception:
        env = np.abs(sig)
    sec_bins = np.floor(elapsed).astype(int)
    if len(sec_bins) == 0:
        return np.array([]), np.array([])
    max_sec = int(sec_bins.max())
    min_sec = int(sec_bins.min())
    out_sec = np.arange(min_sec, max_sec + 1, dtype=int)
    out_env = np.full(len(out_sec), np.nan, dtype=float)
    for i, s in enumerate(out_sec):
        mask = sec_bins == s
        if mask.any():
            out_env[i] = float(np.nanmax(env[mask]))
    return out_sec.astype(float), out_env


def active_seconds_from_envelope(
    sec_bins: np.ndarray,
    env: np.ndarray,
    win_start: float,
    win_end: float,
    threshold: float,
) -> Tuple[float, float, int]:
    if win_end <= win_start:
        return 0.0, 0.0, 0
    mask = (sec_bins >= win_start) & (sec_bins < win_end) & np.isfinite(env)
    if not mask.any():
        return 0.0, float(win_end - win_start), 0
    active = int(np.sum(env[mask] > threshold))
    dur = float(win_end - win_start)
    return float(active), dur, active


def robust_threshold(env_values: np.ndarray, mad_mult: float) -> float:
    vals = env_values[np.isfinite(env_values)]
    if len(vals) == 0:
        return float("inf")
    med = float(np.median(vals))
    mad = float(np.median(np.abs(vals - med)))
    if mad <= 0:
        mad = float(np.std(vals)) if len(vals) > 1 else 0.0
    return med + mad_mult * mad


def episode_duration_sec(ep: pd.Series) -> Optional[float]:
    dur = pd.to_numeric(ep.get("episode_duration_sec"), errors="coerce")
    if pd.notna(dur) and float(dur) > 0:
        return float(dur)
    start_sod = parse_hms_to_seconds(str(ep.get("episode_start_boston", "")))
    end_sod = parse_hms_to_seconds(str(ep.get("episode_end_boston", "")))
    if start_sod is None or end_sod is None:
        return None
    if end_sod >= start_sod:
        return end_sod - start_sod
    return end_sod + SECONDS_PER_DAY - start_sod


def resolve_eeg_csv(args: argparse.Namespace) -> str:
    if args.eeg_csv:
        return args.eeg_csv
    eeg_pid = args.eeg_pid or args.pid
    candidates: List[str] = []
    if args.night_number is not None:
        candidates.extend([
            os.path.join(EEG_DATA_DIR, f"EEG_{eeg_pid}{args.night_number}.csv"),
            os.path.join(BASE_DIR, f"EEG_{eeg_pid}{args.night_number}.csv"),
        ])
    candidates.extend([
        os.path.join(EEG_DATA_DIR, f"EEG_{eeg_pid}.csv"),
        os.path.join(BASE_DIR, f"EEG_{eeg_pid}.csv"),
    ])
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(f"EEG file not found. Tried: {candidates}")


def load_session_eeg(
    eeg_csv: str,
    session_start_sod: float,
    session_end_sod: float,
    eog_band_low: float,
    eog_band_high: float,
) -> Tuple[np.ndarray, np.ndarray, Optional[float]]:
    eeg = read_csv_robust(
        eeg_csv,
        usecols=["TimeStamp", "RAW_AF7", "RAW_AF8"],
        parse_dates=["TimeStamp"],
    )
    eeg = eeg.dropna(subset=["TimeStamp"]).sort_values("TimeStamp").reset_index(drop=True)
    eeg["RAW_AF7"] = pd.to_numeric(eeg["RAW_AF7"], errors="coerce")
    eeg["RAW_AF8"] = pd.to_numeric(eeg["RAW_AF8"], errors="coerce")
    eeg = eeg.dropna(subset=["RAW_AF7", "RAW_AF8"]).copy()
    eeg["sec_of_day"] = (
        eeg["TimeStamp"].dt.hour * 3600
        + eeg["TimeStamp"].dt.minute * 60
        + eeg["TimeStamp"].dt.second
        + eeg["TimeStamp"].dt.microsecond / 1_000_000.0
    )
    if session_end_sod >= session_start_sod:
        in_session = (eeg["sec_of_day"] >= session_start_sod) & (eeg["sec_of_day"] <= session_end_sod)
    else:
        in_session = (eeg["sec_of_day"] >= session_start_sod) | (eeg["sec_of_day"] <= session_end_sod)
    sess = eeg[in_session].copy()
    if sess.empty:
        raise ValueError("No EEG samples in session window.")
    sess["elapsed_sec"] = np.where(
        sess["sec_of_day"] >= session_start_sod,
        sess["sec_of_day"] - session_start_sod,
        sess["sec_of_day"] + SECONDS_PER_DAY - session_start_sod,
    )
    fs_hz = estimate_sampling_hz(sess["TimeStamp"])
    if fs_hz is None or fs_hz <= 2.5:
        raise ValueError("Could not estimate EEG sampling rate.")
    eog = (sess["RAW_AF7"].to_numpy() - sess["RAW_AF8"].to_numpy()).astype(float)
    eog_f = bandpass_filter(eog, fs_hz, eog_band_low, eog_band_high)
    sec_bins, env = envelope_1hz(sess["elapsed_sec"].to_numpy(), eog_f)
    if len(sec_bins) == 0:
        raise ValueError(
            "Could not build 1 Hz EOG envelope (0 bins). "
            f"Session samples={len(sess)}, fs≈{fs_hz:.1f} Hz. "
            "Check EEG timestamps align with session_start/end from rem_episodes.csv."
        )
    return sec_bins, env, fs_hz


def analyze_episode(
    ep: pd.Series,
    sec_bins: np.ndarray,
    env: np.ndarray,
    session_start_sod: float,
    session_max_elapsed: float,
    window_mode: str,
    fixed_context_sec: float,
    min_rem_sec: float,
    mad_mult: float,
) -> Optional[Dict[str, Any]]:
    start_sod = parse_hms_to_seconds(str(ep.get("episode_start_boston", "")))
    end_sod = parse_hms_to_seconds(str(ep.get("episode_end_boston", "")))
    if start_sod is None or end_sod is None:
        return None
    rem_start = sec_of_day_to_elapsed(start_sod, session_start_sod)
    rem_end = sec_of_day_to_elapsed(end_sod, session_start_sod)
    if rem_end <= rem_start:
        return None
    rem_dur = rem_end - rem_start
    if rem_dur < min_rem_sec:
        return None

    if window_mode == "fixed":
        win = float(fixed_context_sec)
        pre_start = max(0.0, rem_start - win)
        pre_end = rem_start
        post_start = rem_end
        post_end = min(session_max_elapsed, rem_end + win)
        pre_dur = pre_end - pre_start
        post_dur = post_end - post_start
    else:
        pre_start = max(0.0, rem_start - rem_dur)
        pre_end = rem_start
        post_start = rem_end
        post_end = min(session_max_elapsed, rem_end + rem_dur)
        pre_dur = pre_end - pre_start
        post_dur = post_end - post_start

    if pre_dur < min_rem_sec or post_dur < min_rem_sec:
        return None

    combined_mask = (
        ((sec_bins >= pre_start) & (sec_bins < post_end))
        & np.isfinite(env)
    )
    threshold = robust_threshold(env[combined_mask], mad_mult)

    pre_active, _, _ = active_seconds_from_envelope(sec_bins, env, pre_start, pre_end, threshold)
    intra_active, _, _ = active_seconds_from_envelope(sec_bins, env, rem_start, rem_end, threshold)
    post_active, _, _ = active_seconds_from_envelope(sec_bins, env, post_start, post_end, threshold)

    return {
        "episode_index": int(ep["episode_index"]),
        "rem_duration_sec": rem_dur,
        "pre_duration_sec": pre_dur,
        "post_duration_sec": post_dur,
        "pre_active_sec": pre_active,
        "intra_active_sec": intra_active,
        "post_active_sec": post_active,
        "pre_active_fraction": pre_active / pre_dur if pre_dur > 0 else np.nan,
        "intra_active_fraction": intra_active / rem_dur if rem_dur > 0 else np.nan,
        "post_active_fraction": post_active / post_dur if post_dur > 0 else np.nan,
        "threshold": threshold,
        "rem_start_elapsed": rem_start,
        "rem_end_elapsed": rem_end,
    }


def print_active_seconds_report(out_df: pd.DataFrame) -> None:
    """Print elevated EOG active seconds per period (before any plots)."""
    print("\n=== Elevated EOG-like activity (active seconds / period duration) ===")
    for row in out_df.itertuples():
        ep = int(row.episode_index)
        print(f"\nREM #{ep}:")
        for phase, label in [("pre", "Pre-REM"), ("intra", "Intra-REM"), ("post", "Post-REM")]:
            active = getattr(row, f"{phase}_active_sec")
            dur = getattr(row, f"{phase}_duration_sec")
            frac = getattr(row, f"{phase}_active_fraction")
            print(f"  {label:10s}: {active:6.0f} / {dur:6.0f} s  ({frac * 100:5.1f}%)")

    print("\n--- Session mean ---")
    for phase, label in [("pre", "Pre-REM"), ("intra", "Intra-REM"), ("post", "Post-REM")]:
        active_mean = out_df[f"{phase}_active_sec"].mean()
        dur_mean = out_df[f"{phase}_duration_sec"].mean()
        frac_mean = out_df[f"{phase}_active_fraction"].mean()
        print(
            f"  {label:10s}: {active_mean:6.1f} / {dur_mean:6.1f} s  ({frac_mean * 100:5.1f}%)"
        )
    print()


def plot_episode_bars(rows: pd.DataFrame, output_path: str, pid: str, night_number: Optional[int]) -> None:
    n = len(rows)
    if n == 0:
        return
    fig, axes = plt.subplots(1, 2, figsize=(max(10, 2.5 * n), 5))

    x = np.arange(n)
    width = 0.25
    phases = ["pre", "intra", "post"]
    colors = ["#bdbdbd", "#ffb74d", "#bdbdbd"]
    for j, phase in enumerate(phases):
        frac_col = f"{phase}_active_fraction"
        axes[0].bar(x + (j - 1) * width, rows[frac_col], width, label=phase, color=colors[j])
        sec_col = f"{phase}_active_sec"
        axes[1].bar(x + (j - 1) * width, rows[sec_col], width, label=phase, color=colors[j])

    labels = [f"REM {int(r.episode_index)}" for r in rows.itertuples()]
    for ax, ylab in [
        (axes[0], "Active fraction (sec with eye movement / period duration)"),
        (axes[1], "Active seconds"),
    ]:
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45 if n > 6 else 0, ha="right")
        ax.set_ylabel(ylab)
        ax.grid(True, axis="y", alpha=0.3)
        ax.legend()

    night_txt = f"night {night_number}" if night_number is not None else "night ?"
    fig.suptitle(
        f"Eye movement (EOG proxy AF7-AF8) | pid={pid} | {night_txt}\n"
        "pre / intra-REM / post windows",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def plot_session_summary(rows: pd.DataFrame, output_path: str, pid: str, night_number: Optional[int]) -> None:
    if rows.empty:
        return
    phases = ["pre", "intra", "post"]
    means = [rows[f"{p}_active_fraction"].mean() for p in phases]
    sems = [rows[f"{p}_active_fraction"].std(ddof=1) / math.sqrt(len(rows)) if len(rows) > 1 else 0.0 for p in phases]

    fig, ax = plt.subplots(figsize=(6, 5))
    x = np.arange(len(phases))
    bars = ax.bar(x, means, yerr=sems, capsize=6, color=["#bdbdbd", "#ffb74d", "#bdbdbd"], alpha=0.9)
    ax.set_xticks(x)
    ax.set_xticklabels(["Pre-REM", "Intra-REM", "Post-REM"])
    ax.set_ylabel("Mean active fraction ± SEM")
    ax.set_ylim(0, max(0.05, float(np.nanmax(means) + np.nanmax(sems) + 0.05)))
    ax.grid(True, axis="y", alpha=0.3)
    for bar, m in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{m:.2f}", ha="center", va="bottom", fontsize=9)

    night_txt = f"night {night_number}" if night_number is not None else "night ?"
    ax.set_title(f"Session mean | pid={pid} | {night_txt} | n={len(rows)} REM episodes")
    fig.tight_layout()
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Compare eye-movement activity (EOG proxy) pre / intra / post REM."
    )
    p.add_argument("--pid", required=True)
    p.add_argument("--night-number", type=int, default=None)
    p.add_argument("--eeg-csv", default=None)
    p.add_argument("--eeg-pid", default=None)
    p.add_argument("--rem-csv", default=DEFAULT_REM_CSV)
    p.add_argument("--session-start-boston", default=None, help="HH:MM:SS")
    p.add_argument("--rem-episodes", default=None, help="Comma-separated episode_index list")
    p.add_argument(
        "--window-mode",
        choices=["matched", "fixed"],
        default="matched",
        help="matched: pre/post same duration as REM; fixed: use --context-sec",
    )
    p.add_argument("--context-sec", type=float, default=DEFAULT_CONTEXT_SEC, help="Fixed pre/post window (fixed mode)")
    p.add_argument("--eog-band-low-hz", type=float, default=DEFAULT_EOG_BAND_LOW_HZ)
    p.add_argument("--eog-band-high-hz", type=float, default=DEFAULT_EOG_BAND_HIGH_HZ)
    p.add_argument("--threshold-mad-mult", type=float, default=DEFAULT_THRESHOLD_MAD_MULT)
    p.add_argument("--min-rem-sec", type=float, default=DEFAULT_MIN_REM_SEC)
    p.add_argument("--output-dir", default=OUTPUT_DIR)
    p.add_argument(
        "--no-plots",
        action="store_true",
        help="Print active-second stats and CSV only; skip PNG generation",
    )
    args = p.parse_args()

    rem_df = read_csv_robust(args.rem_csv)
    rem_df = choose_rem_session(rem_df, args.pid, args.session_start_boston, args.night_number)
    if rem_df.empty:
        raise ValueError(f"No REM rows for pid={args.pid}")
    rem_df["episode_index"] = pd.to_numeric(rem_df["episode_index"], errors="coerce")
    rem_df = rem_df.dropna(subset=["episode_index"]).copy()
    rem_df["episode_index"] = rem_df["episode_index"].astype(int)
    rem_df = rem_df.sort_values("episode_index").reset_index(drop=True)

    sel = parse_rem_episodes(args.rem_episodes)
    if sel is not None:
        rem_df = rem_df[rem_df["episode_index"].isin(sel)].copy()
        if rem_df.empty:
            raise ValueError(f"No matching REM episodes: {sel}")

    session_start_hms = str(rem_df["session_start_boston"].iloc[0])
    session_end_hms = str(rem_df["session_end_boston"].iloc[0])
    session_start_sod = parse_hms_to_seconds(session_start_hms)
    session_end_sod = parse_hms_to_seconds(session_end_hms)
    if session_start_sod is None or session_end_sod is None:
        raise ValueError("Invalid session times in rem_episodes.csv")

    eeg_csv = resolve_eeg_csv(args)
    print(f"Loading EEG: {eeg_csv}")
    sec_bins, env, fs_hz = load_session_eeg(
        eeg_csv,
        session_start_sod,
        session_end_sod,
        args.eog_band_low_hz,
        args.eog_band_high_hz,
    )
    session_max_elapsed = float(np.nanmax(sec_bins)) if len(sec_bins) else 0.0
    print(f"EEG fs≈{fs_hz:.1f} Hz | session envelope bins={len(sec_bins)}")

    results: List[Dict[str, Any]] = []
    for _, ep in rem_df.iterrows():
        row = analyze_episode(
            ep,
            sec_bins,
            env,
            session_start_sod,
            session_max_elapsed,
            args.window_mode,
            args.context_sec,
            args.min_rem_sec,
            args.threshold_mad_mult,
        )
        if row is not None:
            row["pid"] = args.pid
            row["night_number"] = args.night_number
            row["session_start_boston"] = session_start_hms
            results.append(row)
        else:
            print(f"Skipped REM #{int(ep['episode_index'])} (too short or missing alignment)")

    if not results:
        raise ValueError("No analyzable REM episodes.")

    out_df = pd.DataFrame(results)
    os.makedirs(args.output_dir, exist_ok=True)
    night_suffix = f"_night{args.night_number}" if args.night_number is not None else ""
    start_slug = safe_slug(session_start_hms)
    base = f"eye_movement_{args.pid}{night_suffix}_{start_slug}"

    csv_path = os.path.join(args.output_dir, f"{base}.csv")
    out_df.to_csv(csv_path, index=False)

    print_active_seconds_report(out_df)
    print(f"Saved stats: {csv_path}")

    if args.no_plots:
        return

    plot_episode_bars(
        out_df,
        os.path.join(args.output_dir, f"{base}_episodes.png"),
        args.pid,
        args.night_number,
    )
    plot_session_summary(
        out_df,
        os.path.join(args.output_dir, f"{base}_summary.png"),
        args.pid,
        args.night_number,
    )
    print(f"Saved plots to {args.output_dir}/")


if __name__ == "__main__":
    main()
