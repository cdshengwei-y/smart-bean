"""Authenticated, device-scoped access to completed Markdown notes."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import JobRepository


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_device_id(value: str) -> bool:
    return bool(value) and all(
        character.isascii() and (character.isalnum() or character in "_-")
        for character in value
    )


def new_activation_code() -> str:
    """Return a high-entropy code suitable for a one-time customer activation."""
    return "ARB-" + secrets.token_urlsafe(24)


def new_device_token() -> str:
    """Return a long-lived bearer token; only its SHA-256 hash is persisted."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class SyncNote:
    id: int
    relative_path: str
    etag: str
    updated_at: str


class SyncAuthorizationService:
    def __init__(self, repository: JobRepository) -> None:
        self.repository = repository

    def issue_activation_code(
        self,
        device_id: str,
        expires_at: str = "",
        label: str = "",
        replace_existing: bool = False,
    ) -> str:
        if not _valid_device_id(device_id):
            raise ValueError("设备 ID 只能包含字母、数字、下划线和连字符。")
        if replace_existing:
            self.repository.revoke_pending_replacement_activation_codes_for_device(
                device_id
            )
        code = new_activation_code()
        self.repository.create_sync_activation_code(
            code_hash=_hash_secret(code),
            device_id=device_id,
            expires_at=expires_at,
            label=label,
            replace_existing=replace_existing,
        )
        return code

    def activate(self, activation_code: str, label: str = "") -> tuple[str, str]:
        code = activation_code.strip()
        if len(code) < 24 or len(code) > 256:
            raise PermissionError("激活码无效或已失效。")
        token = new_device_token()
        device_id = self.repository.redeem_sync_activation_code_and_create_token(
            code_hash=_hash_secret(code),
            token_hash=_hash_secret(token),
            label=label,
        )
        if device_id is None:
            raise PermissionError("激活码无效、已使用或已失效。")
        return device_id, token

    def active_clients(self, device_id: str) -> list[dict[str, str]]:
        if not _valid_device_id(device_id):
            raise ValueError("设备 ID 只能包含字母、数字、下划线和连字符。")
        return [
            {
                "label": str(row["label"]),
                "created_at": str(row["created_at"]),
                "last_used_at": str(row["last_used_at"]),
            }
            for row in self.repository.list_active_sync_tokens_for_device(device_id)
        ]

    def revoke(self, token: str) -> bool:
        return self.repository.revoke_sync_token(_hash_secret(token.strip()))

    def revoke_device(self, device_id: str) -> int:
        if not _valid_device_id(device_id):
            raise ValueError("设备 ID 只能包含字母、数字、下划线和连字符。")
        return self.repository.revoke_sync_tokens_for_device(device_id)

    def authenticate(self, token: str) -> str:
        value = token.strip()
        if len(value) < 32 or len(value) > 512:
            raise PermissionError("同步令牌无效或已失效。")
        device_id = self.repository.authenticate_sync_token(_hash_secret(value))
        if device_id is None:
            raise PermissionError("同步令牌无效或已失效。")
        return device_id


class CompletedNotesService:
    """Expose only notes whose optimization and filtering have completed."""

    def __init__(self, repository: JobRepository, store: Any, notes_prefix: str) -> None:
        self.repository = repository
        self.store = store
        self.notes_prefix = notes_prefix.strip("/") + "/"

    def list_notes(self, device_id: str) -> list[SyncNote]:
        expected_prefix = "%s%s/" % (self.notes_prefix, device_id)
        objects = {
            item.object_name: item
            for item in self.store.list_notes(expected_prefix)
            if item.object_name.startswith(expected_prefix)
        }
        notes: list[SyncNote] = []
        for row in self.repository.list_completed_notes(device_id):
            object_name = str(row["note_object_name"])
            item = objects.get(object_name)
            if item is None:
                continue
            relative_path = self._relative_path(object_name, expected_prefix)
            notes.append(
                SyncNote(
                    id=int(row["id"]),
                    relative_path=relative_path,
                    etag=str(item.etag),
                    updated_at=str(row["updated_at"]),
                )
            )
        return notes

    def download_note(self, device_id: str, note_id: int) -> tuple[SyncNote, str]:
        row = self.repository.get_completed_note(note_id, device_id)
        if row is None:
            raise FileNotFoundError("未找到可同步的笔记。")
        expected_prefix = "%s%s/" % (self.notes_prefix, device_id)
        object_name = str(row["note_object_name"])
        relative_path = self._relative_path(object_name, expected_prefix)
        object_info = next(
            (
                item
                for item in self.store.list_notes(object_name)
                if item.object_name == object_name
            ),
            None,
        )
        if object_info is None:
            raise FileNotFoundError("云端笔记暂不可用。")
        return (
            SyncNote(
                id=int(row["id"]),
                relative_path=relative_path,
                etag=str(object_info.etag),
                updated_at=str(row["updated_at"]),
            ),
            self.store.download_note(object_name),
        )

    @staticmethod
    def _relative_path(object_name: str, expected_prefix: str) -> str:
        if not object_name.startswith(expected_prefix):
            raise ValueError("云端笔记路径不属于当前设备。")
        relative_path = object_name.removeprefix(expected_prefix)
        path = Path(relative_path)
        if (
            not relative_path
            or path.is_absolute()
            or ".." in path.parts
            or path.suffix.lower() != ".md"
        ):
            raise ValueError("云端笔记路径无效。")
        return path.as_posix()
