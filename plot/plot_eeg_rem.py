#!/usr/bin/env python3
# pyright: basic
"""
Plot EEG by REM phase.

Overview mode: full session with REM windows and disruptive cues.

Per-REM mode: pick the longest REM episode in the session, split it and
±context windows into fixed-length segments (default 30s), and plot one panel
per segment with EEG RAW_AF7/RAW_AF8 and disruptive cue markers.
"""

import argparse
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, cast

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
DEFAULT_DATA_DIR = os.path.join(PROJECT_ROOT, "data_prep", "output")
DEFAULT_REM_CSV = os.path.join(DEFAULT_DATA_DIR, "rem_episodes.csv")
DEFAULT_CUE_CSV = os.path.join(DEFAULT_DATA_DIR, "cue_events.csv")
EEG_DATA_DIR = os.path.join(BASE_DIR, "EEG")
EEG_PLOTS_DIR = os.path.join(BASE_DIR, "eeg_plots")
DEFAULT_OUTPUT_TEMPLATE = os.path.join(EEG_PLOTS_DIR, "eeg_rem_phases_{pid}.png")
SECONDS_PER_DAY = 24 * 3600
DEFAULT_LOWPASS_HZ = 20.0
PER_REM_SEGMENT_SEC = 30.0
DEFAULT_PER_REM_CONTEXT_SEC = 900.0
EEG_YLIM = (-10.0, 1800.0)


def read_csv_robust(path: str, **kwargs: Any) -> pd.DataFrame:
    """Read CSV with retries; macOS iCloud can raise OSError errno 89 if not local."""
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
        "On macOS this usually means the file is still in iCloud — in Finder, "
        "right-click the file or parent folder and choose Download Now, then retry."
    ) from last_err


def scalar_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def scalar_int(value: Any) -> Optional[int]:
    f = scalar_float(value)
    if f is None:
        return None
    return int(f)


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    try:
        missing = pd.isna(value)
        return bool(missing) if isinstance(missing, (bool, np.bool_)) else False
    except (TypeError, ValueError):
        return False


def as_int(value: Any) -> Optional[int]:
    return scalar_int(value)


def safe_slug(value: Optional[str]) -> str:
    s = "" if value is None else str(value)
    s = s.replace(":", "")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s or "unknown"


def list_rem_sessions(rem_df: pd.DataFrame, pid: Optional[str], night_number: Optional[int], session_start_boston: Optional[str]) -> List[Tuple[str, str, Optional[int]]]:
    sessions: Dict[Tuple[str, str, Optional[int]], int] = {}
    for _, row in rem_df.iterrows():
        row_pid = str(row.get("pid", "")).strip()
        row_start = str(row.get("session_start_boston", "")).strip()
        row_night = as_int(row.get("night_number", ""))
        if row_pid and row_start:
            key = (row_pid, row_start, row_night)
            sessions[key] = sessions.get(key, 0) + 1
    if not sessions:
        return []
    items = list(sessions.keys())
    if pid is not None:
        items = [k for k in items if k[0] == str(pid)]
    if night_number is not None:
        items = [k for k in items if k[2] == int(night_number)]
    if session_start_boston is not None:
        items = [k for k in items if k[1] == str(session_start_boston)]
    items.sort(key=lambda k: (k[0], k[2] if k[2] is not None else -1, k[1]))
    return items


def sec_of_day_to_elapsed(sec_of_day: float, ref_sec_of_day: float) -> float:
    """Elapsed seconds from reference time-of-day, handling midnight wrap."""
    if sec_of_day >= ref_sec_of_day:
        return sec_of_day - ref_sec_of_day
    return sec_of_day + SECONDS_PER_DAY - ref_sec_of_day


def in_wrapped_window(sec_of_day: float, start_sec: float, end_sec: float) -> bool:
    """Whether sec_of_day lies inside [start_sec, end_sec] with possible wrap."""
    if end_sec >= start_sec:
        return start_sec <= sec_of_day <= end_sec
    return sec_of_day >= start_sec or sec_of_day <= end_sec


def format_elapsed_hms(seconds: float) -> str:
    s = int(max(0, round(seconds)))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


def seconds_to_hms_wrapped(seconds: float) -> str:
    s = int(round(seconds)) % SECONDS_PER_DAY
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


def parse_hms_to_seconds(value: str) -> Optional[float]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.strptime(value[:8], "%H:%M:%S")
        return dt.hour * 3600 + dt.minute * 60 + dt.second
    except ValueError:
        return None


def estimate_sampling_hz(ts: pd.Series) -> Optional[float]:
    vals = pd.to_datetime(ts, errors="coerce").dropna()
    if len(vals) < 5:
        return None
    duration = (vals.iloc[-1] - vals.iloc[0]).total_seconds()
    fs_from_duration: Optional[float] = None
    if duration > 0:
        fs_from_duration = (len(vals) - 1) / duration

    diffs = vals.diff().dt.total_seconds().to_numpy()
    diffs = diffs[np.isfinite(diffs) & (diffs > 0) & (diffs < 1.0)]
    if len(diffs) >= 5:
        fs_from_median = 1.0 / float(np.median(diffs))
        # Muse CSVs often include many 1 ms timestamp steps; median diff can
        # over-estimate fs (~1000 Hz). Prefer span-based rate when that happens.
        if fs_from_median > 400 and fs_from_duration is not None and 50 < fs_from_duration < 500:
            return fs_from_duration
        if 50 < fs_from_median < 500:
            return fs_from_median

    if fs_from_duration is not None and 50 < fs_from_duration < 500:
        return fs_from_duration
    return None


def bandpass_notch_filter(series: pd.Series, fs_hz: float, low_hz: float, high_hz: float, notch_hz: float, notch_q: float) -> pd.Series:
    vals = pd.to_numeric(series, errors="coerce").astype(float).to_numpy()
    finite = np.isfinite(vals)
    if finite.sum() < 10:
        return pd.Series(vals, index=series.index)
    idx = np.arange(len(vals))
    interp = np.interp(idx, idx[finite], vals[finite])
    try:
        from scipy import signal
    except Exception:
        return pd.Series(vals, index=series.index)

    nyq = fs_hz * 0.5
    lo = max(0.001, float(low_hz))
    hi = min(float(high_hz), nyq * 0.95)
    if hi <= lo:
        return pd.Series(vals, index=series.index)
    if notch_hz > 0 and notch_hz < nyq * 0.95:
        b_notch, a_notch = signal.iirnotch(w0=float(notch_hz), Q=float(notch_q), fs=float(fs_hz))  # type: ignore[misc]
        interp = signal.filtfilt(b_notch, a_notch, interp, method="pad")
    b_bp, a_bp = signal.butter(4, [lo, hi], btype="bandpass", fs=float(fs_hz))  # type: ignore[misc]
    filt = signal.filtfilt(b_bp, a_bp, interp, method="pad")
    filt[~finite] = np.nan
    return pd.Series(filt, index=series.index)


def lowpass_filter(series: pd.Series, fs_hz: float, cutoff_hz: float) -> pd.Series:
    vals = pd.to_numeric(series, errors="coerce").astype(float).to_numpy()
    finite = np.isfinite(vals)
    if finite.sum() < 10:
        return pd.Series(vals, index=series.index)
    idx = np.arange(len(vals))
    interp = np.interp(idx, idx[finite], vals[finite])
    try:
        from scipy import signal
    except Exception:
        return pd.Series(vals, index=series.index)
    nyq = fs_hz * 0.5
    cut = min(float(cutoff_hz), nyq * 0.95)
    if cut <= 0:
        return pd.Series(vals, index=series.index)
    b_lp, a_lp = signal.butter(4, cut, btype="lowpass", fs=float(fs_hz))  # type: ignore[misc]
    filt = signal.filtfilt(b_lp, a_lp, interp, method="pad")
    filt[~finite] = np.nan
    return pd.Series(filt, index=series.index)


def rem_episode_duration_sec(ep: pd.Series) -> Optional[float]:
    dur = scalar_float(ep.get("episode_duration_sec"))
    if dur is not None and dur > 0:
        return dur
    start_sod = parse_hms_to_seconds(str(ep.get("episode_start_boston", "")))
    end_sod = parse_hms_to_seconds(str(ep.get("episode_end_boston", "")))
    if start_sod is None or end_sod is None:
        return None
    if end_sod >= start_sod:
        return end_sod - start_sod
    return end_sod + SECONDS_PER_DAY - start_sod


def pick_longest_rem_episode(rem_df: pd.DataFrame) -> pd.DataFrame:
    if rem_df.empty:
        return rem_df.iloc[0:0].copy()
    best_idx = None
    best_dur = -1.0
    for idx, ep in rem_df.iterrows():
        dur = rem_episode_duration_sec(ep)
        if dur is not None and dur > best_dur:
            best_dur = dur
            best_idx = idx
    if best_idx is None:
        return rem_df.iloc[0:0].copy()
    return rem_df.loc[[best_idx]].copy()


def parse_rem_episodes(value: Optional[str]) -> Optional[List[int]]:
    if value is None or not str(value).strip():
        return None
    out: List[int] = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    if not out:
        return None
    return out


def select_rem_episodes_by_index(rem_df: pd.DataFrame, episode_indices: List[int]) -> pd.DataFrame:
    available = set(rem_df["episode_index"].astype(int).tolist())
    missing = [i for i in episode_indices if i not in available]
    if missing:
        raise ValueError(
            f"REM episode(s) not found: {missing}. Available episode_index values: {sorted(available)}"
        )
    order = {idx: pos for pos, idx in enumerate(episode_indices)}
    sel = rem_df[rem_df["episode_index"].astype(int).isin(episode_indices)].copy()
    sel["_plot_order"] = sel["episode_index"].astype(int).map(order)
    return sel.sort_values("_plot_order").drop(columns=["_plot_order"]).reset_index(drop=True)


def per_rem_episode_output_path(
    args: argparse.Namespace,
    night_suffix: str,
    ep_idx: int,
    explicit_episodes: bool,
    default_path: Optional[str],
) -> str:
    if not explicit_episodes and default_path:
        return default_path
    root_base = args.output or os.path.join(
        EEG_PLOTS_DIR,
        f"eeg_rem_per_rem_{args.pid}{night_suffix}",
    )
    root, ext = os.path.splitext(root_base)
    if not ext:
        ext = ".png"
    if root.endswith("_per_rem"):
        root = root[: -len("_per_rem")]
    return f"{root}_rem{ep_idx}{ext}"


def build_time_segments(start_x: float, end_x: float, segment_sec: float) -> List[Tuple[float, float, int]]:
    if end_x <= start_x or segment_sec <= 0:
        return []
    segments: List[Tuple[float, float, int]] = []
    t = start_x
    seg_idx = 0
    while t < end_x - 1e-9:
        seg_end = min(t + segment_sec, end_x)
        segments.append((t, seg_end, seg_idx))
        t += segment_sec
        seg_idx += 1
    return segments


def build_rem_plot_segments(
    rem_start_x: float,
    rem_end_x: float,
    segment_sec: float,
    context_sec: float,
    session_max_x: float,
) -> List[Tuple[float, float, int, str]]:
    """Build pre-REM, REM, and post-REM segments. phase is pre|rem|post."""
    if rem_end_x <= rem_start_x or segment_sec <= 0:
        return []
    segments: List[Tuple[float, float, int, str]] = []
    pre_start = max(0.0, rem_start_x - max(0.0, context_sec))
    for win_start, win_end, seg_idx in build_time_segments(pre_start, rem_start_x, segment_sec):
        segments.append((win_start, win_end, seg_idx, "pre"))
    for win_start, win_end, seg_idx in build_time_segments(rem_start_x, rem_end_x, segment_sec):
        segments.append((win_start, win_end, seg_idx, "rem"))
    post_end = min(session_max_x, rem_end_x + max(0.0, context_sec))
    for win_start, win_end, seg_idx in build_time_segments(rem_end_x, post_end, segment_sec):
        segments.append((win_start, win_end, seg_idx, "post"))
    return segments


def segment_phase_style(phase: str) -> Tuple[str, str]:
    if phase == "rem":
        return "REM", "#fff3d6"
    if phase == "pre":
        return "pre-REM (not REM)", "#ececec"
    return "post-REM (not REM)", "#ececec"


def choose_rem_session(
    rem_df: pd.DataFrame,
    pid: str,
    session_start_boston: Optional[str],
    night_number: Optional[int],
) -> pd.DataFrame:
    d = rem_df[rem_df["pid"].astype(str) == pid].copy()
    has_night_col = "night_number" in d.columns
    if has_night_col:
        d["night_number"] = pd.to_numeric(d["night_number"], errors="coerce")
    if night_number is not None and has_night_col:
        d = d[d["night_number"] == int(night_number)].copy()
    if session_start_boston:
        d = d[d["session_start_boston"].astype(str) == session_start_boston].copy()
        return cast(pd.DataFrame, d)
    if d.empty:
        return cast(pd.DataFrame, d)
    if has_night_col:
        grp_n = d.groupby("night_number").size().sort_index()
        if len(grp_n) > 0:
            sel_night = int(grp_n.index.max())
            d = d[d["night_number"] == sel_night].copy()
    # default: choose session with most REM rows
    grp = d.groupby("session_start_boston").size().sort_values(ascending=False)
    sel = str(grp.index[0])
    return cast(pd.DataFrame, d[d["session_start_boston"].astype(str) == sel].copy())


def plot_rem_segment_figure(
    segments: List[Tuple[float, float, int, str]],
    ep: pd.Series,
    ep_idx: int,
    sess: pd.DataFrame,
    cues_sess: pd.DataFrame,
    session_start_sod: float,
    session_start_hms: str,
    cue_color: Dict[str, str],
    output_path: str,
    args: argparse.Namespace,
    af7_col: str,
    af8_col: str,
    af7_label: str,
    af8_label: str,
    ylabel: str,
    suptitle_prefix: str,
) -> int:
    n = len(segments)
    if n == 0:
        return 0
    ncols = 2 if n > 1 else 1
    nrows = int(math.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(16, max(4.0 * nrows, 5)))
    try:
        axes = axes.flatten()
    except Exception:
        axes = [axes]
    plotted_count = 0
    cues_ep = cues_sess[cues_sess["episode_index"] == ep_idx].copy()
    for i, (win_start_x, win_end_x, seg_idx, phase) in enumerate(segments):
        ax = axes[i]
        phase_label, facecolor = segment_phase_style(phase)
        seg = sess[(sess["elapsed_sec"] >= win_start_x) & (sess["elapsed_sec"] <= win_end_x)].copy()
        if seg.empty:
            ax.text(
                0.5,
                0.5,
                f"{phase_label} seg {seg_idx}: no EEG data",
                transform=ax.transAxes,
                ha="center",
                va="center",
            )
            ax.axis("off")
            continue
        plotted_count += 1
        ax.set_facecolor(facecolor)
        relx = seg["elapsed_sec"] - win_start_x
        ax.plot(relx, seg[af7_col], color="tab:blue", linewidth=0.9, alpha=0.9, label=af7_label)
        ax.plot(relx, seg[af8_col], color="tab:orange", linewidth=0.9, alpha=0.9, label=af8_label)
        if phase != "rem":
            ax.text(
                0.02,
                0.96,
                "NOT REM",
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=9,
                fontweight="bold",
                color="#555555",
                bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#999999", "alpha": 0.9},
            )

        first_disruptive = True
        for _, c in cues_ep.iterrows():
            cue_sod = c.get("event_sod")
            if is_missing(cue_sod):
                continue
            cue_abs = sec_of_day_to_elapsed(scalar_float(cue_sod) or 0.0, session_start_sod)
            if cue_abs < win_start_x or cue_abs > win_end_x:
                continue
            cue_x = cue_abs - win_start_x
            ct = str(c.get("cue_type", "cue"))
            if ct == "induction":
                continue
            col = cue_color.get(ct, "tab:purple")
            tr_idx = c.get("train_index")
            tr_suffix = ""
            if not is_missing(tr_idx):
                tr_suffix = f" (train {scalar_int(tr_idx)})"
            cue_label = None
            if ct == "disruptive" and first_disruptive:
                cue_label = "Disruptive cue" + tr_suffix
                first_disruptive = False
            ax.axvline(cue_x, color=col, linestyle=":", linewidth=0.9, alpha=0.85, label=cue_label)

        dur = max(1, int(round(win_end_x - win_start_x)))
        ticks = np.arange(0, dur + 1, 1)
        ax.set_xticks(ticks)
        ax.set_xticklabels([str(int(t)) for t in ticks], fontsize=7, rotation=0)
        seg_start_hms = seconds_to_hms_wrapped(session_start_sod + win_start_x)
        seg_end_hms = seconds_to_hms_wrapped(session_start_sod + win_end_x)
        ax.set_title(
            f"{phase_label} | seg {seg_idx} | {seg_start_hms} -> {seg_end_hms} ({int(round(dur))}s)",
            fontsize=9,
            fontweight="bold" if phase == "rem" else "normal",
        )
        ax.set_xlabel("Seconds within segment")
        ax.set_ylabel(ylabel)
        ax.set_ylim(EEG_YLIM)
        ax.grid(True, alpha=0.25)
        ax.legend(loc="upper right", fontsize=7)

    for j in range(n, len(axes)):
        axes[j].axis("off")
    rem_dur = rem_episode_duration_sec(ep)
    rem_dur_txt = f"{int(round(rem_dur))}s" if rem_dur is not None else "?"
    n_pre = sum(1 for _, _, _, phase in segments if phase == "pre")
    n_rem = sum(1 for _, _, _, phase in segments if phase == "rem")
    n_post = sum(1 for _, _, _, phase in segments if phase == "post")
    fig.suptitle(
        f"{suptitle_prefix} | pid={args.pid} | night={args.night_number} | "
        f"session_start={session_start_hms} | REM #{ep_idx} ({rem_dur_txt}) | "
        f"±{args.per_rem_context_sec:g}s context | segments={args.per_rem_segment_sec:g}s "
        f"(pre={n_pre}, rem={n_rem}, post={n_post}) | plotted={plotted_count}/{n}",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(output_path, dpi=120)
    print(f"Saved plot to {output_path}")
    plt.close(fig)
    return plotted_count


def main() -> None:
    p = argparse.ArgumentParser(description="Plot RAW_AF7/RAW_AF8 for each REM phase with disruptive cue markers.")
    p.add_argument("--pid", default=None, help="Participant ID (e.g. Sole)")
    p.add_argument("--night-number", type=int, default=None, help="Night number from export_data (1-based within pid)")
    p.add_argument("--eeg-csv", default=None, help="Path to EEG csv. If omitted, tries EEG_<eeg-pid><night>.csv then fallbacks")
    p.add_argument("--eeg-pid", default=None, help="Optional EEG file PID stem if it differs from --pid")
    p.add_argument("--rem-csv", default=DEFAULT_REM_CSV)
    p.add_argument("--cue-csv", default=DEFAULT_CUE_CSV)
    p.add_argument("--session-start-boston", default=None, help="Optional session_start_boston (HH:MM:SS)")
    p.add_argument("--both-phases-only", action="store_true", help="Keep only REM episodes with both disruptive and induction cues")
    p.add_argument("--plot-mode", choices=["overview", "per-rem", "both"], default="both", help="overview: full session; per-rem: one panel per REM episode; both: generate both plots")
    p.add_argument("--max-points", type=int, default=40000, help="Max plotted points in session overview")
    p.add_argument("--overview-smooth-sec", type=int, default=9, help="Rolling smoothing window (seconds) for overview only; 1 disables extra smoothing")
    p.add_argument("--bandpass-low-hz", type=float, default=0.5, help="Deprecated (kept for CLI compatibility)")
    p.add_argument("--bandpass-high-hz", type=float, default=40.0, help="Deprecated (kept for CLI compatibility)")
    p.add_argument("--notch-hz", type=float, default=60.0, help="Deprecated (kept for CLI compatibility)")
    p.add_argument("--notch-q", type=float, default=30.0, help="Deprecated (kept for CLI compatibility)")
    p.add_argument(
        "--lowpass-hz",
        "--per-rem-lowpass-hz",
        type=float,
        default=DEFAULT_LOWPASS_HZ,
        dest="lowpass_hz",
        help="Low-pass cutoff in Hz applied to RAW_AF7/RAW_AF8 before plotting (default: 20)",
    )
    p.add_argument("--per-rem-pre-sec", type=int, default=180, help="Deprecated (kept for CLI compatibility)")
    p.add_argument("--per-rem-post-sec", type=int, default=180, help="Deprecated (kept for CLI compatibility)")
    p.add_argument(
        "--per-rem-segment-sec",
        type=float,
        default=PER_REM_SEGMENT_SEC,
        help="Split REM/context windows into segments of this length (seconds) for per-rem plots",
    )
    p.add_argument(
        "--per-rem-context-sec",
        type=float,
        default=DEFAULT_PER_REM_CONTEXT_SEC,
        help="Seconds of EEG before/after each REM to include in per-rem plots (default: 900 = 15 min)",
    )
    p.add_argument(
        "--rem-episodes",
        default=None,
        help="Comma-separated REM episode_index values to plot (e.g. 0,4,6). Default: longest REM only.",
    )
    p.add_argument("--output", default=None)
    p.add_argument("--all-sessions", action="store_true", help="Plot all matching sessions (one file per session).")
    args = p.parse_args()
    if args.lowpass_hz <= 0:
        raise ValueError("--lowpass-hz must be > 0")
    rem_episode_selection = parse_rem_episodes(args.rem_episodes)

    if args.all_sessions:
        rem_df_all = read_csv_robust(args.rem_csv)
        sessions = list_rem_sessions(rem_df_all, args.pid, args.night_number, args.session_start_boston)
        if not sessions:
            raise ValueError("No sessions match the provided filters.")
        print(f"Found {len(sessions)} matching sessions.")
        success_count = 0
        failed: List[Tuple[str, str, Optional[int], str]] = []
        script_path = os.path.abspath(__file__)
        for sel_pid, sel_start, sel_night in sessions:
            pid_dir = os.path.join(EEG_PLOTS_DIR, safe_slug(sel_pid))
            os.makedirs(pid_dir, exist_ok=True)
            night_suffix = f"_night{sel_night}" if sel_night is not None else ""
            start_slug = safe_slug(sel_start)
            base_out = os.path.join(pid_dir, f"eeg_rem_{sel_pid}{night_suffix}_{start_slug}.png")
            cmd = [
                sys.executable,
                script_path,
                "--pid",
                str(sel_pid),
                "--session-start-boston",
                str(sel_start),
                "--plot-mode",
                str(args.plot_mode),
                "--max-points",
                str(args.max_points),
                "--overview-smooth-sec",
                str(args.overview_smooth_sec),
                "--bandpass-low-hz",
                str(args.bandpass_low_hz),
                "--bandpass-high-hz",
                str(args.bandpass_high_hz),
                "--notch-hz",
                str(args.notch_hz),
                "--notch-q",
                str(args.notch_q),
                "--lowpass-hz",
                str(args.lowpass_hz),
                "--per-rem-pre-sec",
                str(args.per_rem_pre_sec),
                "--per-rem-post-sec",
                str(args.per_rem_post_sec),
                "--per-rem-segment-sec",
                str(args.per_rem_segment_sec),
                "--per-rem-context-sec",
                str(args.per_rem_context_sec),
                "--rem-csv",
                str(args.rem_csv),
                "--cue-csv",
                str(args.cue_csv),
                "--output",
                str(base_out),
            ]
            if sel_night is not None:
                cmd.extend(["--night-number", str(sel_night)])
            if args.both_phases_only:
                cmd.append("--both-phases-only")
            if args.eeg_csv:
                cmd.extend(["--eeg-csv", str(args.eeg_csv)])
            if args.eeg_pid:
                cmd.extend(["--eeg-pid", str(args.eeg_pid)])
            if rem_episode_selection is not None:
                cmd.extend(["--rem-episodes", ",".join(str(x) for x in rem_episode_selection)])
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if proc.returncode == 0:
                    success_count += 1
                    print(proc.stdout.strip())
                else:
                    err_msg = proc.stderr.strip() or proc.stdout.strip() or f"return code {proc.returncode}"
                    failed.append((sel_pid, sel_start, sel_night, err_msg))
                    print(
                        f"Skipping session due to error: pid={sel_pid}, night_number={sel_night}, "
                        f"session_start_boston={sel_start} | {err_msg}"
                    )
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                failed.append((sel_pid, sel_start, sel_night, err_msg))
                print(
                    f"Skipping session due to error: pid={sel_pid}, night_number={sel_night}, "
                    f"session_start_boston={sel_start} | {err_msg}"
                )
        print(f"Completed all-sessions run: {success_count} succeeded, {len(failed)} failed.")
        if failed:
            print("Failed sessions:")
            for pid, start, night, err in failed:
                print(f"- pid={pid}, night_number={night}, session_start_boston={start} | {err}")
        return

    if not args.pid:
        raise ValueError("--pid is required unless --all-sessions is used.")
    night_suffix = f"_night{args.night_number}" if args.night_number is not None else ""
    output_png = args.output or DEFAULT_OUTPUT_TEMPLATE.format(pid=f"{args.pid}{night_suffix}")
    if args.plot_mode == "both":
        if args.output:
            root, ext = os.path.splitext(args.output)
            if not ext:
                ext = ".png"
            overview_output_png = f"{root}_overview{ext}"
            per_rem_output_png = f"{root}_per_rem{ext}"
        else:
            overview_output_png = os.path.join(EEG_PLOTS_DIR, f"eeg_rem_overview_{args.pid}{night_suffix}.png")
            per_rem_output_png = os.path.join(EEG_PLOTS_DIR, f"eeg_rem_per_rem_{args.pid}{night_suffix}.png")
    elif args.plot_mode == "overview":
        overview_output_png = output_png
        per_rem_output_png = None
    else:
        overview_output_png = None
        per_rem_output_png = output_png
    output_targets = [x for x in [overview_output_png, per_rem_output_png] if x]
    for target in output_targets:
        out_dir = os.path.dirname(target)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

    if args.eeg_csv:
        eeg_csv = args.eeg_csv
    else:
        eeg_pid = args.eeg_pid or args.pid
        candidates = []
        if args.night_number is not None:
            candidates.extend([
                os.path.join(EEG_DATA_DIR, f"EEG_{eeg_pid}{args.night_number}.csv"),
                os.path.join(BASE_DIR, f"EEG_{eeg_pid}{args.night_number}.csv"),
            ])
        candidates.extend([
            os.path.join(EEG_DATA_DIR, f"EEG_{eeg_pid}.csv"),
            os.path.join(BASE_DIR, f"EEG_{eeg_pid}.csv"),
        ])
        eeg_csv = None
        for c in candidates:
            if os.path.exists(c):
                eeg_csv = c
                break
        if eeg_csv is None:
            raise FileNotFoundError(f"EEG file not found. Tried: {candidates}")

    rem_df = read_csv_robust(args.rem_csv)
    cue_df = read_csv_robust(args.cue_csv)

    rem_df = choose_rem_session(rem_df, args.pid, args.session_start_boston, args.night_number)
    rem_df = cast(pd.DataFrame, rem_df)
    rem_df["episode_index"] = pd.to_numeric(rem_df["episode_index"], errors="coerce")
    rem_df["episode_duration_sec"] = pd.to_numeric(rem_df["episode_duration_sec"], errors="coerce")
    rem_df = rem_df.dropna(subset=["episode_index"]).copy()
    rem_df["episode_index"] = rem_df["episode_index"].astype(int)
    rem_df = rem_df.sort_values("episode_index").reset_index(drop=True)

    cue_df = cue_df[cue_df["pid"].astype(str) == args.pid].copy()
    if args.night_number is not None and "night_number" in cue_df.columns:
        cue_df["night_number"] = pd.to_numeric(cue_df["night_number"], errors="coerce")
        cue_df = cue_df[cue_df["night_number"] == int(args.night_number)].copy()
    if "took_place" in cue_df.columns:
        cue_df = cue_df[cue_df["took_place"].astype(str).str.lower() == "true"].copy()
    cue_df["episode_index"] = pd.to_numeric(cue_df["episode_index"], errors="coerce")
    cue_df = cue_df.dropna(subset=["episode_index"]).copy()
    cue_df["episode_index"] = cue_df["episode_index"].astype(int)
    cue_df["train_index"] = pd.to_numeric(cue_df.get("train_index"), errors="coerce")
    cue_df["epoch_sec"] = pd.to_numeric(cue_df.get("epoch_sec"), errors="coerce")
    if "event_time_boston" in cue_df.columns:
        cue_df["event_time_boston"] = cue_df["event_time_boston"].astype(str).str[:8]
    else:
        cue_df["event_time_boston"] = ""
    cue_df["event_sod"] = cue_df["event_time_boston"].apply(parse_hms_to_seconds)
    cue_df["cue_type"] = cue_df["cue_type"].astype(str).str.lower()

    if args.both_phases_only:
        keep = []
        for ep_idx, g in cue_df.groupby("episode_index"):
            types = set(g["cue_type"].tolist())
            if "disruptive" in types and "induction" in types:
                keep.append(ep_idx)
        rem_df = rem_df[rem_df["episode_index"].isin(keep)].copy()

    cue_df_plot = cue_df[cue_df["cue_type"] != "induction"].copy()

    # Load EEG with configurable low-pass filtering (no smoothing, no aggregation)
    eeg = read_csv_robust(
        eeg_csv,
        usecols=["TimeStamp", "RAW_AF7", "RAW_AF8"],
        parse_dates=["TimeStamp"],
    )
    eeg = eeg.dropna(subset=["TimeStamp"]).sort_values("TimeStamp").reset_index(drop=True)
    eeg["RAW_AF7"] = pd.to_numeric(eeg["RAW_AF7"], errors="coerce")
    eeg["RAW_AF8"] = pd.to_numeric(eeg["RAW_AF8"], errors="coerce")
    eeg = eeg.dropna(subset=["RAW_AF7", "RAW_AF8"]).copy()
    fs_hz = estimate_sampling_hz(eeg["TimeStamp"])
    if fs_hz is not None and fs_hz > 2.5:
        eeg["RAW_AF7"] = lowpass_filter(eeg["RAW_AF7"], fs_hz, args.lowpass_hz)
        eeg["RAW_AF8"] = lowpass_filter(eeg["RAW_AF8"], fs_hz, args.lowpass_hz)
        print(f"Applied low-pass filter: {args.lowpass_hz:g} Hz")
    eeg["sec_of_day"] = (
        eeg["TimeStamp"].dt.hour * 3600
        + eeg["TimeStamp"].dt.minute * 60
        + eeg["TimeStamp"].dt.second
        + eeg["TimeStamp"].dt.microsecond / 1_000_000.0
    )
    eeg["date_only"] = eeg["TimeStamp"].dt.date

    no_rem_mode = rem_df.empty
    if no_rem_mode:
        # Fallback mode: still plot EEG even when REM rows are absent.
        # Use full EEG file interval as session window.
        session_start_hms = eeg["TimeStamp"].iloc[0].strftime("%H:%M:%S")
        session_end_hms = eeg["TimeStamp"].iloc[-1].strftime("%H:%M:%S")
        session_start_sod = parse_hms_to_seconds(session_start_hms)
        session_end_sod = parse_hms_to_seconds(session_end_hms)
        if session_start_sod is None or session_end_sod is None:
            raise ValueError("Invalid EEG timestamp bounds.")
        sess = eeg.copy()
        elapsed = (sess["TimeStamp"] - sess["TimeStamp"].iloc[0]).dt.total_seconds()
        sess["elapsed_sec"] = elapsed.astype(float)
    else:
        session_start_hms = str(rem_df["session_start_boston"].iloc[0])
        session_end_hms = str(rem_df["session_end_boston"].iloc[0])
        session_start_sod = parse_hms_to_seconds(session_start_hms)
        session_end_sod = parse_hms_to_seconds(session_end_hms)
        if session_start_sod is None or session_end_sod is None:
            raise ValueError("Invalid session start/end times in rem_episodes.csv")

        # Fast vectorized session mask (handles midnight wrap).
        if session_end_sod >= session_start_sod:
            in_session = (eeg["sec_of_day"] >= session_start_sod) & (eeg["sec_of_day"] <= session_end_sod)
        else:
            in_session = (eeg["sec_of_day"] >= session_start_sod) | (eeg["sec_of_day"] <= session_end_sod)
        sess = eeg[in_session].copy()
        if sess.empty:
            raise ValueError("No EEG samples found in selected session interval.")

        # Elapsed seconds since session start (raw sample-level).
        sess["elapsed_sec"] = np.where(
            sess["sec_of_day"] >= session_start_sod,
            sess["sec_of_day"] - session_start_sod,
            sess["sec_of_day"] + SECONDS_PER_DAY - session_start_sod,
        )
    sess_full = sess.copy()
    sess_overview = sess_full
    if args.max_points > 0 and len(sess_overview) > args.max_points:
        stride = int(math.ceil(len(sess_overview) / float(args.max_points)))
        sess_overview = sess_overview.iloc[::stride].copy()
        print(f"Overview downsampling: kept 1/{stride} samples ({len(sess_overview)} points)")

    cue_color = {"disruptive": "red"}
    rem_episode_ids = set(rem_df["episode_index"].astype(int).tolist())
    if no_rem_mode:
        cues_sess = cue_df_plot.copy()
    else:
        cues_sess = cue_df_plot[cue_df_plot["episode_index"].astype(int).isin(rem_episode_ids)].copy()

    if args.plot_mode in ("overview", "both"):
        lp_txt = f"{args.lowpass_hz:g}Hz"
        fig, ax = plt.subplots(figsize=(18, 7))
        ax.plot(sess_overview["elapsed_sec"], sess_overview["RAW_AF7"], color="tab:blue", linewidth=0.8, alpha=0.9, label=f"AF7 (LP {lp_txt})")
        ax.plot(sess_overview["elapsed_sec"], sess_overview["RAW_AF8"], color="tab:orange", linewidth=0.8, alpha=0.9, label=f"AF8 (LP {lp_txt})")

        # REM windows over full session timeline.
        first_rem_label = True
        first_rem_start_label = True
        first_rem_end_label = True
        for _, ep in rem_df.iterrows():
            start_sod = parse_hms_to_seconds(str(ep.get("episode_start_boston", "")))
            end_sod = parse_hms_to_seconds(str(ep.get("episode_end_boston", "")))
            if start_sod is None or end_sod is None:
                continue
            rem_start_x = sec_of_day_to_elapsed(start_sod, session_start_sod)
            rem_end_x = sec_of_day_to_elapsed(end_sod, session_start_sod)
            ax.axvspan(
                rem_start_x,
                rem_end_x,
                alpha=0.12,
                color="tab:orange",
                label="REM window" if first_rem_label else None,
            )
            first_rem_label = False
            ax.axvline(rem_start_x, color="yellow", linestyle="--", linewidth=0.9, alpha=0.9, label="REM start" if first_rem_start_label else None)
            ax.axvline(rem_end_x, color="black", linestyle="--", linewidth=0.9, alpha=0.9, label="REM end" if first_rem_end_label else None)
            first_rem_start_label = False
            first_rem_end_label = False

        # Disruptive cue markers only (induction cues omitted).
        for _, c in cues_sess.iterrows():
            cue_sod = c.get("event_sod")
            if is_missing(cue_sod):
                continue
            cue_x = sec_of_day_to_elapsed(scalar_float(cue_sod) or 0.0, session_start_sod)
            ct = str(c.get("cue_type", "cue"))
            if ct == "induction":
                continue
            ax.axvline(cue_x, color=cue_color.get(ct, "tab:purple"), linestyle=":", linewidth=0.8, alpha=0.7)

        # Build concise HH:MM x ticks from elapsed seconds.
        total_sec = int(sess_overview["elapsed_sec"].max()) if not sess_overview.empty else 0
        tick_step = 30 * 60  # every 30 minutes
        ticks = np.arange(0, max(total_sec + tick_step, tick_step), tick_step)
        ax.set_xticks(ticks)
        ax.set_xticklabels([format_elapsed_hms(t)[:5] for t in ticks], rotation=0)

        ax.set_title(
            f"EEG session overview with REM windows (LP {lp_txt}) | pid={args.pid} | night={args.night_number} | start={session_start_hms}"
        )
        ax.set_xlabel("Elapsed from session start (HH:MM)")
        ax.set_ylabel("EEG raw amplitude")
        ax.set_ylim(EEG_YLIM)
        ax.grid(True, alpha=0.25)
        ax.legend(loc="upper right", fontsize=8)
        fig.tight_layout()
        fig.savefig(overview_output_png, dpi=120)
        print(f"Saved plot to {overview_output_png}")
        print(f"REM rows considered: {len(rem_df)}")
        if no_rem_mode:
            print("No REM rows found: plotted full EEG session without REM overlays.")
        print(f"Session points plotted (overview): {len(sess_overview)}")
        plt.close(fig)

    if args.plot_mode in ("per-rem", "both"):
        explicit_episodes = rem_episode_selection is not None
        if explicit_episodes:
            episodes_to_plot = select_rem_episodes_by_index(rem_df, rem_episode_selection)
        else:
            episodes_to_plot = pick_longest_rem_episode(rem_df)
        if episodes_to_plot.empty:
            print(f"No REM rows to plot for per-rem mode (pid={args.pid}, night={args.night_number}).")
        else:
            session_max_x = float(sess_full["elapsed_sec"].max()) if not sess_full.empty else 0.0
            total_plotted = 0
            for _, ep in episodes_to_plot.iterrows():
                ep_idx = scalar_int(ep.get("episode_index"))
                if ep_idx is None:
                    continue
                start_sod = parse_hms_to_seconds(str(ep.get("episode_start_boston", "")))
                end_sod = parse_hms_to_seconds(str(ep.get("episode_end_boston", "")))
                if start_sod is None or end_sod is None:
                    print(f"REM #{ep_idx} has invalid times; skipping.")
                    continue
                rem_start_x = sec_of_day_to_elapsed(start_sod, session_start_sod)
                rem_end_x = sec_of_day_to_elapsed(end_sod, session_start_sod)
                segments = build_rem_plot_segments(
                    rem_start_x,
                    rem_end_x,
                    args.per_rem_segment_sec,
                    args.per_rem_context_sec,
                    session_max_x,
                )
                rem_dur = rem_episode_duration_sec(ep)
                rem_dur_txt = f"{int(round(rem_dur))}s" if rem_dur is not None else "?"
                if not segments:
                    print(
                        f"REM #{ep_idx} ({rem_dur_txt}) is too short to split into "
                        f"{args.per_rem_segment_sec}s segments."
                    )
                    continue
                n_pre = sum(1 for _, _, _, phase in segments if phase == "pre")
                n_rem = sum(1 for _, _, _, phase in segments if phase == "rem")
                n_post = sum(1 for _, _, _, phase in segments if phase == "post")
                print(
                    f"Per-rem mode: REM #{ep_idx} ({rem_dur_txt}), "
                    f"±{args.per_rem_context_sec:g}s context, "
                    f"{len(segments)} segments x {args.per_rem_segment_sec:g}s "
                    f"(pre={n_pre}, rem={n_rem}, post={n_post})"
                )
                out_path = per_rem_episode_output_path(
                    args,
                    night_suffix,
                    ep_idx,
                    explicit_episodes,
                    per_rem_output_png,
                )
                out_dir = os.path.dirname(out_path)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                plotted_count = plot_rem_segment_figure(
                    segments,
                    ep,
                    ep_idx,
                    sess_full,
                    cues_sess,
                    session_start_sod,
                    session_start_hms,
                    cue_color,
                    out_path,
                    args,
                    "RAW_AF7",
                    "RAW_AF8",
                    f"AF7 (LP {args.lowpass_hz:g}Hz)",
                    f"AF8 (LP {args.lowpass_hz:g}Hz)",
                    "EEG raw amplitude",
                    f"EEG REM segments (LP {args.lowpass_hz:g}Hz)",
                )
                total_plotted += plotted_count
                print(f"Plotted segments for REM #{ep_idx}: {plotted_count}/{len(segments)}")
            print(f"REM rows in session: {len(rem_df)}")
            print(f"REM episodes plotted: {len(episodes_to_plot)}")
            print(f"Total segment panels plotted: {total_plotted}")


if __name__ == "__main__":
    main()
