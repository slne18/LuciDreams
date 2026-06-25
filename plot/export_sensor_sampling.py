#!/usr/bin/env python3
"""Firebase export helpers and sensor sampling summary export."""

import csv
import json
import os
import argparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FIREBASE_PROJECT_ID = "luciddreaming-33e97"
OUT_DIR = os.path.join(BASE_DIR, "sensor_sampling")
OUT_SAMPLING_CSV = os.path.join(OUT_DIR, "sensor_sampling_summary.csv")
BOSTON_TZ = ZoneInfo("America/New_York")

LOW_HZ_THRESHOLD = 5.0
STALL_DT_MS = 500.0
SEVERE_DT_MS = 1000.0


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


def _fmt(v: float, digits: int = 2) -> str:
    if v != v:
        return ""
    return f"{v:.{digits}f}"


def _pct(count: int, total: int) -> float:
    if total <= 0:
        return float("nan")
    return 100.0 * float(count) / float(total)


def _quantile(arr, q: float) -> float:
    if not arr:
        return float("nan")
    return float(np.quantile(np.asarray(arr, dtype=float), q))


def _mean_or_nan(arr) -> float:
    if not arr:
        return float("nan")
    return float(np.mean(np.asarray(arr, dtype=float)))


def _median_or_nan(arr) -> float:
    if not arr:
        return float("nan")
    return float(np.median(np.asarray(arr, dtype=float)))


def build_sensor_sampling_summary_row(key, series):
    """Build one sensor_sampling_summary.csv row (shared with check_sampling.py)."""
    pid, night, start = key
    eps = series["eps"]
    dt_avg = series["dt_avg"]
    dt_max = series["dt_max"]
    rows = max(len(series["second_index"]), len(eps), len(dt_avg), len(dt_max))

    eps_zero = sum(1 for v in eps if v <= 0)
    eps_low = sum(1 for v in eps if v < LOW_HZ_THRESHOLD)
    dt_stall = sum(1 for v in dt_max if v > STALL_DT_MS)
    dt_severe = sum(1 for v in dt_max if v > SEVERE_DT_MS)

    return {
        "pid": pid,
        "night_number": str(night),
        "session_start_boston": start,
        "rows": str(rows),
        "sensor_events_n": str(len(eps)),
        "sensor_events_mean_hz": _fmt(_mean_or_nan(eps), 2),
        "sensor_events_median_hz": _fmt(_median_or_nan(eps), 2),
        "sensor_events_p10_hz": _fmt(_quantile(eps, 0.10), 2),
        "sensor_events_p90_hz": _fmt(_quantile(eps, 0.90), 2),
        "sensor_events_zero_count": str(eps_zero),
        "sensor_events_below5hz_count": str(eps_low),
        "sensor_events_below5hz_pct": _fmt(_pct(eps_low, len(eps)), 2),
        "sensor_dt_avg_n": str(len(dt_avg)),
        "sensor_dt_avg_mean_ms": _fmt(_mean_or_nan(dt_avg), 2),
        "sensor_dt_avg_p95_ms": _fmt(_quantile(dt_avg, 0.95), 2),
        "sensor_dt_max_n": str(len(dt_max)),
        "sensor_dt_max_mean_ms": _fmt(_mean_or_nan(dt_max), 2),
        "sensor_dt_max_p95_ms": _fmt(_quantile(dt_max, 0.95), 2),
        "sensor_dt_max_gt500ms_count": str(dt_stall),
        "sensor_dt_max_gt500ms_pct": _fmt(_pct(dt_stall, len(dt_max)), 2),
        "sensor_dt_max_gt1000ms_count": str(dt_severe),
        "sensor_dt_max_gt1000ms_pct": _fmt(_pct(dt_severe, len(dt_max)), 2),
    }


def extract_sensor_series_from_session(data):
    """Pull per-second sensor sampling arrays from one Firebase session doc."""
    sensor_events_per_second_series = load_series_from_doc(
        data, "sensor_events_per_second_series", "sensor_events_per_second_series_json"
    )
    sensor_event_dt_avg_ms_series = load_series_from_doc(
        data, "sensor_event_dt_avg_ms_series", "sensor_event_dt_avg_ms_series_json"
    )
    sensor_event_dt_max_ms_series = load_series_from_doc(
        data, "sensor_event_dt_max_ms_series", "sensor_event_dt_max_ms_series_json"
    )

    max_len = max(
        len(sensor_events_per_second_series),
        len(sensor_event_dt_avg_ms_series),
        len(sensor_event_dt_max_ms_series),
    )
    series = {
        "second_index": [],
        "eps": [],
        "dt_avg": [],
        "dt_max": [],
    }
    for sec_idx in range(max_len):
        series["second_index"].append(float(sec_idx))
        if sec_idx < len(sensor_events_per_second_series):
            eps = sensor_events_per_second_series[sec_idx]
            if eps is not None and np.isfinite(float(eps)):
                series["eps"].append(float(eps))
        if sec_idx < len(sensor_event_dt_avg_ms_series):
            dt_avg = sensor_event_dt_avg_ms_series[sec_idx]
            if dt_avg is not None and np.isfinite(float(dt_avg)):
                series["dt_avg"].append(float(dt_avg))
        if sec_idx < len(sensor_event_dt_max_ms_series):
            dt_max = sensor_event_dt_max_ms_series[sec_idx]
            if dt_max is not None and np.isfinite(float(dt_max)):
                series["dt_max"].append(float(dt_max))
    return series


SAMPLING_SUMMARY_FIELDS = [
    "pid",
    "night_number",
    "session_start_boston",
    "rows",
    "sensor_events_n",
    "sensor_events_mean_hz",
    "sensor_events_median_hz",
    "sensor_events_p10_hz",
    "sensor_events_p90_hz",
    "sensor_events_zero_count",
    "sensor_events_below5hz_count",
    "sensor_events_below5hz_pct",
    "sensor_dt_avg_n",
    "sensor_dt_avg_mean_ms",
    "sensor_dt_avg_p95_ms",
    "sensor_dt_max_n",
    "sensor_dt_max_mean_ms",
    "sensor_dt_max_p95_ms",
    "sensor_dt_max_gt500ms_count",
    "sensor_dt_max_gt500ms_pct",
    "sensor_dt_max_gt1000ms_count",
    "sensor_dt_max_gt1000ms_pct",
]


def main():
    parser = argparse.ArgumentParser(description="Export per-session sensor sampling summary from Firebase.")
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
    parser.add_argument(
        "--output",
        default=OUT_SAMPLING_CSV,
        help=f"Output CSV path (default: {OUT_SAMPLING_CSV}).",
    )
    args = parser.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    summary_rows = []
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
        pid = item["pid"]
        night_number = item.get("night_number")
        if args.pid and str(pid) != str(args.pid):
            continue
        if args.night_number is not None and int(night_number or -1) != int(args.night_number):
            continue

        general = item["data"].get("general", {}) or {}
        session_start_boston = to_boston_time(general.get("device_time_start"))
        if not session_start_boston:
            continue

        session_count += 1
        participant_ids.add(pid)
        series = extract_sensor_series_from_session(item["data"])
        key = (str(pid), str(night_number or ""), session_start_boston)
        summary_rows.append(build_sensor_sampling_summary_row(key, series))

    summary_rows.sort(key=lambda r: (r["pid"], int(r["night_number"] or 0), r["session_start_boston"]))

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SAMPLING_SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"Scanned participants: {len(participant_ids)}")
    print(f"Scanned sessions: {session_count}")
    print(f"Wrote {args.output} ({len(summary_rows)} rows)")


if __name__ == "__main__":
    main()