#!/usr/bin/env python3
"""
REM episode timeline plot (same style intent as rem_cutoff_plot):
- x-axis uses clock time (HH:MM:SS)
- one panel per REM episode
- highlights REM detection moment and its motion-per-second value
- marks each cue with volume, arousal flag, and +1s/+2s/+3s motion values
"""

import argparse
import csv
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import matplotlib.dates as mdates
import matplotlib.pyplot as plt


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_NIGHT_DIR = os.path.join(BASE_DIR, "data_night")
DEFAULT_CUTOFF_CSV = os.path.join(DATA_NIGHT_DIR, "motion_smoothed_series.csv")
DEFAULT_REM_CSV = os.path.join(DATA_NIGHT_DIR, "rem_episodes.csv")
DEFAULT_CUE_CSV = os.path.join(DATA_NIGHT_DIR, "cue_events.csv")
MOTION_PLOTS_DIR = os.path.join(BASE_DIR, "motion_plots")
FOUR_HOURS_SECONDS = 4 * 60 * 60
# For smoothed series indexed from first valid 5-minute sample,
# absolute_second = csv_second_index + 299.
DEFAULT_SMOOTH_INDEX_OFFSET_SEC = 299


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_hms(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M:%S")
    except ValueError:
        return None


def choose_session(
    cutoff_rows: List[Dict[str, str]],
    pid: Optional[str],
    session_start_boston: Optional[str],
    night_number: Optional[int],
) -> Tuple[str, str, Optional[int]]:
    sessions = {}
    for row in cutoff_rows:
        row_pid = row.get("pid", "")
        row_start = row.get("session_start_boston", "")
        row_night = as_int(row.get("night_number", ""))
        if row_pid and row_start:
            sessions[(row_pid, row_start, row_night)] = sessions.get((row_pid, row_start, row_night), 0) + 1

    if not sessions:
        raise ValueError("No sessions found in cutoff CSV.")

    if pid and session_start_boston:
        key = (pid, session_start_boston, night_number)
        if key not in sessions:
            raise ValueError(f"Session not found for pid={pid}, session_start_boston={session_start_boston}, night_number={night_number}")
        return key

    if pid and night_number is not None:
        pid_sessions = [(k, n) for k, n in sessions.items() if k[0] == pid and k[2] == night_number]
        if not pid_sessions:
            raise ValueError(f"No sessions found for pid={pid}, night_number={night_number}")
        pid_sessions.sort(key=lambda x: x[1], reverse=True)
        return pid_sessions[0][0]

    if pid:
        pid_sessions = [(k, n) for k, n in sessions.items() if k[0] == pid]
        if not pid_sessions:
            raise ValueError(f"No sessions found for pid={pid}")
        # Prefer latest night when available, then densest row count.
        pid_sessions.sort(key=lambda x: (x[0][2] if x[0][2] is not None else -1, x[1]), reverse=True)
        return pid_sessions[0][0]

    all_sessions = sorted(sessions.items(), key=lambda x: x[1], reverse=True)
    return all_sessions[0][0]


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot each REM episode with cue details on a time axis.")
    parser.add_argument("--cutoff-csv", default=DEFAULT_CUTOFF_CSV, help="Motion CSV (default: motion_smoothed_series.csv)")
    parser.add_argument("--rem-csv", default=DEFAULT_REM_CSV)
    parser.add_argument("--cue-csv", default=DEFAULT_CUE_CSV)
    parser.add_argument("--pid", default=None)
    parser.add_argument("--night-number", type=int, default=None, help="Night number from export_data (1-based within pid)")
    parser.add_argument(
        "--smooth-index-offset-sec",
        type=int,
        default=DEFAULT_SMOOTH_INDEX_OFFSET_SEC,
        help="Offset applied to second_index when plotting smoothed CSV (default: 299 for 5-minute-window indexed CSVs). Use 0 if second_index is already absolute.",
    )
    parser.add_argument("--session-start-boston", default=None, help="HH:MM:SS")
    parser.add_argument("--plot-mode", choices=["overview", "per-rem", "both"], default="both")
    parser.add_argument("--both-phases-only", action="store_true", help="Only keep REM episodes that have both disruptive and induction cues")
    parser.add_argument("--per-rem-pre-sec", type=int, default=180, help="Seconds shown before REM start in per-rem mode")
    parser.add_argument("--per-rem-post-sec", type=int, default=180, help="Seconds shown after REM end in per-rem mode")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    cutoff_rows = read_csv(args.cutoff_csv)
    rem_rows = read_csv(args.rem_csv)
    cue_rows = read_csv(args.cue_csv)

    sel_pid, sel_start, sel_night = choose_session(cutoff_rows, args.pid, args.session_start_boston, args.night_number)
    os.makedirs(MOTION_PLOTS_DIR, exist_ok=True)
    session_start_dt = parse_hms(sel_start)
    if session_start_dt is None:
        raise ValueError(f"Invalid selected session_start_boston: {sel_start}")

    # Build per-second data maps for selected session (prefer smoothed motion).
    sec_to_val: Dict[int, float] = {}
    sec_to_time: Dict[int, datetime] = {}
    has_smoothed_col = False
    for r in cutoff_rows:
        if "motion_smoothed" in r and str(r.get("motion_smoothed", "")).strip() != "":
            has_smoothed_col = True
            break
    sec_offset = int(args.smooth_index_offset_sec) if has_smoothed_col else 0
    for row in cutoff_rows:
        if row.get("pid") != sel_pid or row.get("session_start_boston") != sel_start:
            continue
        row_night = as_int(row.get("night_number", ""))
        if sel_night is not None and row_night != sel_night:
            continue
        sec = as_int(row.get("second_index", ""))
        raw_val = row.get("motion_smoothed")
        if raw_val is None:
            raw_val = row.get("motion_per_second")
        if raw_val is None:
            raw_val = row.get("motion_80pct_cutoff", "")
        val = as_float(raw_val)
        if sec is None or val is None:
            continue
        sec = sec + sec_offset
        sec_to_val[sec] = val
        # Build monotonic clock time from session start + elapsed seconds.
        # This preserves midnight rollover (night sessions spanning 00:00).
        sec_to_time[sec] = session_start_dt + timedelta(seconds=sec)

    if not sec_to_val:
        raise ValueError("No motion data found for selected session.")
    first_valid_sec = min(sec_to_val.keys())

    # Parse REM episodes for selected session.
    rem_eps = []
    for row in rem_rows:
        if row.get("pid") != sel_pid or row.get("session_start_boston") != sel_start:
            continue
        row_night = as_int(row.get("night_number", ""))
        if sel_night is not None and row_night != sel_night:
            continue
        ep_idx = as_int(row.get("episode_index", ""))
        start_sec = as_int(row.get("episode_start_epoch_sec", ""))
        dur_sec = as_int(row.get("episode_duration_sec", ""))
        if ep_idx is None or start_sec is None or dur_sec is None:
            continue
        rem_eps.append({
            "episode_index": ep_idx,
            "start_sec": start_sec,
            "dur_sec": max(0, dur_sec),
            "start_clock": row.get("episode_start_boston", ""),
            "end_clock": row.get("episode_end_boston", ""),
        })

    if not rem_eps:
        raise ValueError("No REM episodes found for selected session.")
    rem_eps.sort(key=lambda d: d["episode_index"])

    # Cue events by episode.
    cues_by_episode = defaultdict(list)
    for row in cue_rows:
        if row.get("pid") != sel_pid:
            continue
        row_night = as_int(row.get("night_number", ""))
        if sel_night is not None and row_night != sel_night:
            continue
        if str(row.get("took_place", "")).strip().lower() != "true":
            continue
        ep_idx = as_int(row.get("episode_index", ""))
        cue_sec = as_int(row.get("epoch_sec", ""))
        if ep_idx is None or cue_sec is None:
            continue
        cues_by_episode[ep_idx].append({
            "cue_type": (row.get("cue_type", "") or "").strip().lower(),
            "cue_sec": cue_sec,
            "clock": row.get("event_time_boston", ""),
            "volume": row.get("volume", "") if row.get("volume", "") != "" else "na",
            "arousal": row.get("arousal_detected", "") if row.get("arousal_detected", "") != "" else "na",
        })

    if args.both_phases_only:
        filtered_eps = []
        for ep in rem_eps:
            ep_idx = ep["episode_index"]
            cue_types = {c["cue_type"] for c in cues_by_episode.get(ep_idx, [])}
            if "disruptive" in cue_types and "induction" in cue_types:
                filtered_eps.append(ep)
        rem_eps = filtered_eps
        if not rem_eps:
            raise ValueError("No REM episodes with both disruption and induction phases in selected session.")

    cue_color = {"disruptive": "red", "induction": "green"}
    outputs = []

    if args.plot_mode in ("overview", "both"):
        overview_out = args.output
        if args.plot_mode == "both" or not overview_out:
            if sel_night is not None:
                overview_out = os.path.join(MOTION_PLOTS_DIR, f"motion_overview_{sel_pid}_night{sel_night}.png")
            else:
                overview_out = os.path.join(MOTION_PLOTS_DIR, f"motion_overview_{sel_pid}.png")
        overview_dir = os.path.dirname(overview_out)
        if overview_dir:
            os.makedirs(overview_dir, exist_ok=True)
        sorted_secs = sorted(sec_to_val.keys())
        # Full-night overview: from first valid motion sample (already offset-aligned)
        # to last available motion value.
        overview_start_sec = first_valid_sec
        x_all = [sec_to_time[s] for s in sorted_secs if s in sec_to_time and s >= overview_start_sec]
        y_all = [round(sec_to_val[s], 3) for s in sorted_secs if s in sec_to_time and s >= overview_start_sec]
        if not x_all:
            raise ValueError("No motion points from first valid sample onward for overview plot.")
        fig_o, ax_o = plt.subplots(figsize=(14, 6))
        ax_o.plot(x_all, y_all, linewidth=1.2, color="tab:blue", label="motion smoothed")
        first = True
        for ep in rem_eps:
            st = sec_to_time.get(ep["start_sec"])
            en = sec_to_time.get(ep["start_sec"] + ep["dur_sec"])
            if st is None or en is None:
                continue
            ax_o.axvspan(st, en, alpha=0.22, color="tab:orange", label="REM episode" if first else None)
            ax_o.axvline(st, linestyle="--", linewidth=1, color="yellow", alpha=0.95, label="REM start" if first else None)
            ax_o.axvline(en, linestyle="--", linewidth=1, color="black", alpha=0.95, label="REM end" if first else None)
            first = False
        cue_first = {"disruptive": True, "induction": True}
        for ep_idx in cues_by_episode:
            for c in cues_by_episode[ep_idx]:
                cs = c["cue_sec"]
                if cs < overview_start_sec:
                    continue
                ct = sec_to_time.get(cs)
                if ct is None:
                    continue
                ctype = c["cue_type"] if c["cue_type"] in cue_color else "induction"
                label = f"{ctype} cue" if cue_first.get(ctype, False) else None
                cue_first[ctype] = False
                ax_o.axvline(ct, color=cue_color.get(ctype, "tab:purple"), linestyle=":", linewidth=1.0, alpha=0.8, label=label)
        # Zoom y-axis for readability while keeping full peak visibility.
        y_zoom_ref = sorted(y_all)
        lo_idx = max(0, int(0.01 * (len(y_zoom_ref) - 1)))
        hi_idx = max(0, int(0.995 * (len(y_zoom_ref) - 1)))
        y_lo = y_zoom_ref[lo_idx]
        y_hi = y_zoom_ref[hi_idx]
        if y_hi <= y_lo:
            y_lo = min(y_all)
            y_hi = max(y_all)
        y_max = max(y_all)
        y_upper = max(y_hi, y_max)
        pad = max(0.5, 0.1 * (y_upper - y_lo if y_upper > y_lo else 1.0))
        ax_o.set_ylim(max(0.0, y_lo - pad), y_upper + pad)
        ax_o.set_title(f"Motion overview with REM windows | pid={sel_pid} | night={sel_night} | session_start={sel_start}")
        ax_o.set_xlabel("Time (Boston)")
        ax_o.set_ylabel("motion_smoothed")
        ax_o.grid(True, alpha=0.25)
        ax_o.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        ax_o.tick_params(axis="x", labelrotation=30, labelsize=8)
        ax_o.legend(loc="upper right", fontsize=8)
        fig_o.tight_layout()
        fig_o.savefig(overview_out, dpi=160)
        outputs.append(("overview", overview_out))

    if args.plot_mode in ("per-rem", "both"):
        per_rem_out = args.output
        if args.plot_mode == "both" or not per_rem_out:
            if sel_night is not None:
                per_rem_out = os.path.join(MOTION_PLOTS_DIR, f"motion_per_rem_{sel_pid}_night{sel_night}.png")
            else:
                per_rem_out = os.path.join(MOTION_PLOTS_DIR, f"motion_per_rem_{sel_pid}.png")
        per_rem_dir = os.path.dirname(per_rem_out)
        if per_rem_dir:
            os.makedirs(per_rem_dir, exist_ok=True)
        n = len(rem_eps)
        ncols = 2 if n > 1 else 1
        nrows = int(math.ceil(n / ncols))
        fig, axes = plt.subplots(nrows, ncols, figsize=(16, max(4.0 * nrows, 5)))
        try:
            axes = axes.flatten()
        except Exception:
            axes = [axes]
        for i, ep in enumerate(rem_eps):
            ax = axes[i]
            ep_idx = ep["episode_index"]
            start_sec = ep["start_sec"]
            end_sec = start_sec + ep["dur_sec"]
            win_start_sec = max(first_valid_sec, start_sec - max(0, args.per_rem_pre_sec))
            win_end_sec = end_sec + max(0, args.per_rem_post_sec)
            xs = []
            ys = []
            for sec in range(win_start_sec, win_end_sec + 1):
                if sec not in sec_to_val or sec not in sec_to_time:
                    continue
                xs.append(sec_to_time[sec])
                ys.append(round(sec_to_val[sec], 3))
            if not xs:
                ax.text(0.5, 0.5, "No motion values", transform=ax.transAxes, ha="center", va="center")
                ax.axis("off")
                continue
            ax.plot(xs, ys, color="tab:blue", linewidth=1.2, marker="o", markersize=2)
            rem_start_t = sec_to_time.get(start_sec)
            rem_end_t = sec_to_time.get(end_sec)
            if rem_start_t is not None and rem_end_t is not None:
                ax.axvspan(rem_start_t, rem_end_t, alpha=0.12, color="tab:orange", label="REM window")
            start_t = sec_to_time.get(start_sec)
            start_v = sec_to_val.get(start_sec)
            if start_t is not None and start_v is not None:
                ax.axvline(start_t, color="yellow", linestyle="--", linewidth=1.0, alpha=0.95, label="REM start")
                ax.scatter([start_t], [round(start_v, 3)], color="yellow", s=28, zorder=4, edgecolors="black", linewidths=0.4)
            end_t = sec_to_time.get(end_sec)
            end_v = sec_to_val.get(end_sec)
            if end_t is not None and end_v is not None:
                ax.axvline(end_t, color="black", linestyle="--", linewidth=1.0, alpha=0.95, label="REM end")
                ax.scatter([end_t], [round(end_v, 3)], color="black", s=24, zorder=4)
            local_cues = sorted(cues_by_episode.get(ep_idx, []), key=lambda d: d["cue_sec"])
            cue_first = {"disruptive": True, "induction": True}
            for c in local_cues:
                cs = c["cue_sec"]
                if cs < win_start_sec or cs > win_end_sec:
                    continue
                ct = sec_to_time.get(cs)
                if ct is None:
                    continue
                ccol = cue_color.get(c["cue_type"], "tab:purple")
                ctype = c["cue_type"] if c["cue_type"] in cue_color else "induction"
                clabel = f"{ctype} cue" if cue_first.get(ctype, False) else None
                cue_first[ctype] = False
                ax.axvline(ct, color=ccol, linestyle=":", linewidth=1.0, alpha=0.9, label=clabel)
            ax.set_title(f"REM #{ep_idx} | {ep['start_clock']} -> {ep['end_clock']} | dur={ep['dur_sec']}s", fontsize=9)
            ax.set_xlabel("Time (Boston)")
            ax.set_ylabel("motion_smoothed")
            ax.grid(True, alpha=0.25)
            ax.xaxis.set_major_locator(mdates.SecondLocator(interval=30))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
            ax.tick_params(axis="x", labelrotation=30, labelsize=8)
            ax.legend(loc="upper right", fontsize=7)
        for j in range(len(rem_eps), len(axes)):
            axes[j].axis("off")
        fig.suptitle(f"REM episode timelines (smoothed motion) | pid={sel_pid} | night={sel_night} | session_start={sel_start}", fontsize=11)
        fig.tight_layout(rect=[0, 0, 1, 0.96])
        fig.savefig(per_rem_out, dpi=160)
        outputs.append(("per-rem", per_rem_out))

    for mode, out in outputs:
        print(f"Saved {mode} plot to {out}")
    print(f"Selected session: pid={sel_pid}, night_number={sel_night}, session_start_boston={sel_start}")
    if has_smoothed_col:
        print(f"Applied smoothed second_index offset: +{sec_offset}s")
    print(f"REM episodes plotted: {len(rem_eps)}")


if __name__ == "__main__":
    main()
