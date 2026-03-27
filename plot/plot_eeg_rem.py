#!/usr/bin/env python3
"""
Plot EEG by REM phase (similar layout to rem_trigger_dynamics).

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

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REM_CSV = os.path.join(BASE_DIR, "rem_episodes.csv")
DEFAULT_CUE_CSV = os.path.join(BASE_DIR, "cue_events.csv")
DEFAULT_OUTPUT_TEMPLATE = os.path.join(BASE_DIR, "eeg_rem_phases_{pid}.png")


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
    p.add_argument("--output", default=None)
    args = p.parse_args()
    output_png = args.output or DEFAULT_OUTPUT_TEMPLATE.format(pid=args.pid)

    eeg_csv = args.eeg_csv or os.path.join(BASE_DIR, f"EEG_{args.pid}.csv")
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

    n = len(rem_df)
    ncols = 2 if n > 1 else 1
    nrows = int(math.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(16, max(4.0 * nrows, 5)))
    try:
        axes = axes.flatten()
    except Exception:
        axes = [axes]

    cue_color = {"disruptive": "tab:red", "induction": "tab:green"}
    plotted_count = 0

    for i, (_, ep) in enumerate(rem_df.iterrows()):
        ax = axes[i]
        ep_idx = int(ep["episode_index"])
        start_hms = str(ep.get("episode_start_boston", ""))
        end_hms = str(ep.get("episode_end_boston", ""))
        start_sod = parse_hms_to_seconds(start_hms)
        end_sod = parse_hms_to_seconds(end_hms)
        if start_sod is None or end_sod is None:
            ax.text(0.5, 0.5, f"REM #{ep_idx}: invalid start/end time", transform=ax.transAxes, ha="center", va="center")
            ax.axis("off")
            continue

        # Find best date with most EEG points in this REM interval.
        per_date_counts: List[Tuple[object, int]] = []
        for d, g in eeg.groupby("date_only"):
            if end_sod >= start_sod:
                mask = (g["sec_of_day"] >= start_sod) & (g["sec_of_day"] <= end_sod)
            else:
                mask = (g["sec_of_day"] >= start_sod) | (g["sec_of_day"] <= end_sod)
            per_date_counts.append((d, int(mask.sum())))
        best_date, _ = max(per_date_counts, key=lambda x: x[1])

        g = eeg[eeg["date_only"] == best_date].copy()
        if end_sod >= start_sod:
            mask = (g["sec_of_day"] >= start_sod) & (g["sec_of_day"] <= end_sod)
        else:
            mask = (g["sec_of_day"] >= start_sod) | (g["sec_of_day"] <= end_sod)
        seg = g[mask].copy()

        if seg.empty:
            ax.text(0.5, 0.5, f"REM #{ep_idx}: no EEG samples in interval", transform=ax.transAxes, ha="center", va="center")
            ax.axis("off")
            continue

        plotted_count += 1
        ax.plot(seg["TimeStamp"], seg["RAW_AF7"], color="tab:blue", linewidth=0.8, label="RAW_AF7")
        ax.plot(seg["TimeStamp"], seg["RAW_AF8"], color="tab:orange", linewidth=0.8, label="RAW_AF8")
        ax.axvspan(seg["TimeStamp"].iloc[0], seg["TimeStamp"].iloc[-1], alpha=0.08, color="tab:green")

        # Mark REM detection (episode start)
        start_dt = pd.Timestamp(datetime.combine(best_date, datetime.strptime(start_hms, "%H:%M:%S").time()))
        ax.axvline(start_dt, color="tab:red", linestyle="--", linewidth=1.0, alpha=0.8)
        ax.annotate("REM detected", xy=(start_dt, seg["RAW_AF7"].iloc[0]), xytext=(8, 8), textcoords="offset points", fontsize=7)

        # Cue markers + labels
        cues_ep = cue_df[cue_df["episode_index"] == ep_idx].copy().sort_values("epoch_sec")
        lane = 0
        for _, c in cues_ep.iterrows():
            cue_hms = str(c.get("event_time_boston", ""))[:8]
            if parse_hms_to_seconds(cue_hms) is None:
                continue
            cue_dt = pd.Timestamp(datetime.combine(best_date, datetime.strptime(cue_hms, "%H:%M:%S").time()))
            ct = str(c.get("cue_type", "cue"))
            col = cue_color.get(ct, "tab:purple")
            ax.axvline(cue_dt, color=col, linestyle=":", linewidth=1.0, alpha=0.9)
            vol = c.get("volume", "")
            vol_txt = "na" if pd.isna(vol) or str(vol) == "" else str(vol)
            ar = c.get("arousal_detected", "")
            ar_txt = "na" if pd.isna(ar) or str(ar) == "" else str(ar)
            label = f"{ct} t={cue_hms}"
            if ct == "induction":
                label += f" | vol={vol_txt} | ar={ar_txt}"
            y_anchor = seg["RAW_AF7"].min() + (lane % 4) * (seg["RAW_AF7"].max() - seg["RAW_AF7"].min()) * 0.12
            lane += 1
            ax.annotate(
                label,
                xy=(cue_dt, y_anchor),
                xytext=(6, 4),
                textcoords="offset points",
                fontsize=6.5,
                ha="left",
                va="bottom",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec=col, alpha=0.8),
            )

        ax.set_title(f"REM #{ep_idx} | {start_hms} -> {end_hms}", fontsize=9)
        ax.set_xlabel("Time (Boston)")
        ax.set_ylabel("EEG raw")
        ax.grid(True, alpha=0.25)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        ax.tick_params(axis="x", labelrotation=30, labelsize=8)
        ax.legend(loc="upper right", fontsize=7)

    for j in range(len(rem_df), len(axes)):
        axes[j].axis("off")

    sess = str(rem_df["session_start_boston"].iloc[0]) if not rem_df.empty else "n/a"
    fig.suptitle(
        f"EEG by REM phase | pid={args.pid} | session_start={sess} | plotted={plotted_count}/{len(rem_df)}",
        fontsize=11,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(output_png, dpi=160)
    print(f"Saved plot to {output_png}")
    print(f"REM rows considered: {len(rem_df)}")
    print(f"REM panels with EEG data: {plotted_count}")


if __name__ == "__main__":
    main()
