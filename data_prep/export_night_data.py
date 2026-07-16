#!/usr/bin/env python3
"""
Export per-participant-night summary and detail CSVs from Firebase (or local JSON).

Writes to data_prep/output/ by default:
  - night_summary.csv
  - cue_events.csv (condition + cue_type: induction/disruptive)
  - motion_per_second_series.csv
  - motion_smoothed_series.csv
  - rem_episodes.csv
  - train_events.csv (one row per disruptive→induction train)
"""

import argparse
import csv
import json
import os
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import firebase_admin
from firebase_admin import credentials, firestore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FIREBASE_PROJECT_ID = "luciddreaming-33e97"
BOSTON_TZ = ZoneInfo("America/New_York")

OUT_DIR = os.path.join(BASE_DIR, "output")
OUT_CSV = os.path.join(OUT_DIR, "night_summary.csv")
OUT_REM_CSV = os.path.join(OUT_DIR, "rem_episodes.csv")
OUT_CUES_CSV = os.path.join(OUT_DIR, "cue_events.csv")
OUT_MOTION_CSV = os.path.join(OUT_DIR, "motion_per_second_series.csv")
OUT_SMOOTHED_CSV = os.path.join(OUT_DIR, "motion_smoothed_series.csv")
OUT_TRAINS_CSV = os.path.join(OUT_DIR, "train_events.csv")


def parse_iso(s):
    if not s:
        return None
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


def to_boston_time(iso_string):
    dt = parse_iso(iso_string)
    if dt is None:
        return None
    return dt.astimezone(BOSTON_TZ).strftime("%H:%M:%S")


def epoch_to_boston_time(night_start_iso, epoch_sec, fallback_iso=None):
    if night_start_iso is not None and epoch_sec is not None:
        ts = iso_plus_seconds(night_start_iso, epoch_sec)
        if ts is not None:
            return to_boston_time(ts)
    return to_boston_time(fallback_iso)


def load_series_from_doc(data, plain_key, json_key):
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
                parsed_ms = json.loads(ms_json)
                if isinstance(parsed_ms, list):
                    return len(parsed_ms)
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
                parsed_rp = json.loads(rp_json)
                if isinstance(parsed_rp, list):
                    return len(parsed_rp)
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


def load_rem_periods(data):
    rem_periods = data.get("rem_periods", []) or []
    if rem_periods:
        return rem_periods
    rem_json = data.get("rem_periods_json")
    if isinstance(rem_json, str) and rem_json.strip():
        try:
            parsed = json.loads(rem_json)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []


def flatten_native_api_status(status):
    if isinstance(status, str):
        try:
            status = json.loads(status)
        except Exception:
            status = {}
    if not isinstance(status, dict):
        status = {}
    return {
        "native_api_is_native": status.get("is_native"),
        "native_api_torch": status.get("torch"),
        "native_api_haptics": status.get("haptics"),
        "native_api_native_audio": status.get("native_audio"),
        "native_api_status_json": json.dumps(status, sort_keys=True) if status else "",
    }


def summarize_arousals(rem_periods):
    induction_arousal_count = 0
    disruptive_arousal_count = 0
    disruptive_arousal_volumes = []

    for ep in rem_periods or []:
        for tr in ep.get("trains", []) or []:
            disruptive = tr.get("disruptive", {}) or {}
            params = disruptive.get("params", {}) or {}
            found = params.get("found_arousal_volume")
            if found is not None:
                disruptive_arousal_count += 1
                disruptive_arousal_volumes.append(found)

            induction = tr.get("induction", {}) or {}
            for cue in induction.get("cues", []) or []:
                if cue.get("arousal_detected"):
                    induction_arousal_count += 1

    disruptive_arousal_volume = disruptive_arousal_volumes[-1] if disruptive_arousal_volumes else None

    return {
        "induction_arousal_count": induction_arousal_count,
        "disruptive_arousal_count": disruptive_arousal_count,
        "induction_arousal_any": induction_arousal_count > 0,
        "disruptive_arousal_any": disruptive_arousal_count > 0,
        "disruptive_arousal_volume": disruptive_arousal_volume,
    }


def load_sessions(args):
    if args.local_file:
        return load_local_session_records(args.local_file)

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
    sessions = []
    for sdoc in db.collection_group("sessions").stream():
        sessions.append((sdoc.id, sdoc.reference.path, sdoc.to_dict() or {}))
    return sessions


def assign_night_numbers(prepared_sessions):
    sessions_by_pid = defaultdict(list)
    for item in prepared_sessions:
        sessions_by_pid[item["pid"]].append(item)

    for plist in sessions_by_pid.values():
        plist.sort(
            key=lambda x: (
                x["start_dt"] or datetime.min.replace(tzinfo=timezone.utc),
                str(x["session_doc_id"]),
            )
        )
        for idx, item in enumerate(plist, start=1):
            item["night_number"] = idx


def build_night_summary_row(item):
    data = item["data"]
    general = data.get("general", {}) or {}
    rem_periods = load_rem_periods(data)
    arousal = summarize_arousals(rem_periods)
    api = flatten_native_api_status(general.get("native_api_status"))

    return {
        "pid": item["pid"],
        "night_number": item.get("night_number"),
        "session_doc_id": item["session_doc_id"],
        "condition": general.get("condition"),
        "device_time_start": general.get("device_time_start"),
        "device_time_end": general.get("device_time_end"),
        "rem_minutes": general.get("rem_minutes"),
        "rem_motion_avg": general.get("rem_motion_avg"),
        "induction_arousal_volume": general.get("arousal_threshold"),
        "induction_arousal_count": arousal["induction_arousal_count"],
        "induction_highest_volume": general.get("induction_highest_volume"),
        "total_trains_delivered": general.get("total_trains_delivered"),
        **api,
        "disruptive_arousal_count": arousal["disruptive_arousal_count"],
        "induction_arousal_any": arousal["induction_arousal_any"],
        "disruptive_arousal_any": arousal["disruptive_arousal_any"],
        "disruptive_arousal_volume": arousal["disruptive_arousal_volume"],
    }


def extract_session_detail_rows(item):
    """Extract rem/cue/motion rows for one session."""
    data = item["data"]
    general = data.get("general", {}) or {}
    pid = item["pid"]
    night_number = item.get("night_number")
    condition = general.get("condition")
    rem_periods = load_rem_periods(data)
    rem_dynamic_thresholds = data.get("rem_dynamic_thresholds", {}) or {}

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

    rem_rows = []
    cue_rows = []
    train_rows = []
    motion_rows = []
    smoothed_rows = []

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
        motion_rows.append({
            "pid": pid,
            "night_number": night_number,
            "condition": condition,
            "session_start_boston": to_boston_time(night_start_iso),
            "session_end_boston": to_boston_time(session_end_iso),
            "second_index": sec_idx,
            "epoch_sec": sec_idx,
            "time_boston": to_boston_time(point_iso),
            "motion_per_second": motion_series[sec_idx] if sec_idx < len(motion_series) else None,
            "motion_delta_only": motion_delta_only_series[sec_idx] if sec_idx < len(motion_delta_only_series) else None,
            "motion_delta_plus_tilt15": (
                motion_delta_plus_tilt15_series[sec_idx] if sec_idx < len(motion_delta_plus_tilt15_series) else None
            ),
            "sensor_events_per_second": (
                sensor_events_per_second_series[sec_idx] if sec_idx < len(sensor_events_per_second_series) else None
            ),
            "sensor_event_dt_avg_ms": (
                sensor_event_dt_avg_ms_series[sec_idx] if sec_idx < len(sensor_event_dt_avg_ms_series) else None
            ),
            "sensor_event_dt_max_ms": (
                sensor_event_dt_max_ms_series[sec_idx] if sec_idx < len(sensor_event_dt_max_ms_series) else None
            ),
        })

    for sec_idx, smoothed_value in enumerate(smoothed_series):
        point_iso = iso_plus_seconds(night_start_iso, sec_idx)
        smoothed_rows.append({
            "pid": pid,
            "night_number": night_number,
            "condition": condition,
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
        ep_start_boston = epoch_to_boston_time(night_start_iso, ep_start_epoch, ep.get("device_time_start"))
        ep_end_boston = None
        if ep_start_epoch is not None and ep_dur is not None:
            ep_end_boston = epoch_to_boston_time(night_start_iso, ep_start_epoch + ep_dur)

        rem_rows.append({
            "pid": pid,
            "night_number": night_number,
            "condition": condition,
            "session_start_boston": to_boston_time(night_start_iso),
            "session_end_boston": to_boston_time(session_end_iso),
            "episode_index": ep_idx,
            "episode_start_epoch_sec": ep_start_epoch,
            "episode_duration_sec": ep_dur,
            "episode_start_boston": ep_start_boston,
            "episode_end_boston": ep_end_boston,
            "episode_motion_avg": ep.get("motion_avg"),
        })

        for tr_idx, tr in enumerate(ep.get("trains", []) or []):
            disruptive = tr.get("disruptive", {}) or {}
            induction = tr.get("induction", {}) or {}
            cues = induction.get("cues", []) or []
            first_induction_epoch = cues[0].get("epoch_sec") if cues else None

            train_rows.append({
                "pid": pid,
                "night_number": night_number,
                "condition": condition,
                "episode_index": ep_idx,
                "train_index": tr_idx,
                "disruptive_took_place": disruptive.get("took_place"),
                "disruptive_start_epoch_sec": disruptive.get("start_epoch_sec"),
                "first_induction_epoch_sec": first_induction_epoch,
                "train_end_epoch_sec": tr.get("end_epoch_sec"),
                "train_duration_sec": tr.get("duration_sec"),
                "induction_cues_count": len(cues),
            })

            cue_rows.append({
                "pid": pid,
                "night_number": night_number,
                "condition": condition,
                "episode_index": ep_idx,
                "train_index": tr_idx,
                "cue_index": None,
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

            for cue_idx, cue in enumerate(cues):
                cue_epoch = cue.get("epoch_sec")
                cue_time_iso = iso_plus_seconds(night_start_iso, cue_epoch)
                cue_device_time = cue.get("device_time_start")
                cue_rows.append({
                    "pid": pid,
                    "night_number": night_number,
                    "condition": condition,
                    "episode_index": ep_idx,
                    "train_index": tr_idx,
                    "cue_index": cue_idx,
                    "cue_type": "induction",
                    "took_place": cue.get("took_place"),
                    "epoch_sec": cue_epoch,
                    "event_time_boston": epoch_to_boston_time(
                        night_start_iso, cue_epoch, cue_device_time or cue_time_iso
                    ),
                    "volume": cue.get("volume"),
                    "arousal_detected": cue.get("arousal_detected"),
                })

    return rem_rows, cue_rows, train_rows, motion_rows, smoothed_rows


def write_csv(path, fieldnames, rows):
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Write via /tmp first: overwriting large Desktop/iCloud files can hit Errno 60.
    fd, tmp_path = tempfile.mkstemp(prefix="luci_export_", suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        shutil.move(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Export per-participant-night summary rows from LuciDreams sessions."
    )
    parser.add_argument("--local-file", default=None, help="Optional local JSON export file.")
    parser.add_argument("--pid", default=None, help="Optional participant filter.")
    parser.add_argument("--night-number", type=int, default=None, help="Optional night number filter (1-based).")
    parser.add_argument(
        "--service-account",
        default=os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH"),
        help="Path to Firebase service-account JSON.",
    )
    parser.add_argument(
        "--project-id",
        default=os.environ.get("FIREBASE_PROJECT_ID", DEFAULT_FIREBASE_PROJECT_ID),
        help=f"Firebase/GCP project ID (default: {DEFAULT_FIREBASE_PROJECT_ID}).",
    )
    parser.add_argument(
        "--use-adc",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use Application Default Credentials from gcloud (default: true).",
    )
    parser.add_argument(
        "--output-dir",
        default=OUT_DIR,
        help=f"Output directory for all CSV exports (default: {OUT_DIR}).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=f"Night summary CSV path (default: <output-dir>/night_summary.csv).",
    )
    args = parser.parse_args()

    sessions = load_sessions(args)
    prepared_sessions = []
    for session_doc_id, session_path, data in sessions:
        general = data.get("general", {}) or {}
        path_parts = session_path.split("/")
        pid = None
        if len(path_parts) >= 4 and path_parts[-2] == "sessions":
            pid = path_parts[-3]
        if not pid:
            pid = data.get("participant_id")
        if not pid:
            pid = "unknown_pid"
        start_iso = general.get("device_time_start")
        prepared_sessions.append(
            {
                "session_doc_id": session_doc_id,
                "session_path": session_path,
                "data": data,
                "pid": pid,
                "start_iso": start_iso,
                "start_dt": parse_iso(start_iso),
            }
        )

    assign_night_numbers(prepared_sessions)

    output_dir = os.path.abspath(args.output_dir)
    summary_path = args.output or os.path.join(output_dir, "night_summary.csv")

    summary_rows = []
    rem_rows = []
    cue_rows = []
    train_rows = []
    motion_rows = []
    smoothed_rows = []

    for item in prepared_sessions:
        if args.pid and str(item["pid"]) != str(args.pid):
            continue
        if args.night_number is not None and int(item.get("night_number") or -1) != int(args.night_number):
            continue
        summary_rows.append(build_night_summary_row(item))
        s_rem, s_cues, s_trains, s_motion, s_smooth = extract_session_detail_rows(item)
        rem_rows.extend(s_rem)
        cue_rows.extend(s_cues)
        train_rows.extend(s_trains)
        motion_rows.extend(s_motion)
        smoothed_rows.extend(s_smooth)

    summary_rows.sort(key=lambda r: (str(r["pid"]), int(r["night_number"] or 0)))

    write_csv(
        summary_path,
        [
            "pid",
            "night_number",
            "session_doc_id",
            "condition",
            "device_time_start",
            "device_time_end",
            "rem_minutes",
            "rem_motion_avg",
            "induction_arousal_volume",
            "induction_arousal_count",
            "induction_highest_volume",
            "total_trains_delivered",
            "native_api_is_native",
            "native_api_torch",
            "native_api_haptics",
            "native_api_native_audio",
            "native_api_status_json",
            "disruptive_arousal_count",
            "induction_arousal_any",
            "disruptive_arousal_any",
            "disruptive_arousal_volume",
        ],
        summary_rows,
    )

    rem_path = os.path.join(output_dir, "rem_episodes.csv")
    cues_path = os.path.join(output_dir, "cue_events.csv")
    trains_path = os.path.join(output_dir, "train_events.csv")
    motion_path = os.path.join(output_dir, "motion_per_second_series.csv")
    smoothed_path = os.path.join(output_dir, "motion_smoothed_series.csv")

    write_csv(
        rem_path,
        [
            "pid",
            "night_number",
            "condition",
            "session_start_boston",
            "session_end_boston",
            "episode_index",
            "episode_start_epoch_sec",
            "episode_duration_sec",
            "episode_start_boston",
            "episode_end_boston",
            "episode_motion_avg",
        ],
        rem_rows,
    )

    write_csv(
        cues_path,
        [
            "pid",
            "night_number",
            "condition",
            "episode_index",
            "train_index",
            "cue_index",
            "cue_type",
            "took_place",
            "epoch_sec",
            "event_time_boston",
            "volume",
            "arousal_detected",
        ],
        cue_rows,
    )

    write_csv(
        trains_path,
        [
            "pid",
            "night_number",
            "condition",
            "episode_index",
            "train_index",
            "disruptive_took_place",
            "disruptive_start_epoch_sec",
            "first_induction_epoch_sec",
            "train_end_epoch_sec",
            "train_duration_sec",
            "induction_cues_count",
        ],
        train_rows,
    )

    write_csv(
        motion_path,
        [
            "pid",
            "night_number",
            "condition",
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
        ],
        motion_rows,
    )

    write_csv(
        smoothed_path,
        [
            "pid",
            "night_number",
            "condition",
            "session_start_boston",
            "session_end_boston",
            "second_index",
            "epoch_sec",
            "time_boston",
            "motion_smoothed",
        ],
        smoothed_rows,
    )

    print(f"Wrote {summary_path} ({len(summary_rows)} rows)")
    print(f"Wrote {rem_path} ({len(rem_rows)} rows)")
    print(f"Wrote {cues_path} ({len(cue_rows)} rows)")
    print(f"Wrote {trains_path} ({len(train_rows)} rows)")
    print(f"Wrote {motion_path} ({len(motion_rows)} rows)")
    print(f"Wrote {smoothed_path} ({len(smoothed_rows)} rows)")


if __name__ == "__main__":
    main()
