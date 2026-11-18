"""S3-compatible object-storage operations used by the transcription backend."""

from __future__ import annotations

import io
from pathlib import Path
from typing import List

from minio import Minio
from minio.error import S3Error

from ..config import ScannerSettings
from .contract import NoteObject, RecordingObject
from .inbox import is_live_segment_object


class MinioRecordingStore:
    def __init__(self, settings: ScannerSettings) -> None:
        self.settings = settings
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region=settings.minio_region,
        )
        if settings.storage_provider == "huawei-obs":
            self.client.enable_virtual_style_endpoint()

    def list_recordings(self) -> List[RecordingObject]:
        try:
            objects = self.client.list_objects(
                self.settings.minio_bucket,
                prefix=self.settings.recordings_prefix,
                recursive=True,
            )
            return [
                RecordingObject(
                    object_name=str(item.object_name or ""),
                    etag=str(item.etag or "").strip('"'),
                    size=int(item.size or 0),
                    last_modified=item.last_modified,
                )
                for item in objects
                if str(item.object_name or "").lower().endswith(".wav")
                and not is_live_segment_object(
                    str(item.object_name or ""), self.settings.recordings_prefix
                )
            ]
        except S3Error as exc:
            raise RuntimeError("无法扫描云端录音：%s" % exc) from exc

    def download_recording(self, object_name: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.fget_object(self.settings.minio_bucket, object_name, str(destination))
        except S3Error as exc:
            raise RuntimeError("无法下载待转写录音 %s：%s" % (object_name, exc)) from exc

    def list_live_segments(self) -> List[RecordingObject]:
        """列出直播预览分段（recordings/<设备>/live/<录音>/...）。

        注意 prefix 必须是 recordings_prefix 整体（设备 key 中设备 ID 位于
        live 之前），再用 is_live_segment_object 过滤——之前误用
        "recordings/live/" 前缀导致云端分段永远扫不到。
        """
        try:
            objects = self.client.list_objects(
                self.settings.minio_bucket,
                prefix=self.settings.recordings_prefix,
                recursive=True,
            )
            return [
                RecordingObject(
                    object_name=str(item.object_name or ""),
                    etag=str(item.etag or "").strip('"'),
                    size=int(item.size or 0),
                    last_modified=item.last_modified,
                )
                for item in objects
                if str(item.object_name or "").lower().endswith(".wav")
                and is_live_segment_object(
                    str(item.object_name or ""), self.settings.recordings_prefix
                )
            ]
        except S3Error as exc:
            raise RuntimeError("无法扫描直播分段：%s" % exc) from exc

    def delete_recording(self, object_name: str) -> None:
        try:
            self.client.remove_object(self.settings.minio_bucket, object_name)
        except S3Error as exc:
            raise RuntimeError("无法删除云端对象 %s：%s" % (object_name, exc)) from exc

    def note_object_name(self, recording_object_name: str) -> str:
        relative_name = recording_object_name.removeprefix(self.settings.recordings_prefix)
        return self.settings.notes_prefix + Path(relative_name).with_suffix(".md").as_posix()

    def upload_note(self, object_name: str, markdown: str) -> None:
        body = markdown.encode("utf-8")
        try:
            self.client.put_object(
                self.settings.minio_bucket,
                object_name,
                io.BytesIO(body),
                len(body),
                content_type="text/markdown; charset=utf-8",
            )
        except S3Error as exc:
            raise RuntimeError("无法保存云端笔记 %s：%s" % (object_name, exc)) from exc

    def list_notes(self, prefix: str | None = None) -> List[NoteObject]:
        note_prefix = prefix or self.settings.notes_prefix
        try:
            objects = self.client.list_objects(
                self.settings.minio_bucket,
                prefix=note_prefix,
                recursive=True,
            )
            return [
                NoteObject(
                    object_name=str(item.object_name or ""),
                    etag=str(item.etag or "").strip('"'),
                    size=int(item.size or 0),
                    last_modified=item.last_modified,
                )
                for item in objects
                if str(item.object_name or "").lower().endswith(".md")
            ]
        except S3Error as exc:
            raise RuntimeError("无法扫描云端转写笔记：%s" % exc) from exc

    def download_note(self, object_name: str) -> str:
        try:
            response = self.client.get_object(self.settings.minio_bucket, object_name)
            try:
                return response.read().decode("utf-8")
            finally:
                response.close()
                response.release_conn()
        except (S3Error, UnicodeDecodeError) as exc:
            raise RuntimeError("无法读取转写笔记 %s：%s" % (object_name, exc)) from exc


def create_store(settings: ScannerSettings) -> MinioRecordingStore:
    """Default storage factory kept for MinIO and other S3-compatible services."""
    return MinioRecordingStore(settings)
