#!/usr/bin/env python3
"""Issue or revoke customer sync credentials without exposing storage access."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import JobRepository  # noqa: E402
from app.sync_api import SyncAuthorizationService  # noqa: E402


def expiration(value: str) -> str:
    if not value.strip():
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("过期时间必须为 ISO 8601，例如 2026-09-01T00:00:00+08:00") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed <= datetime.now(timezone.utc):
        raise argparse.ArgumentTypeError("过期时间必须晚于当前时间。")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description="管理录音豆同步助手的客户激活码")
    parser.add_argument(
        "--database",
        default=os.getenv(
            "TRANSCRIPTION_DB_PATH",
            str(BACKEND_ROOT / "data" / "transcriptions.db"),
        ),
        type=Path,
        help="转写任务 SQLite 路径",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    issue = commands.add_parser("issue", help="签发一次性激活码")
    issue.add_argument("--device-id", required=True)
    issue.add_argument("--expires-at", type=expiration, default="")
    issue.add_argument("--label", default="")

    revoke = commands.add_parser("revoke", help="撤销某台设备已签发的全部同步令牌")
    revoke.add_argument("--device-id", required=True)

    args = parser.parse_args()
    repository = JobRepository(args.database.expanduser())
    service = SyncAuthorizationService(repository)
    try:
        if args.command == "issue":
            code = service.issue_activation_code(args.device_id, args.expires_at, args.label)
            print("device_id=%s" % args.device_id)
            print("activation_code=%s" % code)
            if args.expires_at:
                print("expires_at=%s" % args.expires_at)
            return 0
        if args.command == "revoke":
            revoked = service.revoke_device(args.device_id)
            if revoked == 0:
                print("该设备没有有效同步令牌。", file=sys.stderr)
                return 1
            print("已撤销 %d 个同步令牌。" % revoked)
            return 0
        return 2
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
