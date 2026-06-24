#!/usr/bin/env python3
"""
Check sensor sampling stability from motion_per_second_series.csv.

Per session, this script reports and optionally plots:
- sensor_events_per_second (effective sampling frequency in Hz)
- sensor_event_dt_avg_ms (mean inter-event interval per second)
- sensor_event_dt_max_ms (max inter-event interval per second; spikes indicate stalls/gaps)
"""

import argparse
import csv
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
DEFAULT_DATA_DIR = os.path.join(PROJECT_ROOT, "data_prep", "output")
DEFAULT_MOTION_CSV = os.path.join(DEFAULT_DATA_DIR, "motion_per_second_series.csv")
DEFAULT_OUT_CSV = os.path.join(BASE_DIR, "data_night", "sensor_sampling_summary.csv")
DEFAULT_OUT_DIR = os.path.join(BASE_DIR, "sampling_plots")

from export_data import (  # noqa: E402
    LOW_HZ_THRESHOLD,
    SAMPLING_SUMMARY_FIELDS,
    SEVERE_DT_MS,
    STALL_DT_MS,
    build_sensor_sampling_summary_row,
)


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


def hms_anchor(value: str) -> Optional[datetime]:
    dt = parse_hms(value)
    if dt is None:
        return None
    return datetime(2000, 1, 1, dt.hour, dt.minute, dt.second)


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "unknown"))


def load_sessions(motion_csv: str) -> Dict[Tuple[str, str, str], Dict[str, List[float]]]:
    sessions: Dict[Tuple[str, str, str], Dict[str, List[float]]] = {}
    with open(motion_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = (row.get("pid") or "").strip()
            night = (row.get("night_number") or "").strip()
            start = (row.get("session_start_boston") or "").strip()
            if not pid or not start:
                continue
            key = (pid, night, start)
            if key not in sessions:
                sessions[key] = {
                    "second_index": [],
                    "eps": [],
                    "dt_avg": [],
                    "dt_max": [],
                }
            sec = as_int(row.get("second_index", ""))
            if sec is not None:
                sessions[key]["second_index"].append(float(sec))
            eps = as_float(row.get("sensor_events_per_second", ""))
            if eps is not None and np.isfinite(eps):
                sessions[key]["eps"].append(float(eps))
            dt_avg = as_float(row.get("sensor_event_dt_avg_ms", ""))
            if dt_avg is not None and np.isfinite(dt_avg):
                sessions[key]["dt_avg"].append(float(dt_avg))
            dt_max = as_float(row.get("sensor_event_dt_max_ms", ""))
            if dt_max is not None and np.isfinite(dt_max):
                sessions[key]["dt_max"].append(float(dt_max))
    return sessions


def session_matches(
    key: Tuple[str, str, str],
    pid: Optional[str],
    night_number: Optional[int],
    session_start_boston: Optional[str],
) -> bool:
    k_pid, k_night, k_start = key
    if pid and k_pid != str(pid):
        return False
    if night_number is not None and str(k_night) != str(night_number):
        return False
    if session_start_boston and k_start != session_start_boston:
        return False
    return True


def plot_session(
    key: Tuple[str, str, str],
    series: Dict[str, List[float]],
    out_dir: str,
) -> Optional[str]:
    pid, night, start = key
    anchor = hms_anchor(start)
    if anchor is None:
        return None

    # Use smallest common length for aligned per-second plotting.
    n = min(len(series["eps"]), len(series["dt_avg"]), len(series["dt_max"]))
    if n <= 0:
        return None

    times = [anchor + timedelta(seconds=i) for i in range(n)]
    eps = series["eps"][:n]
    dt_avg = series["dt_avg"][:n]
    dt_max = series["dt_max"][:n]

    fig, axes = plt.subplots(3, 1, figsize=(15, 8), sharex=True)

    axes[0].plot(times, eps, color="tab:blue", linewidth=0.8, alpha=0.95)
    axes[0].axhline(LOW_HZ_THRESHOLD, color="tab:red", linestyle="--", linewidth=1.0, alpha=0.8, label="5 Hz threshold")
    axes[0].set_ylabel("events/s")
    axes[0].set_title("Sensor events per second")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend(loc="upper right", fontsize=8)

    axes[1].plot(times, dt_avg, color="tab:orange", linewidth=0.8, alpha=0.95)
    axes[1].set_ylabel("ms")
    axes[1].set_title("Inter-event dt average (ms)")
    axes[1].grid(True, alpha=0.25)

    axes[2].plot(times, dt_max, color="tab:green", linewidth=0.8, alpha=0.95)
    axes[2].axhline(STALL_DT_MS, color="tab:red", linestyle="--", linewidth=1.0, alpha=0.8, label="500 ms stall")
    axes[2].axhline(SEVERE_DT_MS, color="tab:purple", linestyle="--", linewidth=1.0, alpha=0.8, label="1000 ms severe stall")
    axes[2].set_ylabel("ms")
    axes[2].set_title("Inter-event dt max (ms)")
    axes[2].grid(True, alpha=0.25)
    axes[2].legend(loc="upper right", fontsize=8)

    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    axes[-1].tick_params(axis="x", labelrotation=30, labelsize=8)
    axes[-1].set_xlabel("Time (session local clock)")

    fig.suptitle(f"Sampling stability | pid={pid} | night={night} | start={start}", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.96])

    os.makedirs(out_dir, exist_ok=True)
    out_name = f"sampling_{safe_name(pid)}_night{safe_name(night)}_{safe_name(start)}.png"
    out_path = os.path.join(out_dir, out_name)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate per-session sampling stability report/plots.")
    parser.add_argument("--motion-csv", default=DEFAULT_MOTION_CSV)
    parser.add_argument("--pid", default=None)
    parser.add_argument("--night-number", type=int, default=None)
    parser.add_argument("--session-start-boston", default=None, help="HH:MM:SS")
    parser.add_argument("--out-csv", default=DEFAULT_OUT_CSV)
    parser.add_argument("--plot-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--max-plots", type=int, default=30, help="Safety cap for number of generated session plots.")
    args = parser.parse_args()

    sessions = load_sessions(args.motion_csv)
    selected_keys = [k for k in sorted(sessions.keys()) if session_matches(k, args.pid, args.night_number, args.session_start_boston)]

    if not selected_keys:
        raise ValueError("No matching sessions found in motion CSV for provided filters.")

    rows: List[Dict[str, str]] = []
    generated_plots: List[str] = []
    for idx, key in enumerate(selected_keys):
        series = sessions[key]
        row = build_sensor_sampling_summary_row(key, series)
        rows.append(row)
        if not args.no_plots and idx < max(0, int(args.max_plots)):
            plot_path = plot_session(key, series, args.plot_dir)
            if plot_path:
                generated_plots.append(plot_path)

    os.makedirs(os.path.dirname(args.out_csv) or ".", exist_ok=True)
    with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SAMPLING_SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Selected sessions: {len(selected_keys)}")
    print(f"Summary CSV: {args.out_csv}")
    if args.no_plots:
        print("Plots: disabled (--no-plots)")
    else:
        print(f"Generated plots: {len(generated_plots)}")
        if selected_keys and len(selected_keys) > max(0, int(args.max_plots)):
            print(f"Plot cap reached (max_plots={args.max_plots}).")


if __name__ == "__main__":
    main()

