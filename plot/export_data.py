#!/usr/bin/env python3
# extract_lucidreams_data.py

import csv
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, firestore


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_PATH = os.path.join(os.path.dirname(BASE_DIR), "lucidreans-firebase-adminsdk-fbsvc-4b27ed98c4.json")
OUT_REM_CSV = os.path.join(BASE_DIR, "rem_episodes.csv")
OUT_CUES_CSV = os.path.join(BASE_DIR, "cue_events.csv")
OUT_CUTOFF_CSV = os.path.join(BASE_DIR, "motion_per_second_series.csv")
OUT_SESSIONS_CSV = os.path.join(BASE_DIR, "sessions_overview.csv")
BOSTON_TZ = ZoneInfo("America/New_York")


def parse_iso(s):
    if not s:
        return None
    # handle trailing Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def iso_plus_seconds(iso_string, seconds):
    if iso_string is None or seconds is None:
        return None
    base = parse_iso(iso_string)
    if base is None:
        return None
    return (base + timedelta(seconds=float(seconds))).isoformat()


def to_boston_iso(iso_string):
    dt = parse_iso(iso_string)
    if dt is None:
        return None
    return dt.astimezone(BOSTON_TZ).isoformat()


def to_boston_time(iso_string):
    dt = parse_iso(iso_string)
    if dt is None:
        return None
    return dt.astimezone(BOSTON_TZ).strftime("%H:%M:%S")


def epoch_to_boston_time(night_start_iso, epoch_sec, fallback_iso=None):
    """Canonical clock time using session start + epoch seconds."""
    if night_start_iso is not None and epoch_sec is not None:
        ts = iso_plus_seconds(night_start_iso, epoch_sec)
        if ts is not None:
            return to_boston_time(ts)
    return to_boston_time(fallback_iso)


def main():
    cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    rem_rows = []
    cue_rows = []
    motion_rows = []
    session_rows = []

    participant_ids = set()
    session_count = 0
    # Use collection-group query to include sessions even when parent sleep_studies/{pid}
    # doc is missing (Firestore allows subcollections without parent doc body).
    sessions = db.collection_group("sessions").stream()

    for sdoc in sessions:
        session_count += 1
        data = sdoc.to_dict() or {}
        general = data.get("general", {}) or {}
        rem_periods = data.get("rem_periods", []) or []
        rem_dynamic_thresholds = data.get("rem_dynamic_thresholds", {}) or {}
        # Path shape: sleep_studies/{pid}/sessions/{session_doc_id}
        path_parts = sdoc.reference.path.split("/")
        pid = None
        if len(path_parts) >= 4 and path_parts[-2] == "sessions":
            pid = path_parts[-3]
        if not pid:
            pid = data.get("participant_id")
        if not pid:
            pid = "unknown_pid"
        participant_ids.add(pid)

        # New schema stores per-second motion amount at top level.
        # Keep backward compatibility with older nested schemas.
        motion_series = data.get("motion_per_second_series", None)
        if not motion_series:
            motion_series = rem_dynamic_thresholds.get("motion_per_second_series", None)
        if not motion_series:
            motion_series = rem_dynamic_thresholds.get("motion_80pct_cutoff_series", []) or []

        night_start_iso = general.get("device_time_start")
        session_end_iso = general.get("device_time_end")
        total_trains = 0
        total_induction_cues = 0

        for sec_idx, motion_value in enumerate(motion_series):
            point_iso = iso_plus_seconds(night_start_iso, sec_idx)
            motion_rows.append({
                "pid": pid,
                "session_start_boston": to_boston_time(night_start_iso),
                "session_end_boston": to_boston_time(session_end_iso),
                "second_index": sec_idx,
                "epoch_sec": sec_idx,
                "time_boston": to_boston_time(point_iso),
                "motion_per_second": motion_value,
            })

        for ep_idx, ep in enumerate(rem_periods):
            ep_start_epoch = ep.get("start_epoch_sec")
            ep_dur = ep.get("duration_sec")
            # Canonical episode wall-clock from session start + epoch.
            ep_start_boston = epoch_to_boston_time(night_start_iso, ep_start_epoch, ep.get("device_time_start"))
            ep_end_boston = None
            if ep_start_epoch is not None and ep_dur is not None:
                ep_end_boston = epoch_to_boston_time(night_start_iso, ep_start_epoch + ep_dur)

            rem_rows.append({
                "pid": pid,
                "session_start_boston": to_boston_time(night_start_iso),
                "session_end_boston": to_boston_time(session_end_iso),
                "episode_index": ep_idx,
                "episode_start_epoch_sec": ep_start_epoch,
                "episode_duration_sec": ep_dur,
                "episode_start_boston": ep_start_boston,
                "episode_end_boston": ep_end_boston,
                "episode_motion_avg": ep.get("motion_avg"),
            })

            trains = ep.get("trains", []) or []
            total_trains += len(trains)
            for tr_idx, tr in enumerate(trains):
                disruptive = tr.get("disruptive", {}) or {}
                induction = tr.get("induction", {}) or {}
                cues = induction.get("cues", []) or []
                total_induction_cues += len(cues)

                # disruptive cue/event (has absolute time in your schema)
                cue_rows.append({
                    "pid": pid,
                    "episode_index": ep_idx,
                    "train_index": tr_idx,
                    "cue_type": "disruptive",
                    "took_place": disruptive.get("took_place"),
                    "epoch_sec": disruptive.get("start_epoch_sec"),
                        "event_time_boston": epoch_to_boston_time(
                            night_start_iso,
                            disruptive.get("start_epoch_sec"),
                            disruptive.get("device_time_start"),
                        ),
                    "volume": None,
                    "arousal_detected": None,
                })

                # induction cues (epoch-based; absolute time reconstructed)
                for cue_idx, cue in enumerate(cues):
                    cue_epoch = cue.get("epoch_sec")
                    cue_time_iso = iso_plus_seconds(night_start_iso, cue_epoch)

                    cue_rows.append({
                        "pid": pid,
                        "episode_index": ep_idx,
                        "train_index": tr_idx,
                        "cue_index": cue_idx,
                        "cue_type": "induction",
                        "took_place": cue.get("took_place"),
                        "epoch_sec": cue_epoch,
                        "event_time_boston": epoch_to_boston_time(night_start_iso, cue_epoch, cue_time_iso),
                        "volume": cue.get("volume"),
                        "arousal_detected": cue.get("arousal_detected"),
                    })

        session_rows.append({
            "pid": pid,
            "session_doc_id": sdoc.id,
            "session_start_boston": to_boston_time(night_start_iso),
            "session_end_boston": to_boston_time(session_end_iso),
            "has_general": bool(general),
            "rem_episode_count": len(rem_periods),
            "train_count": total_trains,
            "induction_cue_count": total_induction_cues,
            "motion_points": len(motion_series),
        })

    # Write REM episodes
    with open(OUT_REM_CSV, "w", newline="", encoding="utf-8") as f:
        fields = [
            "pid",
            "session_start_boston",
            "session_end_boston",
            "episode_index", "episode_start_epoch_sec", "episode_duration_sec",
            "episode_start_boston",
            "episode_end_boston",
            "episode_motion_avg"
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rem_rows)

    # Write cue events
    with open(OUT_CUES_CSV, "w", newline="", encoding="utf-8") as f:
        fields = [
            "pid", "episode_index", "train_index", "cue_index",
            "cue_type", "took_place", "epoch_sec", "event_time_boston",
            "volume", "arousal_detected"
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(cue_rows)

    # Write per-second motion values
    with open(OUT_CUTOFF_CSV, "w", newline="", encoding="utf-8") as f:
        fields = [
            "pid",
            "session_start_boston",
            "session_end_boston",
            "second_index",
            "epoch_sec",
            "time_boston",
            "motion_per_second",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(motion_rows)

    # Write sessions overview (includes sessions with zero REM/cue/motion rows)
    with open(OUT_SESSIONS_CSV, "w", newline="", encoding="utf-8") as f:
        fields = [
            "pid",
            "session_doc_id",
            "session_start_boston",
            "session_end_boston",
            "has_general",
            "rem_episode_count",
            "train_count",
            "induction_cue_count",
            "motion_points",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(session_rows)

    print(f"Scanned participants: {len(participant_ids)}")
    print(f"Scanned sessions: {session_count}")
    print(f"Wrote {OUT_REM_CSV} ({len(rem_rows)} rows)")
    print(f"Wrote {OUT_CUES_CSV} ({len(cue_rows)} rows)")
    print(f"Wrote {OUT_CUTOFF_CSV} ({len(motion_rows)} rows)")
    print(f"Wrote {OUT_SESSIONS_CSV} ({len(session_rows)} rows)")


if __name__ == "__main__":
    main()