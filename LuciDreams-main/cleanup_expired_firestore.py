#!/usr/bin/env python3
"""
Delete expired Firestore session documents (Spark-friendly manual cleanup).

By default, runs in dry-run mode and only reports documents that would be deleted.
Use --apply to actually delete them.

Target documents:
- collection group: "sessions"
- filter: expires_at <= now (UTC)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
from typing import Optional

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.api_core.exceptions import FailedPrecondition

RETENTION_HOURS = 48
RETENTION_DELTA = dt.timedelta(hours=RETENTION_HOURS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete Firestore session docs where expires_at is in the past."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete documents (default is dry-run).",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Optional Firebase project ID override.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=400,
        help="Number of deletes per batch commit (default: 400).",
    )
    parser.add_argument(
        "--pid",
        default=None,
        help="Optional participant_id filter (delete only this participant's expired sessions).",
    )
    parser.add_argument(
        "--service-account",
        default=None,
        help="Path to service-account JSON (optional).",
    )
    return parser.parse_args()


def resolve_service_account(cli_value: Optional[str]) -> Optional[str]:
    if cli_value:
        p = Path(cli_value).expanduser().resolve()
        return str(p) if p.exists() else None

    env_value = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if env_value:
        p = Path(env_value).expanduser().resolve()
        if p.exists():
            return str(p)

    # Common local path for this repo: one level above LuciDreams-main.
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    default_name = "lucidreans-firebase-adminsdk-fbsvc-4b27ed98c4.json"
    candidate = repo_root / default_name
    if candidate.exists():
        return str(candidate)

    return None


def init_firestore(project_id: Optional[str], service_account_path: Optional[str]):
    if not firebase_admin._apps:
        if service_account_path:
            cred = credentials.Certificate(service_account_path)
            firebase_admin.initialize_app(cred, {"projectId": project_id} if project_id else None)
        else:
            firebase_admin.initialize_app(options={"projectId": project_id} if project_id else None)
    return firestore.client()


def to_utc_datetime(value) -> Optional[dt.datetime]:
    """Best-effort conversion to UTC datetime for Firestore/ISO values."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.timezone.utc) if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Handle Firestore/ISO style with trailing Z.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = dt.datetime.fromisoformat(s)
            return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            return None
    return None


def main() -> None:
    args = parse_args()
    service_account_path = resolve_service_account(args.service_account)

    if args.service_account and not service_account_path:
        raise FileNotFoundError(f"Service account not found: {args.service_account}")

    db = init_firestore(args.project, service_account_path)
    now = dt.datetime.now(dt.timezone.utc)

    print("Firestore cleanup started")
    print(f"- mode: {'APPLY (deletes enabled)' if args.apply else 'DRY-RUN'}")
    print(f"- now (UTC): {now.isoformat()}")
    if args.project:
        print(f"- project override: {args.project}")
    if args.pid:
        print(f"- participant filter: {args.pid}")
    if service_account_path:
        print(f"- service account: {service_account_path}")
    else:
        print("- service account: default application credentials")

    pid_target = str(args.pid) if args.pid else None

    def fetch_expired_with_index() -> list:
        query = db.collection_group("sessions").where(
            filter=FieldFilter("expires_at", "<=", now)
        )
        docs = list(query.stream())
        if pid_target:
            docs = [
                d for d in docs
                if str((d.to_dict() or {}).get("participant_id", "")) == pid_target
            ]
        return docs

    def fetch_expired_without_index() -> list:
        docs = []
        studies = db.collection("sleep_studies")
        if pid_target:
            participants = [studies.document(pid_target)]
        else:
            participants = list(studies.stream())

        retention_cutoff = now - RETENTION_DELTA

        for p in participants:
            p_ref = p.reference if hasattr(p, "reference") else p
            pid_from_path = p_ref.id
            for s in p_ref.collection("sessions").stream():
                data = s.to_dict() or {}
                # PID filter: prefer path pid, then participant_id field fallback.
                if pid_target:
                    pid_field = str(data.get("participant_id", "")) if data.get("participant_id") is not None else ""
                    if pid_from_path != pid_target and pid_field != pid_target:
                        continue

                # 1) Preferred explicit expiry timestamp.
                exp = to_utc_datetime(data.get("expires_at"))

                # 2) Backward-compat for older docs without expires_at:
                #    treat them as expired if session end time is older than retention window.
                if exp is None:
                    g = data.get("general") or {}
                    session_end = (
                        to_utc_datetime(g.get("device_time_end"))
                        or to_utc_datetime(data.get("device_time_end"))
                        or to_utc_datetime(data.get("device_time"))
                    )
                    if session_end is not None and session_end <= retention_cutoff:
                        docs.append(s)
                    continue

                if exp <= now:
                    docs.append(s)
        return docs

    try:
        expired_docs = fetch_expired_with_index()
        print("- query mode: indexed collection-group query")
    except FailedPrecondition as e:
        msg = str(e)
        if "requires a COLLECTION_GROUP_ASC index" in msg or "requires an index" in msg:
            print("- query mode: fallback scan (missing Firestore index)")
            expired_docs = fetch_expired_without_index()
        else:
            raise
    total = len(expired_docs)
    print(f"- expired session docs found: {total}")

    if total == 0:
        print("Nothing to delete.")
        return

    for i, doc in enumerate(expired_docs[:10], start=1):
        expires_at = doc.to_dict().get("expires_at")
        print(f"  [{i}] {doc.reference.path} | expires_at={expires_at}")
    if total > 10:
        print(f"  ... and {total - 10} more")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to delete.")
        return

    deleted = 0
    batch = db.batch()
    pending = 0
    batch_size = max(1, min(args.batch_size, 450))

    for doc in expired_docs:
        batch.delete(doc.reference)
        pending += 1
        if pending >= batch_size:
            batch.commit()
            deleted += pending
            print(f"- committed batch, deleted so far: {deleted}")
            batch = db.batch()
            pending = 0

    if pending:
        batch.commit()
        deleted += pending

    print(f"\nDone. Deleted {deleted} expired session docs.")


if __name__ == "__main__":
    main()
