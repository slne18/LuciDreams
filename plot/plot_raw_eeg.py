#!/usr/bin/env python3
"""
Plot raw EEG (no processing) by session and REM phase.

Differences vs plot_eeg_rem.py:
- No low-pass / band-pass / notch filtering
- No rolling smoothing
- No 1-second aggregation (plots raw samples directly)
"""

import argparse
import math
import os
from datetime import datetime
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_NIGHT_DIR = os.path.join(BASE_DIR, "data_night")
DEFAULT_REM_CSV = os.path.join(DATA_NIGHT_DIR, "rem_episodes.csv")
DEFAULT_CUE_CSV = os.path.join(DATA_NIGHT_DIR, "cue_events.csv")
EEG_DATA_DIR = os.path.join(BASE_DIR, "EEG")
EEG_PLOTS_DIR = os.path.join(BASE_DIR, "eeg_plots")
DEFAULT_OUTPUT_TEMPLATE = os.path.join(EEG_PLOTS_DIR, "raw_eeg_rem_phases_{pid}.png")
SECONDS_PER_DAY = 24 * 3600


def sec_of_day_to_elapsed(sec_of_day: float, ref_sec_of_day: float) -> float:
    if sec_of_day >= ref_sec_of_day:
        return sec_of_day - ref_sec_of_day
    return sec_of_day + SECONDS_PER_DAY - ref_sec_of_day


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


def choose_rem_session(
    rem_df: pd.DataFrame,
    pid: str,
    session_start_boston: Optional[str],
    night_number: Optional[int],
) -> pd.DataFrame:
    d = rem_df[rem_df["pid"].astype(str) == pid].copy()
    if d.empty:
        raise ValueError(f"No REM rows found for pid={pid}")
    has_night_col = "night_number" in d.columns
    if has_night_col:
        d["night_number"] = pd.to_numeric(d["night_number"], errors="coerce")
    if night_number is not None and has_night_col:
        d = d[d["night_number"] == int(night_number)].copy()
        if d.empty:
            raise ValueError(f"No REM rows found for pid={pid}, night_number={night_number}")
    if session_start_boston:
        d = d[d["session_start_boston"].astype(str) == session_start_boston].copy()
        if d.empty:
            raise ValueError(
                f"No REM rows found for pid={pid}, night_number={night_number}, session_start_boston={session_start_boston}"
            )
        return d
    if has_night_col:
        grp_n = d.groupby("night_number").size().sort_index()
        if len(grp_n) > 0:
            sel_night = int(grp_n.index.max())
            d = d[d["night_number"] == sel_night].copy()
    grp = d.groupby("session_start_boston").size().sort_values(ascending=False)
    sel = str(grp.index[0])
    return d[d["session_start_boston"].astype(str) == sel].copy()


def main() -> None:
    p = argparse.ArgumentParser(description="Plot completely raw RAW_AF7/RAW_AF8 for overview and per-REM windows.")
    p.add_argument("--pid", required=True, help="Participant ID")
    p.add_argument("--night-number", type=int, default=None, help="Night number (1-based within pid)")
    p.add_argument("--eeg-csv", default=None, help="Path to EEG csv. If omitted, tries standard EEG_<pid>.csv paths")
    p.add_argument("--eeg-pid", default=None, help="Optional EEG file PID stem if it differs from --pid")
    p.add_argument("--rem-csv", default=DEFAULT_REM_CSV)
    p.add_argument("--cue-csv", default=DEFAULT_CUE_CSV)
    p.add_argument("--session-start-boston", default=None, help="Optional session_start_boston (HH:MM:SS)")
    p.add_argument("--both-phases-only", action="store_true", help="Keep only REM episodes with both disruptive+induction cues")
    p.add_argument("--plot-mode", choices=["overview", "per-rem", "both"], default="both")
    p.add_argument("--max-points-overview", type=int, default=0, help="Optional decimation cap for overview only (0=no decimation)")
    p.add_argument("--max-points-per-rem", type=int, default=0, help="Optional decimation cap for per-REM panels only (0=no decimation)")
    p.add_argument("--per-rem-pre-sec", type=int, default=300, help="Seconds shown before REM start in per-rem mode")
    p.add_argument("--per-rem-post-sec", type=int, default=300, help="Seconds shown after REM end in per-rem mode")
    p.add_argument("--output", default=None)
    args = p.parse_args()

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
            overview_output_png = os.path.join(EEG_PLOTS_DIR, f"raw_eeg_overview_{args.pid}{night_suffix}.png")
            per_rem_output_png = os.path.join(EEG_PLOTS_DIR, f"raw_eeg_per_rem_{args.pid}{night_suffix}.png")
    elif args.plot_mode == "overview":
        overview_output_png = output_png
        per_rem_output_png = None
    else:
        overview_output_png = None
        per_rem_output_png = output_png

    for target in [x for x in [overview_output_png, per_rem_output_png] if x]:
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

    rem_df = pd.read_csv(args.rem_csv)
    cue_df = pd.read_csv(args.cue_csv)

    rem_df = choose_rem_session(rem_df, args.pid, args.session_start_boston, args.night_number)
    rem_df["episode_index"] = pd.to_numeric(rem_df["episode_index"], errors="coerce")
    rem_df = rem_df.dropna(subset=["episode_index"]).copy()
    rem_df["episode_index"] = rem_df["episode_index"].astype(int)
    rem_df = rem_df.sort_values("episode_index").reset_index(drop=True)
    if rem_df.empty:
        raise ValueError("No REM episodes to plot after filtering.")

    cue_df = cue_df[cue_df["pid"].astype(str) == args.pid].copy()
    if args.night_number is not None and "night_number" in cue_df.columns:
        cue_df["night_number"] = pd.to_numeric(cue_df["night_number"], errors="coerce")
        cue_df = cue_df[cue_df["night_number"] == int(args.night_number)].copy()
    if "took_place" in cue_df.columns:
        cue_df = cue_df[cue_df["took_place"].astype(str).str.lower() == "true"].copy()
    cue_df["episode_index"] = pd.to_numeric(cue_df.get("episode_index"), errors="coerce")
    cue_df = cue_df.dropna(subset=["episode_index"]).copy()
    cue_df["episode_index"] = cue_df["episode_index"].astype(int)
    cue_df["train_index"] = pd.to_numeric(cue_df.get("train_index"), errors="coerce")
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

    eeg = pd.read_csv(
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

    session_start_hms = str(rem_df["session_start_boston"].iloc[0])
    session_end_hms = str(rem_df["session_end_boston"].iloc[0])
    session_start_sod = parse_hms_to_seconds(session_start_hms)
    session_end_sod = parse_hms_to_seconds(session_end_hms)
    if session_start_sod is None or session_end_sod is None:
        raise ValueError("Invalid session start/end times in rem_episodes.csv")

    if session_end_sod >= session_start_sod:
        in_session = (eeg["sec_of_day"] >= session_start_sod) & (eeg["sec_of_day"] <= session_end_sod)
    else:
        in_session = (eeg["sec_of_day"] >= session_start_sod) | (eeg["sec_of_day"] <= session_end_sod)
    sess = eeg[in_session].copy()
    if sess.empty:
        raise ValueError("No EEG samples found in selected session interval.")

    sess["elapsed_sec"] = np.where(
        sess["sec_of_day"] >= session_start_sod,
        sess["sec_of_day"] - session_start_sod,
        sess["sec_of_day"] + SECONDS_PER_DAY - session_start_sod,
    )

    cue_color = {"disruptive": "red", "induction": "green"}
    rem_episode_ids = set(rem_df["episode_index"].astype(int).tolist())
    cues_sess = cue_df[cue_df["episode_index"].isin(rem_episode_ids)].copy()

    if args.plot_mode in ("overview", "both"):
        overview = sess
        if args.max_points_overview and len(overview) > args.max_points_overview:
            stride = int(math.ceil(len(overview) / float(args.max_points_overview)))
            overview = overview.iloc[::stride].copy()

        fig, ax = plt.subplots(figsize=(18, 7))
        ax.plot(overview["elapsed_sec"], overview["RAW_AF7"], color="tab:blue", linewidth=0.7, alpha=0.9, label="AF7 (raw)")
        ax.plot(overview["elapsed_sec"], overview["RAW_AF8"], color="tab:orange", linewidth=0.7, alpha=0.9, label="AF8 (raw)")

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
            ax.axvspan(rem_start_x, rem_end_x, alpha=0.12, color="tab:orange", label="REM window" if first_rem_label else None)
            first_rem_label = False
            ax.axvline(rem_start_x, color="yellow", linestyle="--", linewidth=0.9, alpha=0.9, label="REM start" if first_rem_start_label else None)
            ax.axvline(rem_end_x, color="black", linestyle="--", linewidth=0.9, alpha=0.9, label="REM end" if first_rem_end_label else None)
            first_rem_start_label = False
            first_rem_end_label = False

        for _, c in cues_sess.iterrows():
            cue_sod = c.get("event_sod")
            if pd.isna(cue_sod):
                continue
            cue_x = sec_of_day_to_elapsed(float(cue_sod), session_start_sod)
            ct = str(c.get("cue_type", "cue"))
            ax.axvline(cue_x, color=cue_color.get(ct, "tab:purple"), linestyle=":", linewidth=0.8, alpha=0.7)

        total_sec = int(max(sess["elapsed_sec"].max(), 1))
        tick_step = 30 * 60
        ticks = np.arange(0, max(total_sec + tick_step, tick_step), tick_step)
        ax.set_xticks(ticks)
        ax.set_xticklabels([format_elapsed_hms(t)[:5] for t in ticks], rotation=0)
        ax.set_title(f"Raw EEG session overview with REM windows | pid={args.pid} | night={args.night_number} | start={session_start_hms}")
        ax.set_xlabel("Elapsed from session start (HH:MM)")
        ax.set_ylabel("EEG raw amplitude")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="upper right", fontsize=8)
        fig.tight_layout()
        fig.savefig(overview_output_png, dpi=120)
        print(f"Saved plot to {overview_output_png}")
        print(f"REM rows considered: {len(rem_df)}")
        print(f"Session raw points plotted: {len(overview)}")
        plt.close(fig)

    if args.plot_mode in ("per-rem", "both"):
        rem_plot_df = rem_df.copy()
        n = len(rem_plot_df)
        if n == 0:
            print(f"No REM rows to plot for per-rem mode (pid={args.pid}, night={args.night_number}).")
        else:
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
                win_end_x = min(float(sess["elapsed_sec"].max()), base_end + max(0, args.per_rem_post_sec))
                seg = sess[(sess["elapsed_sec"] >= win_start_x) & (sess["elapsed_sec"] <= win_end_x)].copy()
                if seg.empty:
                    ax.text(0.5, 0.5, f"REM #{ep_idx}: no EEG data", transform=ax.transAxes, ha="center", va="center")
                    ax.axis("off")
                    continue

                if args.max_points_per_rem and len(seg) > args.max_points_per_rem:
                    stride = int(math.ceil(len(seg) / float(args.max_points_per_rem)))
                    seg = seg.iloc[::stride].copy()

                plotted_count += 1
                relx = seg["elapsed_sec"] - win_start_x
                ax.plot(relx, seg["RAW_AF7"], color="tab:blue", linewidth=0.7, alpha=0.9, label="AF7 (raw)")
                ax.plot(relx, seg["RAW_AF8"], color="tab:orange", linewidth=0.7, alpha=0.9, label="AF8 (raw)")

                rem_start_rel = rem_start_x - win_start_x
                rem_end_rel = rem_end_x - win_start_x
                ax.axvspan(rem_start_rel, rem_end_rel, alpha=0.12, color="tab:orange", label="REM window")
                ax.axvline(rem_start_rel, color="yellow", linestyle="--", linewidth=0.9, alpha=0.95, label="REM start")
                ax.axvline(rem_end_rel, color="black", linestyle="--", linewidth=0.9, alpha=0.95, label="REM end")

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

                dur = max(1, int(round(win_end_x - win_start_x)))
                step = 30
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
                ax.set_ylabel("EEG raw amplitude")
                ax.grid(True, alpha=0.25)
                ax.legend(loc="upper right", fontsize=7)

            for j in range(len(rem_plot_df), len(axes)):
                axes[j].axis("off")
            fig.suptitle(
                f"Raw EEG per REM phase | pid={args.pid} | night={args.night_number} | session_start={session_start_hms} | plotted={plotted_count}/{len(rem_plot_df)}",
                fontsize=11,
            )
            fig.tight_layout(rect=[0, 0, 1, 0.97])
            fig.savefig(per_rem_output_png, dpi=120)
            print(f"Saved plot to {per_rem_output_png}")
            print(f"REM rows considered: {len(rem_df)}")
            print(f"REM panels requested: {len(rem_df)}")
            plt.close(fig)


if __name__ == "__main__":
    main()

