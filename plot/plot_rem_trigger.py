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
DEFAULT_CUTOFF_CSV = os.path.join(BASE_DIR, "motion_per_second_series.csv")
DEFAULT_REM_CSV = os.path.join(BASE_DIR, "rem_episodes.csv")
DEFAULT_CUE_CSV = os.path.join(BASE_DIR, "cue_events.csv")
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "rem_trigger_dynamics.png")


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


def choose_session(cutoff_rows: List[Dict[str, str]], pid: Optional[str], session_start_boston: Optional[str]) -> Tuple[str, str]:
    sessions = {}
    for row in cutoff_rows:
        row_pid = row.get("pid", "")
        row_start = row.get("session_start_boston", "")
        if row_pid and row_start:
            sessions[(row_pid, row_start)] = sessions.get((row_pid, row_start), 0) + 1

    if not sessions:
        raise ValueError("No sessions found in cutoff CSV.")

    if pid and session_start_boston:
        key = (pid, session_start_boston)
        if key not in sessions:
            raise ValueError(f"Session not found for pid={pid}, session_start_boston={session_start_boston}")
        return key

    if pid:
        pid_sessions = [(k, n) for k, n in sessions.items() if k[0] == pid]
        if not pid_sessions:
            raise ValueError(f"No sessions found for pid={pid}")
        pid_sessions.sort(key=lambda x: x[1], reverse=True)
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
    parser.add_argument("--cutoff-csv", default=DEFAULT_CUTOFF_CSV)
    parser.add_argument("--rem-csv", default=DEFAULT_REM_CSV)
    parser.add_argument("--cue-csv", default=DEFAULT_CUE_CSV)
    parser.add_argument("--pid", default=None)
    parser.add_argument("--session-start-boston", default=None, help="HH:MM:SS")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    cutoff_rows = read_csv(args.cutoff_csv)
    rem_rows = read_csv(args.rem_csv)
    cue_rows = read_csv(args.cue_csv)

    sel_pid, sel_start = choose_session(cutoff_rows, args.pid, args.session_start_boston)

    # Build per-second data maps for selected session.
    sec_to_val: Dict[int, float] = {}
    sec_to_time: Dict[int, datetime] = {}
    for row in cutoff_rows:
        if row.get("pid") != sel_pid or row.get("session_start_boston") != sel_start:
            continue
        sec = as_int(row.get("second_index", ""))
        raw_val = row.get("motion_per_second")
        if raw_val is None:
            raw_val = row.get("motion_80pct_cutoff", "")
        val = as_float(raw_val)
        t = parse_hms(row.get("time_boston", ""))
        if sec is None or val is None or t is None:
            continue
        sec_to_val[sec] = val
        sec_to_time[sec] = t

    if not sec_to_val:
        raise ValueError("No motion-per-second data found for selected session.")

    # Parse REM episodes for selected session.
    rem_eps = []
    for row in rem_rows:
        if row.get("pid") != sel_pid or row.get("session_start_boston") != sel_start:
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

    # Keep only REM episodes that include both disruption and induction phases.
    filtered_eps = []
    for ep in rem_eps:
        ep_idx = ep["episode_index"]
        cue_types = {c["cue_type"] for c in cues_by_episode.get(ep_idx, [])}
        if "disruptive" in cue_types and "induction" in cue_types:
            filtered_eps.append(ep)
    rem_eps = filtered_eps

    if not rem_eps:
        raise ValueError("No REM episodes with both disruption and induction phases in selected session.")

    n = len(rem_eps)
    ncols = 2 if n > 1 else 1
    nrows = int(math.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(16, max(4.0 * nrows, 5)))
    try:
        axes = axes.flatten()
    except Exception:
        axes = [axes]

    cue_color = {"disruptive": "tab:red", "induction": "tab:green"}

    for i, ep in enumerate(rem_eps):
        ax = axes[i]
        ep_idx = ep["episode_index"]
        start_sec = ep["start_sec"]
        end_sec = start_sec + ep["dur_sec"]

        xs = []
        ys = []
        for sec in range(start_sec, end_sec + 1):
            if sec not in sec_to_val or sec not in sec_to_time:
                continue
            xs.append(sec_to_time[sec])
            ys.append(round(sec_to_val[sec], 3))

        if not xs:
            ax.text(0.5, 0.5, "No per-second values", transform=ax.transAxes, ha="center", va="center")
            ax.axis("off")
            continue

        ax.plot(xs, ys, color="tab:blue", linewidth=1.2, marker="o", markersize=2)
        ax.axvspan(xs[0], xs[-1], alpha=0.12, color="tab:orange")

        # REM detection marker at episode start.
        start_t = sec_to_time.get(start_sec)
        start_v = sec_to_val.get(start_sec)
        if start_t is not None and start_v is not None:
            ax.axvline(start_t, color="tab:red", linestyle="--", linewidth=1.0, alpha=0.9)
            ax.scatter([start_t], [round(start_v, 3)], color="tab:red", s=28, zorder=4)
            ax.annotate(
                f"REM detected\nmotion={start_v:.3f}",
                xy=(start_t, round(start_v, 3)),
                xytext=(8, 8),
                textcoords="offset points",
                fontsize=7,
                ha="left",
                va="bottom",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="0.7", alpha=0.85),
            )

        # Cues with detailed annotations.
        local_cues = sorted(cues_by_episode.get(ep_idx, []), key=lambda d: d["cue_sec"])
        # Adaptive lane assignment to reduce label overlap for induction cues.
        induction_lane_last_x: List[int] = []
        lane_min_sep_sec = 25
        lane_step_y = 42
        base_y = -10
        for c in local_cues:
            cs = c["cue_sec"]
            if cs < start_sec or cs > end_sec:
                continue
            ct = sec_to_time.get(cs)
            cv = sec_to_val.get(cs)
            if ct is None or cv is None:
                continue
            cvm1 = sec_to_val.get(cs - 1)
            cv1 = sec_to_val.get(cs + 1)
            cv2 = sec_to_val.get(cs + 2)
            cv3 = sec_to_val.get(cs + 3)
            txtm1 = f"{cvm1:.3f}" if cvm1 is not None else "na"
            txt0 = f"{cv:.3f}"
            txt1 = f"{cv1:.3f}" if cv1 is not None else "na"
            txt2 = f"{cv2:.3f}" if cv2 is not None else "na"
            txt3 = f"{cv3:.3f}" if cv3 is not None else "na"
            ccol = cue_color.get(c["cue_type"], "tab:purple")

            ax.axvline(ct, color=ccol, linestyle=":", linewidth=1.0, alpha=0.9)
            ax.scatter([ct], [round(cv, 3)], color=ccol, s=24, zorder=5)
            if c["cue_type"] == "disruptive":
                note = f"{c['cue_type']} | t={c['clock']}"
                xytext = (6, -10)
            else:
                note = (
                    f"{c['cue_type']} | t={c['clock']}\n"
                    f"vol={c['volume']} | arousal={c['arousal']}\n"
                    f"motion -1/0s = {txtm1}, {txt0}\n"
                    f"motion +1/+2/+3s = {txt1}, {txt2}, {txt3}"
                )
                xr = cs - start_sec
                lane_idx = None
                for i_lane, last_x in enumerate(induction_lane_last_x):
                    if xr - last_x >= lane_min_sep_sec:
                        lane_idx = i_lane
                        break
                if lane_idx is None:
                    induction_lane_last_x.append(xr)
                    lane_idx = len(induction_lane_last_x) - 1
                else:
                    induction_lane_last_x[lane_idx] = xr
                xytext = (8, base_y - lane_idx * lane_step_y)
            ax.annotate(
                note,
                xy=(ct, round(cv, 3)),
                xytext=xytext,
                textcoords="offset points",
                fontsize=6.5,
                ha="left",
                va="top",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=ccol, alpha=0.8),
            )

        ax.set_title(
            f"REM #{ep_idx} | {ep['start_clock']} -> {ep['end_clock']} | dur={ep['dur_sec']}s",
            fontsize=9,
        )
        ax.set_xlabel("Time (Boston)")
        ax.set_ylabel("motion_per_second")
        ax.grid(True, alpha=0.25)
        if ep["dur_sec"] <= 120:
            ax.xaxis.set_major_locator(mdates.SecondLocator(interval=10))
        elif ep["dur_sec"] <= 300:
            ax.xaxis.set_major_locator(mdates.SecondLocator(interval=30))
        else:
            ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=1))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        ax.tick_params(axis="x", labelrotation=30, labelsize=8)

    for j in range(len(rem_eps), len(axes)):
        axes[j].axis("off")

    fig.suptitle(
        f"REM episode timelines (motion per second) | pid={sel_pid} | session_start={sel_start}",
        fontsize=11,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(args.output, dpi=160)

    print(f"Saved plot to {args.output}")
    print(f"Selected session: pid={sel_pid}, session_start_boston={sel_start}")
    print(f"REM episodes plotted: {len(rem_eps)}")


if __name__ == "__main__":
    main()
