"""Path conventions that keep recordings and notes traceable to one device."""

from __future__ import annotations

from pathlib import PurePosixPath


LEGACY_DEVICE_ID = "legacy"

# These are seeded into the administrator's device directory on first startup.
# Future devices are added explicitly through the administrator interface;
# historical jobs and accidental test identities are never auto-registered.
FORMAL_DEVICE_IDS = ("bean_ye_1", "bean_ye_2")


def device_id_from_recording_object(object_name: str, recordings_prefix: str = "recordings/") -> str:
    """Return the first directory below recordings/, or legacy for old flat uploads."""
    relative_name = object_name.removeprefix(recordings_prefix)
    if relative_name == object_name:
        raise ValueError("录音对象不在配置的 recordings 前缀下：%s" % object_name)

    parts = PurePosixPath(relative_name).parts
    if len(parts) < 2:
        return LEGACY_DEVICE_ID

    device_id = parts[0]
    if device_id in ("", ".", "..") or "/" in device_id or "\\" in device_id:
        raise ValueError("录音对象中的 device_id 无效：%s" % object_name)
    return device_id
