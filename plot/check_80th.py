#!/usr/bin/env python3
"""
Quick REM check plot:
- read raw motion_per_second
- rebuild 5-minute smoothed motion from raw values
- compute evolving 80th-percentile cutoff from smoothed history
- plot smoothed + cutoffs + REM overlays
"""

import argparse
import csv
import math
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_NIGHT_DIR = os.path.join(BASE_DIR, "data_night")
DEFAULT_MOTION_CSV = os.path.join(DATA_NIGHT_DIR, "motion_per_second_series.csv")
DEFAULT_REM_CSV = os.path.join(DATA_NIGHT_DIR, "rem_episodes.csv")
DEFAULT_OUT_DIR = os.path.join(BASE_DIR, "80th_plots")


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def as_int(value: str) -> Optional[int]:
    try:
        return int(float(value))
    except Exception:
        return None


def as_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def parse_hms(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M:%S")
    except Exception:
        return None


def hms_to_seconds(value: str) -> Optional[int]:
    dt = parse_hms(value)
    if dt is None:
        return None
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def rolling_mean(values: List[float], window_sec: int) -> List[float]:
    w = max(1, int(window_sec))
    out = [np.nan] * len(values)
    run_sum = 0.0
    for i, v in enumerate(values):
        run_sum += float(v)
        if i >= w:
            run_sum -= float(values[i - w])
        if i >= w - 1:
            out[i] = run_sum / w
    return out


def choose_session(
    motion_rows: List[Dict[str, str]],
    pid: Optional[str],
    night_number: Optional[int],
    session_start_boston: Optional[str],
) -> Tuple[str, str, Optional[int]]:
    sessions: Dict[Tuple[str, str, Optional[int]], int] = {}
    for row in motion_rows:
        row_pid = (row.get("pid") or "").strip()
        row_start = (row.get("session_start_boston") or "").strip()
        row_night = as_int(row.get("night_number", ""))
        if row_pid and row_start:
            key = (row_pid, row_start, row_night)
            sessions[key] = sessions.get(key, 0) + 1

    if not sessions:
        raise ValueError("No sessions found in motion CSV.")

    if pid and session_start_boston:
        key = (pid, session_start_boston, night_number)
        if key not in sessions:
            raise ValueError(
                f"Session not found for pid={pid}, session_start_boston={session_start_boston}, night_number={night_number}"
            )
        return key

    if pid and night_number is not None:
        matches = [(k, n) for k, n in sessions.items() if k[0] == pid and k[2] == night_number]
        if not matches:
            raise ValueError(f"No sessions found for pid={pid}, night_number={night_number}")
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[0][0]

    if pid:
        matches = [(k, n) for k, n in sessions.items() if k[0] == pid]
        if not matches:
            raise ValueError(f"No sessions found for pid={pid}")
        matches.sort(key=lambda x: (x[0][2] if x[0][2] is not None else -1, x[1]), reverse=True)
        return matches[0][0]

    all_sessions = sorted(sessions.items(), key=lambda x: x[1], reverse=True)
    return all_sessions[0][0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot evolving smoothed-motion 80th-percentile cutoff + REM overlay.")
    parser.add_argument("--motion-csv", default=DEFAULT_MOTION_CSV)
    parser.add_argument("--rem-csv", default=DEFAULT_REM_CSV)
    parser.add_argument("--pid", default=None, help="Participant ID")
    parser.add_argument("--night-number", type=int, default=None)
    parser.add_argument("--session-start-boston", default=None, help="HH:MM:SS")
    parser.add_argument("--percentile", type=float, default=0.80, help="Cutoff percentile in [0,1]")
    parser.add_argument("--smooth-window-sec", type=int, default=300, help="Rolling window for smoothed motion (seconds).")
    parser.add_argument("--per-rem-pre-sec", type=int, default=180, help="Seconds shown before REM start in per-REM plot.")
    parser.add_argument("--per-rem-post-sec", type=int, default=180, help="Seconds shown after REM end in per-REM plot.")
    parser.add_argument("--output", default=None, help="Output PNG path")
    args = parser.parse_args()

    pct = float(args.percentile)
    if pct <= 0 or pct >= 1:
        raise ValueError("--percentile must be between 0 and 1 (e.g. 0.80).")

    motion_rows = read_csv(args.motion_csv)
    rem_rows = read_csv(args.rem_csv)

    sel_pid, sel_start, sel_night = choose_session(
        motion_rows, args.pid, args.night_number, args.session_start_boston
    )

    sec_to_motion: Dict[int, float] = {}
    for row in motion_rows:
        if row.get("pid") != sel_pid or row.get("session_start_boston") != sel_start:
            continue
        row_night = as_int(row.get("night_number", ""))
        if sel_night is not None and row_night != sel_night:
            continue
        sec = as_int(row.get("second_index", ""))
        val = as_float(row.get("motion_per_second", ""))
        if sec is None or val is None:
            continue
        sec_to_motion[sec] = val

    if not sec_to_motion:
        raise ValueError("No motion rows found for selected session.")

    session_start_sod = hms_to_seconds(sel_start)
    if session_start_sod is None:
        raise ValueError(f"Invalid session_start_boston: {sel_start}")
    session_anchor = datetime(2000, 1, 1) + timedelta(seconds=session_start_sod)

    sorted_secs = sorted(sec_to_motion.keys())
    raw_series = [sec_to_motion[s] for s in sorted_secs]
    smoothed_series = rolling_mean(raw_series, args.smooth_window_sec)
    xs, smoothed_vals, p80_vals, q30_vals, q40_vals, app_equiv_vals = [], [], [], [], [], []
    p80_by_sec: Dict[int, float] = {}
    q30_by_sec: Dict[int, float] = {}
    q40_by_sec: Dict[int, float] = {}
    smoothed_by_sec: Dict[int, float] = {}
    app_equiv_by_sec: Dict[int, float] = {}
    smoothed_history: List[float] = []
    for idx, sec in enumerate(sorted_secs):
        if idx >= len(smoothed_series):
            continue
        sm = smoothed_series[idx]
        if not np.isfinite(sm):
            continue
        smoothed_history.append(float(sm))
        xs.append(session_anchor + timedelta(seconds=sec))
        smoothed_vals.append(float(sm))
        p80_now = float(np.quantile(smoothed_history, pct))
        q30_now = float(np.quantile(smoothed_history, 0.30))
        q40_now = float(np.quantile(smoothed_history, 0.40))
        # App stillness check (belowCount >= 0.8) is equivalent to current <= q(1-pct), i.e. q20 when pct=0.8.
        app_now = float(np.quantile(smoothed_history, 1.0 - pct))
        p80_vals.append(p80_now)
        q30_vals.append(q30_now)
        q40_vals.append(q40_now)
        app_equiv_vals.append(app_now)
        p80_by_sec[sec] = p80_now
        q30_by_sec[sec] = q30_now
        q40_by_sec[sec] = q40_now
        smoothed_by_sec[sec] = float(sm)
        app_equiv_by_sec[sec] = app_now

    rem_eps = []
    for row in rem_rows:
        if row.get("pid") != sel_pid or row.get("session_start_boston") != sel_start:
            continue
        row_night = as_int(row.get("night_number", ""))
        if sel_night is not None and row_night != sel_night:
            continue
        ep_idx = as_int(row.get("episode_index", ""))
        start_sec = as_int(row.get("episode_start_epoch_sec", ""))
        dur = as_int(row.get("episode_duration_sec", ""))
        if ep_idx is None or start_sec is None or dur is None:
            continue
        end_sec = start_sec + max(0, dur)
        rem_eps.append((ep_idx, start_sec, end_sec))

    rem_eps.sort(key=lambda x: x[0])

    os.makedirs(DEFAULT_OUT_DIR, exist_ok=True)
    out = args.output
    if not out:
        suffix = f"_night{sel_night}" if sel_night is not None else ""
        out = os.path.join(DEFAULT_OUT_DIR, f"p80_overview_{sel_pid}{suffix}.png")

    fig, ax = plt.subplots(figsize=(15, 6))
    ax.plot(xs, smoothed_vals, color="tab:blue", linewidth=1.1, alpha=0.9, label="smoothed motion (5min)")
    ax.plot(xs, p80_vals, color="tab:red", linewidth=1.2, alpha=0.95, label=f"evolving p{int(pct*100)} cutoff")
    ax.plot(xs, q30_vals, color="tab:purple", linewidth=1.0, alpha=0.9, label="evolving q30 cutoff")
    ax.plot(xs, q40_vals, color="tab:brown", linewidth=1.0, alpha=0.9, label="evolving q40 cutoff")
    q_app_pct = int(round((1.0 - pct) * 100))
    ax.plot(
        xs,
        app_equiv_vals,
        color="tab:green",
        linewidth=1.1,
        alpha=0.9,
        linestyle="--",
        label=f"app-equivalent cutoff q{q_app_pct}",
    )

    first = True
    for _, st_sec, en_sec in rem_eps:
        st = session_anchor + timedelta(seconds=st_sec)
        en = session_anchor + timedelta(seconds=en_sec)
        ax.axvspan(st, en, alpha=0.12, color="tab:orange", label="REM window" if first else None)
        ax.axvline(st, color="yellow", linestyle="--", linewidth=0.9, alpha=0.9, label="REM start" if first else None)
        ax.axvline(en, color="black", linestyle="--", linewidth=0.9, alpha=0.9, label="REM end" if first else None)
        first = False

    ax.set_title(
        f"Evolving p{int(pct*100)} cutoff from smoothed motion | pid={sel_pid} | night={sel_night} | "
        f"session_start={sel_start} | smooth_window={int(args.smooth_window_sec)}s"
    )
    ax.set_xlabel("Time (Boston)")
    ax.set_ylabel("Motion / cutoff")
    ax.grid(True, alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    ax.tick_params(axis="x", labelrotation=30, labelsize=8)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    print(f"Saved plot to {out}")

    # Per-REM p80 plot (-pre/+post around each REM episode).
    if rem_eps:
        suffix = f"_night{sel_night}" if sel_night is not None else ""
        per_rem_out = os.path.join(DEFAULT_OUT_DIR, f"p80_per_rem_{sel_pid}{suffix}.png")
        n = len(rem_eps)
        ncols = 2 if n > 1 else 1
        nrows = int(math.ceil(n / ncols))
        fig2, axes = plt.subplots(nrows, ncols, figsize=(16, max(4.0 * nrows, 5)))
        try:
            axes = axes.flatten()
        except Exception:
            axes = [axes]

        first_valid_sec = min(p80_by_sec.keys()) if p80_by_sec else 0
        for i, (ep_idx, st_sec, en_sec) in enumerate(rem_eps):
            ax = axes[i]
            win_start = max(first_valid_sec, st_sec - max(0, int(args.per_rem_pre_sec)))
            win_end = en_sec + max(0, int(args.per_rem_post_sec))
            secs = [
                s
                for s in range(win_start, win_end + 1)
                if s in p80_by_sec and s in q30_by_sec and s in q40_by_sec and s in smoothed_by_sec and s in app_equiv_by_sec
            ]
            if not secs:
                ax.text(0.5, 0.5, "No p80 values", transform=ax.transAxes, ha="center", va="center")
                ax.axis("off")
                continue
            xw = [session_anchor + timedelta(seconds=s) for s in secs]
            y_sm = [smoothed_by_sec[s] for s in secs]
            y_q30 = [q30_by_sec[s] for s in secs]
            y_q40 = [q40_by_sec[s] for s in secs]
            y_app = [app_equiv_by_sec[s] for s in secs]
            ax.plot(xw, y_sm, color="tab:blue", linewidth=1.1, alpha=0.9, label="smoothed motion")
            ax.plot(xw, y_q30, color="tab:purple", linewidth=1.0, alpha=0.9, label="q30 cutoff")
            ax.plot(xw, y_q40, color="tab:brown", linewidth=1.0, alpha=0.9, label="q40 cutoff")
            ax.plot(
                xw,
                y_app,
                color="tab:green",
                linewidth=1.1,
                alpha=0.9,
                linestyle="--",
                label=f"app q{q_app_pct}",
            )
            st = session_anchor + timedelta(seconds=st_sec)
            en = session_anchor + timedelta(seconds=en_sec)
            ax.axvspan(st, en, alpha=0.12, color="tab:orange", label="REM window")
            ax.axvline(st, color="yellow", linestyle="--", linewidth=0.9, alpha=0.9, label="REM start")
            ax.axvline(en, color="black", linestyle="--", linewidth=0.9, alpha=0.9, label="REM end")
            rem_dur_sec = max(0, int(en_sec - st_sec))
            ax.set_title(f"REM #{ep_idx} | duration={rem_dur_sec}s", fontsize=9)
            ax.set_xlabel("Time (Boston)")
            ax.set_ylabel("Motion / cutoff")
            ax.grid(True, alpha=0.25)
            ax.xaxis.set_major_locator(mdates.SecondLocator(interval=30))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
            ax.tick_params(axis="x", labelrotation=30, labelsize=8)
            ax.legend(loc="upper right", fontsize=7)

        for j in range(len(rem_eps), len(axes)):
            axes[j].axis("off")
        fig2.suptitle(
            f"Per-REM evolving p{int(pct*100)} cutoff | pid={sel_pid} | night={sel_night} | session_start={sel_start}",
            fontsize=11,
        )
        fig2.tight_layout(rect=[0, 0, 1, 0.96])
        fig2.savefig(per_rem_out, dpi=160)
        print(f"Saved plot to {per_rem_out}")

    print(f"Selected session: pid={sel_pid}, night_number={sel_night}, session_start_boston={sel_start}")
    print(f"REM episodes overlaid: {len(rem_eps)}")


if __name__ == "__main__":
    main()

