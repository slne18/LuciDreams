#!/usr/bin/env python3
# extract_lucidreams_data.py

import csv
import json
import os
import argparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo
import firebase_admin
from firebase_admin import credentials, firestore


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FIREBASE_PROJECT_ID = "luciddreaming-33e97"
OUT_DIR = os.path.join(BASE_DIR, "data_night")
OUT_REM_CSV = os.path.join(OUT_DIR, "rem_episodes.csv")
OUT_CUES_CSV = os.path.join(OUT_DIR, "cue_events.csv")
OUT_CUTOFF_CSV = os.path.join(OUT_DIR, "motion_per_second_series.csv")
OUT_SMOOTHED_CSV = os.path.join(OUT_DIR, "motion_smoothed_series.csv")
OUT_SESSIONS_CSV = os.path.join(OUT_DIR, "sessions_overview.csv")
OUT_TRAINS_CSV = os.path.join(OUT_DIR, "train_events.csv")
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


def load_series_from_doc(data, plain_key, json_key):
    """Load list series from direct field or JSON-string companion field."""
    series = data.get(plain_key, None)
    if series is None:
        series_json = data.get(json_key, None)
        if isinstance(series_json, str) and series_json.strip():
            try:
                parsed = json.loads(series_json)
                if isinstance(parsed, list):
                    series = parsed
            except Exception:
                series = None
    if not isinstance(series, list):
        return []
    return series


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
    parser.add_argument("--pid", default=None, help="Optional participant filter (e.g., Soso).")
    parser.add_argument("--night-number", type=int, default=None, help="Optional night number filter (1-based within pid).")
    parser.add_argument(
        "--service-account",
        default=os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH"),
        help="Path to Firebase service-account JSON (defaults to FIREBASE_SERVICE_ACCOUNT_PATH env var).",
    )
    parser.add_argument(
        "--project-id",
        default=os.environ.get("FIREBASE_PROJECT_ID", DEFAULT_FIREBASE_PROJECT_ID),
        help=f"Firebase/GCP project ID (defaults to FIREBASE_PROJECT_ID env var, then {DEFAULT_FIREBASE_PROJECT_ID}).",
    )
    parser.add_argument(
        "--use-adc",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use Application Default Credentials from gcloud (default: true). Use --no-use-adc to force service-account JSON.",
    )
    args = parser.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    rem_rows = []
    cue_rows = []
    motion_rows = []
    smoothed_rows = []
    session_rows = []
    train_rows = []

    participant_ids = set()
    session_count = 0
    if args.local_file:
        sessions = load_local_session_records(args.local_file)
    else:
        app_options = {}
        if args.project_id:
            app_options["projectId"] = args.project_id
        if args.use_adc:
            firebase_admin.initialize_app(options=(app_options or None))
        else:
            if not args.service_account or not os.path.exists(args.service_account):
                raise FileNotFoundError(
                    f"Service account JSON not found: {args.service_account}. "
                    "Pass --service-account <path> or use --use-adc."
                )
            cred = credentials.Certificate(args.service_account)
            firebase_admin.initialize_app(cred, options=(app_options or None))
        db = firestore.client()
        # Use collection-group query to include sessions even when parent sleep_studies/{pid}
        # doc is missing (Firestore allows subcollections without parent doc body).
        sessions = []
        for sdoc in db.collection_group("sessions").stream():
            sessions.append((sdoc.id, sdoc.reference.path, sdoc.to_dict() or {}))

    prepared_sessions = []
    for session_doc_id, session_path, data in sessions:
        general = data.get("general", {}) or {}
        # Path shape: sleep_studies/{pid}/sessions/{session_doc_id}
        path_parts = session_path.split("/")
        pid = None
        if len(path_parts) >= 4 and path_parts[-2] == "sessions":
            pid = path_parts[-3]
        if not pid:
            pid = data.get("participant_id")
        if not pid:
            pid = "unknown_pid"
        start_iso = general.get("device_time_start")
        prepared_sessions.append({
            "session_doc_id": session_doc_id,
            "session_path": session_path,
            "data": data,
            "pid": pid,
            "start_iso": start_iso,
            "start_dt": parse_iso(start_iso),
        })

    sessions_by_pid = defaultdict(list)
    for item in prepared_sessions:
        sessions_by_pid[item["pid"]].append(item)

    for pid_key, plist in sessions_by_pid.items():
        plist.sort(key=lambda x: (x["start_dt"] or datetime.min.replace(tzinfo=timezone.utc), str(x["session_doc_id"])))
        for idx, item in enumerate(plist, start=1):
            item["night_number"] = idx

    for item in prepared_sessions:
        session_doc_id = item["session_doc_id"]
        data = item["data"]
        pid = item["pid"]
        night_number = item.get("night_number")
        if args.pid and str(pid) != str(args.pid):
            continue
        if args.night_number is not None and int(night_number or -1) != int(args.night_number):
            continue
        session_count += 1
        participant_ids.add(pid)
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

        # New schema stores per-second motion amount at top level.
        # Keep backward compatibility with older nested schemas.
        motion_series = load_series_from_doc(data, "motion_per_second_series", "motion_per_second_series_json")
        if not motion_series:
            motion_series = rem_dynamic_thresholds.get("motion_per_second_series", None)
        if not motion_series:
            motion_series = rem_dynamic_thresholds.get("motion_80pct_cutoff_series", []) or []

        motion_delta_only_series = load_series_from_doc(
            data, "motion_delta_only_series", "motion_delta_only_series_json"
        )
        motion_delta_plus_tilt15_series = load_series_from_doc(
            data, "motion_delta_plus_tilt15_series", "motion_delta_plus_tilt15_series_json"
        )
        sensor_events_per_second_series = load_series_from_doc(
            data, "sensor_events_per_second_series", "sensor_events_per_second_series_json"
        )
        sensor_event_dt_avg_ms_series = load_series_from_doc(
            data, "sensor_event_dt_avg_ms_series", "sensor_event_dt_avg_ms_series_json"
        )
        sensor_event_dt_max_ms_series = load_series_from_doc(
            data, "sensor_event_dt_max_ms_series", "sensor_event_dt_max_ms_series_json"
        )

        smoothed_series = data.get("smoothed_motion_series", None)
        if smoothed_series is None:
            smoothed_json = data.get("smoothed_motion_series_json", None)
            if isinstance(smoothed_json, str) and smoothed_json.strip():
                try:
                    parsed_s = json.loads(smoothed_json)
                    if isinstance(parsed_s, list):
                        smoothed_series = parsed_s
                except Exception:
                    smoothed_series = None
        if not smoothed_series:
            smoothed_series = rem_dynamic_thresholds.get("smoothed_motion_series", [])
        if smoothed_series is None:
            smoothed_series = []

        night_start_iso = general.get("device_time_start")
        session_end_iso = general.get("device_time_end")
        total_trains = 0
        total_induction_cues = 0

        max_motion_len = max(
            len(motion_series),
            len(motion_delta_only_series),
            len(motion_delta_plus_tilt15_series),
            len(sensor_events_per_second_series),
            len(sensor_event_dt_avg_ms_series),
            len(sensor_event_dt_max_ms_series),
        )
        for sec_idx in range(max_motion_len):
            point_iso = iso_plus_seconds(night_start_iso, sec_idx)
            motion_value = motion_series[sec_idx] if sec_idx < len(motion_series) else None
            motion_delta_only_value = (
                motion_delta_only_series[sec_idx] if sec_idx < len(motion_delta_only_series) else None
            )
            motion_delta_plus_tilt15_value = (
                motion_delta_plus_tilt15_series[sec_idx] if sec_idx < len(motion_delta_plus_tilt15_series) else None
            )
            sensor_events_per_second_value = (
                sensor_events_per_second_series[sec_idx] if sec_idx < len(sensor_events_per_second_series) else None
            )
            sensor_event_dt_avg_ms_value = (
                sensor_event_dt_avg_ms_series[sec_idx] if sec_idx < len(sensor_event_dt_avg_ms_series) else None
            )
            sensor_event_dt_max_ms_value = (
                sensor_event_dt_max_ms_series[sec_idx] if sec_idx < len(sensor_event_dt_max_ms_series) else None
            )
            motion_rows.append({
                "pid": pid,
                "night_number": night_number,
                "session_start_boston": to_boston_time(night_start_iso),
                "session_end_boston": to_boston_time(session_end_iso),
                "second_index": sec_idx,
                "epoch_sec": sec_idx,
                "time_boston": to_boston_time(point_iso),
                "motion_per_second": motion_value,
                "motion_delta_only": motion_delta_only_value,
                "motion_delta_plus_tilt15": motion_delta_plus_tilt15_value,
                "sensor_events_per_second": sensor_events_per_second_value,
                "sensor_event_dt_avg_ms": sensor_event_dt_avg_ms_value,
                "sensor_event_dt_max_ms": sensor_event_dt_max_ms_value,
            })

        for sec_idx, smoothed_value in enumerate(smoothed_series):
            point_iso = iso_plus_seconds(night_start_iso, sec_idx)
            smoothed_rows.append({
                "pid": pid,
                "night_number": night_number,
                "session_start_boston": to_boston_time(night_start_iso),
                "session_end_boston": to_boston_time(session_end_iso),
                "second_index": sec_idx,
                "epoch_sec": sec_idx,
                "time_boston": to_boston_time(point_iso),
                "motion_smoothed": smoothed_value,
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
                "night_number": night_number,
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

                # Train timeline export for runtime-faithful replay in notebooks.
                disruptive_start = disruptive.get("start_epoch_sec")
                first_induction_epoch = None
                if cues:
                    first_induction_epoch = cues[0].get("epoch_sec")
                train_end_epoch = tr.get("end_epoch_sec")
                train_rows.append({
                    "pid": pid,
                    "night_number": night_number,
                    "episode_index": ep_idx,
                    "train_index": tr_idx,
                    "disruptive_took_place": disruptive.get("took_place"),
                    "disruptive_start_epoch_sec": disruptive_start,
                    "first_induction_epoch_sec": first_induction_epoch,
                    "train_end_epoch_sec": train_end_epoch,
                    "train_duration_sec": tr.get("duration_sec"),
                    "induction_cues_count": len(cues),
                })

                # disruptive cue/event (has absolute time in your schema)
                cue_rows.append({
                    "pid": pid,
                    "night_number": night_number,
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

                # induction cues: prefer direct wall-clock timestamp captured at cue playback.
                # Fallback to epoch reconstruction for older records.
                for cue_idx, cue in enumerate(cues):
                    cue_epoch = cue.get("epoch_sec")
                    cue_time_iso = iso_plus_seconds(night_start_iso, cue_epoch)
                    cue_device_time = cue.get("device_time_start")

                    cue_rows.append({
                        "pid": pid,
                        "night_number": night_number,
                        "episode_index": ep_idx,
                        "train_index": tr_idx,
                        "cue_index": cue_idx,
                        "cue_type": "induction",
                        "took_place": cue.get("took_place"),
                        "epoch_sec": cue_epoch,
                        "event_time_boston": epoch_to_boston_time(night_start_iso, cue_epoch, cue_device_time or cue_time_iso),
                        "volume": cue.get("volume"),
                        "arousal_detected": cue.get("arousal_detected"),
                    })

        session_rows.append({
            "pid": pid,
            "night_number": night_number,
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
            "night_number",
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
            "pid", "night_number", "episode_index", "train_index", "cue_index",
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
            "night_number",
            "session_start_boston",
            "session_end_boston",
            "second_index",
            "epoch_sec",
            "time_boston",
            "motion_per_second",
            "motion_delta_only",
            "motion_delta_plus_tilt15",
            "sensor_events_per_second",
            "sensor_event_dt_avg_ms",
            "sensor_event_dt_max_ms",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(motion_rows)

    # Write smoothed per-second motion values
    with open(OUT_SMOOTHED_CSV, "w", newline="", encoding="utf-8") as f:
        fields = [
            "pid",
            "night_number",
            "session_start_boston",
            "session_end_boston",
            "second_index",
            "epoch_sec",
            "time_boston",
            "motion_smoothed",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(smoothed_rows)

    # Write sessions overview (includes sessions with zero REM/cue/motion rows)
    with open(OUT_SESSIONS_CSV, "w", newline="", encoding="utf-8") as f:
        fields = [
            "pid",
            "night_number",
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

    # Write train timeline rows
    with open(OUT_TRAINS_CSV, "w", newline="", encoding="utf-8") as f:
        fields = [
            "pid",
            "night_number",
            "episode_index",
            "train_index",
            "disruptive_took_place",
            "disruptive_start_epoch_sec",
            "first_induction_epoch_sec",
            "train_end_epoch_sec",
            "train_duration_sec",
            "induction_cues_count",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(train_rows)

    print(f"Scanned participants: {len(participant_ids)}")
    print(f"Scanned sessions: {session_count}")
    print(f"Wrote {OUT_REM_CSV} ({len(rem_rows)} rows)")
    print(f"Wrote {OUT_CUES_CSV} ({len(cue_rows)} rows)")
    print(f"Wrote {OUT_CUTOFF_CSV} ({len(motion_rows)} rows)")
    print(f"Wrote {OUT_SMOOTHED_CSV} ({len(smoothed_rows)} rows)")
    print(f"Wrote {OUT_SESSIONS_CSV} ({len(session_rows)} rows)")
    print(f"Wrote {OUT_TRAINS_CSV} ({len(train_rows)} rows)")


if __name__ == "__main__":
    main()