#!/usr/bin/env python3
"""
Plot per-second motion time series from 4h onward and highlight REM episodes.

Inputs:
  - motion_per_second_series.csv
  - rem_episodes.csv

Output:
  - PNG figure (default: rem_cutoff_plot.png)
"""

import argparse
import csv
import os
from datetime import datetime
from typing import List, Dict, Optional

import matplotlib.pyplot as plt
import matplotlib.dates as mdates


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CUTOFF_CSV = os.path.join(BASE_DIR, "motion_per_second_series.csv")
DEFAULT_REM_CSV = os.path.join(BASE_DIR, "rem_episodes.csv")
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "rem_cutoff_plot.png")
FOUR_HOURS_SECONDS = 4 * 60 * 60


def parse_plot_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    # Current exports use time-only format HH:MM:SS.
    try:
        return datetime.strptime(value, "%H:%M:%S")
    except ValueError:
        pass
    # Backward compatibility for older ISO exports:
    # read the wall-clock time only and ignore timezone offsets to avoid shifts.
    try:
        dt = datetime.fromisoformat(value)
        return datetime(1900, 1, 1, dt.hour, dt.minute, dt.second, dt.microsecond)
    except ValueError:
        return None


def format_duration_hms(total_seconds: float) -> str:
    secs = int(round(total_seconds))
    if secs < 0:
        secs = 0
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def choose_session(
    cutoff_rows: List[Dict[str, str]],
    rem_rows: List[Dict[str, str]],
    pid: Optional[str],
    session_start_boston: Optional[str],
) -> str:
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
        return key[1]

    if pid:
        pid_sessions = [(k, n) for k, n in sessions.items() if k[0] == pid]
        if not pid_sessions:
            raise ValueError(f"No sessions found for pid={pid}")
        # Pick the longest one by number of seconds.
        pid_sessions.sort(key=lambda x: x[1], reverse=True)
        return pid_sessions[0][0][1]

    # No filters: pick longest session overall.
    all_sessions = sorted(sessions.items(), key=lambda x: x[1], reverse=True)
    return all_sessions[0][0][1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot per-second motion curve from 4h and highlight REM episodes.")
    parser.add_argument("--cutoff-csv", default=DEFAULT_CUTOFF_CSV, help="Path to motion_per_second_series.csv")
    parser.add_argument("--rem-csv", default=DEFAULT_REM_CSV, help="Path to rem_episodes.csv")
    parser.add_argument("--pid", default=None, help="Filter by participant id")
    parser.add_argument(
        "--session-start-boston",
        default=None,
        help="Exact session_start_boston to plot (HH:MM:SS)",
    )
    parser.add_argument(
        "--labels",
        choices=["none", "all", "click", "table"],
        default="none",
        help="How to show T-1m/T/T+1m labels: none (default), all, click, or table",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Open interactive window (needed for --labels click)",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output PNG filename")
    args = parser.parse_args()

    cutoff_rows = read_csv(args.cutoff_csv)
    rem_rows = read_csv(args.rem_csv)

    selected_start = choose_session(
        cutoff_rows=cutoff_rows,
        rem_rows=rem_rows,
        pid=args.pid,
        session_start_boston=args.session_start_boston,
    )

    # Collect cutoff rows for selected session (all seconds), then keep >=4h for base curve.
    all_session_cutoff = []
    filtered_cutoff = []
    selected_pid = None
    for row in cutoff_rows:
        if row.get("session_start_boston") != selected_start:
            continue
        if args.pid and row.get("pid") != args.pid:
            continue
        try:
            second_index = int(float(row.get("second_index", "0")))
            raw_val = row.get("motion_per_second")
            if raw_val is None:
                raw_val = row.get("motion_80pct_cutoff", "nan")
            cutoff_value = float(raw_val)
        except ValueError:
            continue
        ts = parse_plot_time(row.get("time_boston", ""))
        if ts is None:
            continue
        point = (ts, cutoff_value, second_index, row.get("pid", ""))
        all_session_cutoff.append(point)
        if second_index >= FOUR_HOURS_SECONDS:
            filtered_cutoff.append(point)
        if selected_pid is None:
            selected_pid = row.get("pid", "")

    if not filtered_cutoff:
        raise ValueError("No cutoff points found from 4h onward for the selected session.")

    filtered_cutoff.sort(key=lambda x: x[2])
    x_times = [r[0] for r in filtered_cutoff]
    y_cutoff = [r[1] for r in filtered_cutoff]

    # Fast lookup by epoch second.
    cutoff_by_second = {p[2]: p for p in all_session_cutoff}

    # Filter REM episodes for same session.
    rem_intervals = []
    total_rem_seconds = 0.0
    for row in rem_rows:
        if row.get("session_start_boston") != selected_start:
            continue
        if selected_pid and row.get("pid") != selected_pid:
            continue
        start_dt = parse_plot_time(row.get("episode_start_boston", ""))
        end_dt = parse_plot_time(row.get("episode_end_boston", ""))
        if start_dt is None or end_dt is None:
            continue
        try:
            total_rem_seconds += float(row.get("episode_duration_sec", "0") or 0)
        except ValueError:
            pass
        try:
            start_epoch = int(float(row.get("episode_start_epoch_sec", "0") or 0))
        except ValueError:
            start_epoch = None
        try:
            ep_motion_avg = float(row.get("episode_motion_avg", "nan"))
        except ValueError:
            ep_motion_avg = None
        rem_intervals.append((start_dt, end_dt, start_epoch, ep_motion_avg))

    if args.labels == "table":
        fig, (ax, ax_table) = plt.subplots(
            2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [3.2, 1.8]}
        )
    else:
        fig, ax = plt.subplots(figsize=(14, 6))
        ax_table = None
    ax.plot(x_times, y_cutoff, linewidth=1.3, label="motion per second")

    first_label = True
    trigger_times = []
    trigger_vals = []
    trigger_notes = []
    table_rows = []
    for rem_idx, (start_dt, end_dt, start_epoch, ep_motion_avg) in enumerate(rem_intervals):
        # Shade REM window
        ax.axvspan(start_dt, end_dt, alpha=0.22, color="tab:orange", label="REM episode" if first_label else None)
        # Explicit start/end markers
        ax.axvline(start_dt, linestyle="--", linewidth=1, color="tab:red", alpha=0.8, label="REM start" if first_label else None)
        ax.axvline(end_dt, linestyle="--", linewidth=1, color="tab:green", alpha=0.8, label="REM end" if first_label else None)

        # Annotate exact point values at T-1min, T, T+1min for each REM trigger.
        if start_epoch is not None:
            p_minus = cutoff_by_second.get(start_epoch - 60)
            p_t = cutoff_by_second.get(start_epoch)
            p_plus = cutoff_by_second.get(start_epoch + 60)

            t_minus = p_minus[0] if p_minus else None
            y_minus = p_minus[1] if p_minus else None
            t_now = p_t[0] if p_t else None
            y_now = p_t[1] if p_t else None
            t_plus = p_plus[0] if p_plus else None
            y_plus = p_plus[1] if p_plus else None

            if t_minus is not None and y_minus is not None:
                ax.scatter([t_minus], [y_minus], s=18, color="tab:purple", zorder=5)
            if t_now is not None and y_now is not None:
                ax.scatter([t_now], [y_now], s=20, color="black", zorder=6)
            if t_plus is not None and y_plus is not None:
                ax.scatter([t_plus], [y_plus], s=18, color="tab:brown", zorder=5)

            if t_now is not None and y_now is not None:
                y_text = y_now
                y_offset = 16 if (rem_idx % 2 == 0) else -24
                before_txt = f"{y_minus:.3f}" if y_minus is not None else "na"
                now_txt = f"{y_now:.3f}"
                after_txt = f"{y_plus:.3f}" if y_plus is not None else "na"
                note = f"T-1m={before_txt}\nT={now_txt}\nT+1m={after_txt}"
                trigger_times.append(t_now)
                trigger_vals.append(y_now)
                trigger_notes.append(note)
                rem_num = len(trigger_times)
                table_rows.append([str(rem_num), t_now.strftime("%H:%M:%S"), before_txt, now_txt, after_txt])
                if args.labels == "table":
                    # Compact numeric marker; details go in table panel.
                    ax.annotate(
                        str(rem_num),
                        xy=(t_now, y_text),
                        xytext=(0, 8),
                        textcoords="offset points",
                        fontsize=7,
                        ha="center",
                        va="bottom",
                        color="black",
                        bbox=dict(boxstyle="circle,pad=0.18", fc="white", ec="0.6", alpha=0.85),
                    )
                if args.labels == "all":
                    ax.annotate(
                        note,
                        xy=(t_now, y_text),
                        xytext=(0, y_offset),
                        textcoords="offset points",
                        fontsize=7,
                        ha="center",
                        va="bottom" if y_offset > 0 else "top",
                        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="0.7", alpha=0.75),
                        arrowprops=dict(arrowstyle="-", color="0.5", lw=0.7, alpha=0.8),
                    )

        first_label = False

    trigger_scatter = None
    if trigger_times:
        trigger_scatter = ax.scatter(
            trigger_times,
            trigger_vals,
            s=28,
            color="black",
            zorder=7,
            label="REM trigger (click for values)" if args.labels == "click" else "REM trigger",
            picker=5,
        )

    total_rem_hms = format_duration_hms(total_rem_seconds)
    ax.set_title(
        f"Motion per second (from 4h) with REM episodes | pid={selected_pid or 'N/A'} | total REM={total_rem_hms}"
    )
    ax.set_xlabel("Boston time (HH:MM:SS)")
    ax.set_ylabel("motion_per_second")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    # Show only time on x-axis with stable tick density.
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=8, maxticks=14))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    ax.tick_params(axis="x", labelrotation=45, labelsize=8)
    ax.tick_params(axis="x", which="major", labelbottom=True)

    if args.labels == "table" and ax_table is not None:
        ax_table.axis("off")
        if table_rows:
            col_labels = ["#", "Trigger time", "T-1m", "T", "T+1m"]
            tbl = ax_table.table(
                cellText=table_rows,
                colLabels=col_labels,
                cellLoc="center",
                loc="center",
                bbox=[0.0, 0.0, 1.0, 0.90],
            )
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(7)
            tbl.scale(1.0, 1.15)
            ax_table.text(
                0.0,
                0.96,
                "REM trigger values",
                transform=ax_table.transAxes,
                ha="left",
                va="bottom",
                fontsize=10,
            )
        else:
            ax_table.text(0.5, 0.5, "No REM trigger values available", ha="center", va="center")

    fig.tight_layout()
    if args.labels == "table":
        fig.subplots_adjust(hspace=0.22)

    if args.labels == "click" and trigger_scatter is not None:
        active = {"ann": None}

        def on_pick(event):
            if event.artist is not trigger_scatter or len(event.ind) == 0:
                return
            idx = event.ind[0]
            if active["ann"] is not None:
                active["ann"].remove()
                active["ann"] = None
            active["ann"] = ax.annotate(
                trigger_notes[idx],
                xy=(trigger_times[idx], trigger_vals[idx]),
                xytext=(10, 10),
                textcoords="offset points",
                fontsize=8,
                ha="left",
                va="bottom",
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="0.6", alpha=0.9),
                arrowprops=dict(arrowstyle="->", color="0.4", lw=0.8),
            )
            fig.canvas.draw_idle()

        fig.canvas.mpl_connect("pick_event", on_pick)

    fig.savefig(args.output, dpi=160)
    print(f"Saved plot to {args.output}")
    print(f"Session start (Boston): {selected_start}")
    print(f"Points plotted (>=4h): {len(filtered_cutoff)}")
    print(f"REM episodes highlighted: {len(rem_intervals)}")
    if args.labels == "click" and not args.show:
        print("Tip: use --show to click trigger points and reveal T-1m/T/T+1m values interactively.")
    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
