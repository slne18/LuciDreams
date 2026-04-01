#!/usr/bin/env python3
"""
Plot EEG by REM phase.

For each REM episode (optionally only those with both disruptive+induction cues):
- x-axis: clock time during the REM phase
- y-axis: EEG RAW_AF7 and RAW_AF8
- cue markers/labels overlaid at cue times
"""

import argparse
import math
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REM_CSV = os.path.join(BASE_DIR, "rem_episodes.csv")
DEFAULT_CUE_CSV = os.path.join(BASE_DIR, "cue_events.csv")
EEG_DATA_DIR = os.path.join(BASE_DIR, "EEG")
EEG_PLOTS_DIR = os.path.join(BASE_DIR, "eeg_plots")
DEFAULT_OUTPUT_TEMPLATE = os.path.join(EEG_PLOTS_DIR, "eeg_rem_phases_{pid}.png")
SECONDS_PER_DAY = 24 * 3600


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


def robust_zscore(series: pd.Series) -> pd.Series:
    """Robust centering/scaling (median + MAD), common for EEG visualization."""
    vals = pd.to_numeric(series, errors="coerce").astype(float)
    med = float(np.nanmedian(vals))
    mad = float(np.nanmedian(np.abs(vals - med)))
    scale = 1.4826 * mad
    if not np.isfinite(scale) or scale < 1e-9:
        scale = float(np.nanstd(vals))
    if not np.isfinite(scale) or scale < 1e-9:
        scale = 1.0
    return (vals - med) / scale


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


def choose_rem_session(rem_df: pd.DataFrame, pid: str, session_start_boston: Optional[str]) -> pd.DataFrame:
    d = rem_df[rem_df["pid"].astype(str) == pid].copy()
    if d.empty:
        raise ValueError(f"No REM rows found for pid={pid}")
    if session_start_boston:
        d = d[d["session_start_boston"].astype(str) == session_start_boston].copy()
        if d.empty:
            raise ValueError(f"No REM rows found for pid={pid}, session_start_boston={session_start_boston}")
        return d
    # default: choose session with most REM rows
    grp = d.groupby("session_start_boston").size().sort_values(ascending=False)
    sel = str(grp.index[0])
    return d[d["session_start_boston"].astype(str) == sel].copy()


def main() -> None:
    p = argparse.ArgumentParser(description="Plot RAW_AF7/RAW_AF8 for each REM phase with cue markers.")
    p.add_argument("--pid", required=True, help="Participant ID (e.g. Sole)")
    p.add_argument("--eeg-csv", default=None, help="Path to EEG csv. Default: plot/EEG_<pid>.csv")
    p.add_argument("--rem-csv", default=DEFAULT_REM_CSV)
    p.add_argument("--cue-csv", default=DEFAULT_CUE_CSV)
    p.add_argument("--session-start-boston", default=None, help="Optional session_start_boston (HH:MM:SS)")
    p.add_argument("--both-phases-only", action="store_true", help="Keep only REM episodes with both disruptive and induction cues")
    p.add_argument("--plot-mode", choices=["overview", "per-rem"], default="overview", help="overview: full session; per-rem: one panel per REM episode")
    p.add_argument("--max-points", type=int, default=40000, help="Max plotted points in session overview")
    p.add_argument("--per-rem-pre-sec", type=int, default=60, help="Seconds shown before REM start in per-rem mode")
    p.add_argument("--per-rem-post-sec", type=int, default=60, help="Seconds shown after REM end in per-rem mode")
    p.add_argument("--output", default=None)
    args = p.parse_args()
    output_png = args.output or DEFAULT_OUTPUT_TEMPLATE.format(pid=args.pid)
    output_dir = os.path.dirname(output_png)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    eeg_csv = args.eeg_csv or os.path.join(EEG_DATA_DIR, f"EEG_{args.pid}.csv")
    if not os.path.exists(eeg_csv):
        legacy_path = os.path.join(BASE_DIR, f"EEG_{args.pid}.csv")
        if os.path.exists(legacy_path):
            eeg_csv = legacy_path
    if not os.path.exists(eeg_csv):
        raise FileNotFoundError(f"EEG file not found: {eeg_csv}")

    rem_df = pd.read_csv(args.rem_csv)
    cue_df = pd.read_csv(args.cue_csv)

    rem_df = choose_rem_session(rem_df, args.pid, args.session_start_boston)
    rem_df["episode_index"] = pd.to_numeric(rem_df["episode_index"], errors="coerce")
    rem_df["episode_duration_sec"] = pd.to_numeric(rem_df["episode_duration_sec"], errors="coerce")
    rem_df = rem_df.dropna(subset=["episode_index"]).copy()
    rem_df["episode_index"] = rem_df["episode_index"].astype(int)
    rem_df = rem_df.sort_values("episode_index").reset_index(drop=True)

    cue_df = cue_df[cue_df["pid"].astype(str) == args.pid].copy()
    if "took_place" in cue_df.columns:
        cue_df = cue_df[cue_df["took_place"].astype(str).str.lower() == "true"].copy()
    cue_df["episode_index"] = pd.to_numeric(cue_df["episode_index"], errors="coerce")
    cue_df = cue_df.dropna(subset=["episode_index"]).copy()
    cue_df["episode_index"] = cue_df["episode_index"].astype(int)
    cue_df["train_index"] = pd.to_numeric(cue_df.get("train_index"), errors="coerce")
    cue_df["epoch_sec"] = pd.to_numeric(cue_df.get("epoch_sec"), errors="coerce")
    cue_df["event_time_boston"] = cue_df.get("event_time_boston", "").astype(str).str[:8]
    cue_df["event_sod"] = cue_df["event_time_boston"].apply(parse_hms_to_seconds)
    cue_df["cue_type"] = cue_df["cue_type"].astype(str).str.lower()

    if args.both_phases_only:
        keep = []
        for ep_idx, g in cue_df.groupby("episode_index"):
            types = set(g["cue_type"].tolist())
            if "disruptive" in types and "induction" in types:
                keep.append(ep_idx)
        rem_df = rem_df[rem_df["episode_index"].isin(keep)].copy()
        if rem_df.empty:
            raise ValueError("No REM episodes with both disruptive and induction cues.")

    # Load EEG
    eeg = pd.read_csv(
        eeg_csv,
        usecols=["TimeStamp", "RAW_AF7", "RAW_AF8"],
        parse_dates=["TimeStamp"],
    )
    eeg = eeg.dropna(subset=["TimeStamp"]).sort_values("TimeStamp").reset_index(drop=True)
    eeg["sec_of_day"] = (
        eeg["TimeStamp"].dt.hour * 3600
        + eeg["TimeStamp"].dt.minute * 60
        + eeg["TimeStamp"].dt.second
        + eeg["TimeStamp"].dt.microsecond / 1_000_000.0
    )
    eeg["date_only"] = eeg["TimeStamp"].dt.date

    if rem_df.empty:
        raise ValueError("No REM episodes to plot after filtering.")

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

    # Elapsed seconds since session start and 1-second aggregation for readability/speed.
    sess["elapsed_sec"] = np.where(
        sess["sec_of_day"] >= session_start_sod,
        sess["sec_of_day"] - session_start_sod,
        sess["sec_of_day"] + SECONDS_PER_DAY - session_start_sod,
    )
    sess["elapsed_bin"] = np.floor(sess["elapsed_sec"]).astype(int)
    agg = sess.groupby("elapsed_bin", as_index=False).agg(
        RAW_AF7=("RAW_AF7", "mean"),
        RAW_AF8=("RAW_AF8", "mean"),
    )

    agg["AF7_plot"] = robust_zscore(agg["RAW_AF7"]).clip(-6, 6)
    agg["AF8_plot"] = robust_zscore(agg["RAW_AF8"]).clip(-6, 6)
    if args.max_points > 0 and len(agg) > args.max_points:
        stride = int(math.ceil(len(agg) / float(args.max_points)))
        agg = agg.iloc[::stride].copy()

    cue_color = {"disruptive": "tab:red", "induction": "tab:green"}
    rem_episode_ids = set(rem_df["episode_index"].astype(int).tolist())
    cues_sess = cue_df[cue_df["episode_index"].astype(int).isin(rem_episode_ids)].copy()

    if args.plot_mode == "overview":
        fig, ax = plt.subplots(figsize=(18, 7))
        ax.plot(agg["elapsed_bin"], agg["AF7_plot"], color="tab:blue", linewidth=0.8, alpha=0.9, label="AF7 (robust z)")
        ax.plot(agg["elapsed_bin"], agg["AF8_plot"], color="tab:orange", linewidth=0.8, alpha=0.9, label="AF8 (robust z)")

        # REM windows over full session timeline.
        first_rem_label = True
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
                color="tab:green",
                label="REM window" if first_rem_label else None,
            )
            first_rem_label = False
            ax.axvline(rem_start_x, color="tab:red", linestyle="--", linewidth=0.8, alpha=0.55)

        # Cue markers (no text labels by default to keep plot clean/fast).
        for _, c in cues_sess.iterrows():
            cue_sod = c.get("event_sod")
            if pd.isna(cue_sod):
                continue
            cue_x = sec_of_day_to_elapsed(float(cue_sod), session_start_sod)
            ct = str(c.get("cue_type", "cue"))
            ax.axvline(cue_x, color=cue_color.get(ct, "tab:purple"), linestyle=":", linewidth=0.8, alpha=0.7)

        # Build concise HH:MM x ticks from elapsed seconds.
        total_sec = int(agg["elapsed_bin"].max()) if not agg.empty else 0
        tick_step = 30 * 60  # every 30 minutes
        ticks = np.arange(0, max(total_sec + tick_step, tick_step), tick_step)
        ax.set_xticks(ticks)
        ax.set_xticklabels([format_elapsed_hms(t)[:5] for t in ticks], rotation=0)

        ax.set_title(f"EEG session overview with REM windows | pid={args.pid} | start={session_start_hms}")
        ax.set_xlabel("Elapsed from session start (HH:MM)")
        ax.set_ylabel("Normalized EEG (robust z)")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="upper right", fontsize=8)
        fig.tight_layout()
    else:
        rem_plot_df = rem_df.copy()
        n = len(rem_plot_df)
        ncols = 2 if n > 1 else 1
        nrows = int(math.ceil(n / ncols))
        fig, axes = plt.subplots(nrows, ncols, figsize=(16, max(4.0 * nrows, 5)))
        try:
            axes = axes.flatten()
        except Exception:
            axes = [axes]
        plotted_count = 0
        for i, (_, ep) in enumerate(rem_plot_df.iterrows()):
            ax = axes[i]
            ep_idx = int(ep["episode_index"])
            start_sod = parse_hms_to_seconds(str(ep.get("episode_start_boston", "")))
            end_sod = parse_hms_to_seconds(str(ep.get("episode_end_boston", "")))
            if start_sod is None or end_sod is None:
                ax.text(0.5, 0.5, f"REM #{ep_idx}: invalid times", transform=ax.transAxes, ha="center", va="center")
                ax.axis("off")
                continue
            rem_start_x = sec_of_day_to_elapsed(start_sod, session_start_sod)
            rem_end_x = sec_of_day_to_elapsed(end_sod, session_start_sod)
            cues_ep = cues_sess[cues_sess["episode_index"] == ep_idx].copy()
            # Use event_time_boston for cue placement, and widen panel if needed so played cues are visible.
            cue_abs_times = []
            for _, c in cues_ep.iterrows():
                cue_sod = c.get("event_sod")
                if pd.isna(cue_sod):
                    continue
                cue_abs_times.append(sec_of_day_to_elapsed(float(cue_sod), session_start_sod))
            base_start = rem_start_x
            base_end = rem_end_x
            if cue_abs_times:
                base_start = min(base_start, min(cue_abs_times))
                base_end = max(base_end, max(cue_abs_times))
            win_start_x = max(0, base_start - max(0, args.per_rem_pre_sec))
            win_end_x = min(int(agg["elapsed_bin"].max()), base_end + max(0, args.per_rem_post_sec))
            seg = agg[(agg["elapsed_bin"] >= win_start_x) & (agg["elapsed_bin"] <= win_end_x)].copy()
            if seg.empty:
                ax.text(0.5, 0.5, f"REM #{ep_idx}: no EEG data", transform=ax.transAxes, ha="center", va="center")
                ax.axis("off")
                continue
            plotted_count += 1
            relx = seg["elapsed_bin"] - win_start_x
            ax.plot(relx, seg["AF7_plot"], color="tab:blue", linewidth=0.9, alpha=0.9, label="AF7 (robust z)")
            ax.plot(relx, seg["AF8_plot"], color="tab:orange", linewidth=0.9, alpha=0.9, label="AF8 (robust z)")
            rem_start_rel = rem_start_x - win_start_x
            rem_end_rel = rem_end_x - win_start_x
            ax.axvspan(rem_start_rel, rem_end_rel, alpha=0.12, color="tab:green", label="REM window")
            ax.axvline(rem_start_rel, color="tab:red", linestyle="--", linewidth=0.8, alpha=0.7, label="REM start")

            first_disruptive = True
            first_induction = True
            for _, c in cues_ep.iterrows():
                cue_sod = c.get("event_sod")
                if pd.isna(cue_sod):
                    continue
                cue_abs = sec_of_day_to_elapsed(float(cue_sod), session_start_sod)
                cue_x = cue_abs - win_start_x
                if cue_abs < win_start_x or cue_abs > win_end_x:
                    continue
                ct = str(c.get("cue_type", "cue"))
                col = cue_color.get(ct, "tab:purple")
                tr_idx = c.get("train_index")
                tr_suffix = ""
                if not pd.isna(tr_idx):
                    tr_suffix = f" (train {int(tr_idx)})"
                cue_label = None
                if ct == "disruptive" and first_disruptive:
                    cue_label = "Disruptive cue" + tr_suffix
                    first_disruptive = False
                elif ct == "induction" and first_induction:
                    cue_label = "Induction cue" + tr_suffix
                    first_induction = False
                ax.axvline(cue_x, color=col, linestyle=":", linewidth=0.9, alpha=0.85, label=cue_label)
                ax.scatter([cue_x], [2.8], s=12, color=col, zorder=4)

            dur = max(1, int(round(win_end_x - win_start_x)))
            step = 30 if dur <= 240 else 60
            ticks = np.arange(0, dur + step, step)
            ax.set_xticks(ticks)
            ax.set_xticklabels(
                [seconds_to_hms_wrapped(session_start_sod + win_start_x + t) for t in ticks],
                fontsize=8,
                rotation=25,
                ha="right",
            )
            ax.set_title(f"REM #{ep_idx} | {ep.get('episode_start_boston','')} -> {ep.get('episode_end_boston','')}", fontsize=9)
            ax.set_xlabel("Clock time (Boston)")
            ax.set_ylabel("Normalized EEG (robust z)")
            ax.grid(True, alpha=0.25)
            ax.legend(loc="upper right", fontsize=7)
        for j in range(len(rem_plot_df), len(axes)):
            axes[j].axis("off")
        fig.suptitle(f"EEG per REM phase | pid={args.pid} | session_start={session_start_hms} | plotted={plotted_count}/{len(rem_plot_df)}", fontsize=11)
        fig.tight_layout(rect=[0, 0, 1, 0.97])

    fig.savefig(output_png, dpi=120)
    print(f"Saved plot to {output_png}")
    print(f"REM rows considered: {len(rem_df)}")
    if args.plot_mode == "overview":
        print(f"Session points plotted: {len(agg)}")
    else:
        print(f"REM panels requested: {len(rem_df)}")


if __name__ == "__main__":
    main()
