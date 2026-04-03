#!/usr/bin/env python3
# extract_lucidreams_data.py

import csv
import json
import os
import argparse
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


def load_local_session_records(local_file):
    with open(local_file, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Some console exports are a single quoted blob with raw newlines and escaped quotes.
        # Example shape: "<newline>[ ... \"key\": ... ]<newline>"
        if raw.startswith('"') and raw.endswith('"'):
            inner = raw[1:-1].replace('\\"', '"')
            parsed = json.loads(inner)
        else:
            raise
    if isinstance(parsed, str):
        parsed = json.loads(parsed)

    def get_motion_len(doc):
        ms = doc.get("motion_per_second_series")
        if isinstance(ms, list):
            return len(ms)
        ms_json = doc.get("motion_per_second_series_json")
        if isinstance(ms_json, str) and ms_json.strip():
            try:
                parsed = json.loads(ms_json)
                if isinstance(parsed, list):
                    return len(parsed)
            except Exception:
                pass
        return 0

    def get_rem_len(doc):
        rp = doc.get("rem_periods")
        if isinstance(rp, list):
            return len(rp)
        rp_json = doc.get("rem_periods_json")
        if isinstance(rp_json, str) and rp_json.strip():
            try:
                parsed = json.loads(rp_json)
                if isinstance(parsed, list):
                    return len(parsed)
            except Exception:
                pass
        return 0

    def session_fingerprint(doc):
        general = doc.get("general", {}) or {}
        start_iso = str(general.get("device_time_start") or "")
        return (
            str(doc.get("participant_id") or ""),
            start_iso,
            str(general.get("condition") if general.get("condition") is not None else ""),
            str(general.get("epochs_count") if general.get("epochs_count") is not None else ""),
            str(get_motion_len(doc)),
            str(get_rem_len(doc)),
        )

    records = []
    counter = 0
    seen_fingerprints = set()
    if isinstance(parsed, list):
        for item in parsed:
            docs = []
            if isinstance(item, dict) and isinstance(item.get("docs"), list):
                docs = item.get("docs") or []
            elif isinstance(item, dict) and item.get("participant_id"):
                docs = [item]
            for d in docs:
                if not isinstance(d, dict):
                    continue
                fp = session_fingerprint(d)
                if fp in seen_fingerprints:
                    continue
                seen_fingerprints.add(fp)
                pid = d.get("participant_id") or "unknown_pid"
                sid = f"local_{counter}"
                spath = f"sleep_studies/{pid}/sessions/{sid}"
                records.append((sid, spath, d))
                counter += 1
    return records


def main():
    parser = argparse.ArgumentParser(description="Export LuciDreams session data to plot CSVs.")
    parser.add_argument("--local-file", default=None, help="Optional local JSON export file (e.g., output.csv).")
    args = parser.parse_args()

    rem_rows = []
    cue_rows = []
    motion_rows = []
    session_rows = []

    participant_ids = set()
    session_count = 0
    if args.local_file:
        sessions = load_local_session_records(args.local_file)
    else:
        cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        # Use collection-group query to include sessions even when parent sleep_studies/{pid}
        # doc is missing (Firestore allows subcollections without parent doc body).
        sessions = []
        for sdoc in db.collection_group("sessions").stream():
            sessions.append((sdoc.id, sdoc.reference.path, sdoc.to_dict() or {}))

    for session_doc_id, session_path, data in sessions:
        session_count += 1
        general = data.get("general", {}) or {}
        rem_periods = data.get("rem_periods", []) or []
        if not rem_periods:
            rem_json = data.get("rem_periods_json", None)
            if isinstance(rem_json, str) and rem_json.strip():
                try:
                    parsed_rem = json.loads(rem_json)
                    if isinstance(parsed_rem, list):
                        rem_periods = parsed_rem
                except Exception:
                    rem_periods = []
        rem_dynamic_thresholds = data.get("rem_dynamic_thresholds", {}) or {}
        # Path shape: sleep_studies/{pid}/sessions/{session_doc_id}
        path_parts = session_path.split("/")
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
        if motion_series is None:
            motion_json = data.get("motion_per_second_series_json", None)
            if isinstance(motion_json, str) and motion_json.strip():
                try:
                    parsed = json.loads(motion_json)
                    if isinstance(parsed, list):
                        motion_series = parsed
                except Exception:
                    motion_series = None
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
            "session_doc_id": session_doc_id,
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