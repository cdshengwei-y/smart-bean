from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

# AI 流程测试需要 AI 队列处于启用状态
os.environ.setdefault("RECORDER_AI_FILTER", "1")

from app.database import JobRepository
from app.storage.minio_store import NoteObject, RecordingObject
from app.sync_api import SyncAuthorizationService
from status_server import StatusHandler, status_payload


class StatusServerTests(unittest.TestCase):
    class FakeSyncStore:
        def list_notes(self, prefix=None):
            notes = [
                NoteObject("notes/bean-a1/REC_001.md", "note-etag", 20, None),
                NoteObject("notes/bean-b2/REC_002.md", "other-etag", 20, None),
            ]
            return [item for item in notes if prefix is None or item.object_name.startswith(prefix)]

        def download_note(self, object_name):
            if object_name == "notes/bean-a1/REC_001.md":
                return "# 已完成\n"
            raise FileNotFoundError(object_name)

    def test_payload_exposes_job_state_without_transcript_or_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jobs.db"
            repository = JobRepository(database_path)
            repository.discover([RecordingObject("recordings/bean-a1b2c3/REC_001.wav", "etag-1", 42, None)])
            job = repository.claim_next(max_attempts=3, retry_delay_seconds=1)
            assert job is not None
            repository.fail(job.id, "ASR unavailable")
            repository.close()

            with patch(
                "status_server.ModelRegistry.health_status",
                return_value={"zhipu-glm-asr": True},
            ):
                payload = status_payload(database_path)

        self.assertEqual(payload["counts"], {"failed": 1})
        self.assertEqual(payload["jobs"][0]["recording_name"], "REC_001.wav")
        self.assertEqual(payload["jobs"][0]["device_id"], "bean-a1b2c3")
        self.assertEqual(payload["devices"], [{"device_id": "bean-a1b2c3", "counts": {"failed": 1}}])
        self.assertEqual(payload["jobs"][0]["error"], "ASR unavailable")
        self.assertEqual(payload["jobs"][0]["job_id"], job.id)
        self.assertEqual(payload["jobs"][0]["ai_attempts"], 0)
        self.assertEqual(payload["jobs"][0]["durations"]["asr"], 0.0)
        self.assertEqual(payload["asr"]["selected_model_id"], "zhipu-glm-asr")
        self.assertEqual(len(payload["asr"]["models"]), 1)
        models = {model["id"]: model for model in payload["asr"]["models"]}
        self.assertTrue(models["zhipu-glm-asr"]["healthy"])
        self.assertNotIn("qwen3-asr", models)
        self.assertNotIn("transcript", payload["jobs"][0])
        self.assertNotIn("note_object_name", payload["jobs"][0])

    def test_model_selection_rejects_removed_qwen_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "asr_model.json"

            class Handler(StatusHandler):
                model_config_path = config_path

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(
                    "127.0.0.1",
                    server.server_port,
                    timeout=3,
                )
                body = json.dumps({"model_id": "qwen3-asr"}).encode("utf-8")
                connection.request(
                    "POST",
                    "/api/models/select",
                    body=iter([body]),
                    headers={"Content-Type": "application/json"},
                    encode_chunked=True,
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

        self.assertEqual(response.status, 400)
        self.assertIn("不支持的转写模型", payload["error"])

    def test_admin_can_issue_list_and_revoke_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jobs.db"

            class Handler(StatusHandler):
                pass

            Handler.database_path = database_path
            Handler.admin_password = "test-admin-password"
            Handler.admin_sessions = {}
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request("GET", "/api/admin/session")
                session_response = connection.getresponse()
                session_payload = json.loads(session_response.read())
                self.assertEqual(session_response.status, 200)
                self.assertTrue(session_payload["configured"])
                self.assertFalse(session_payload["authenticated"])

                connection.request(
                    "POST",
                    "/api/admin/login",
                    body=json.dumps({"password": "test-admin-password"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                login_response = connection.getresponse()
                login_payload = json.loads(login_response.read())
                cookie = login_response.getheader("Set-Cookie", "").split(";", 1)[0]
                self.assertEqual(login_response.status, 200)
                self.assertTrue(login_payload["authenticated"])
                self.assertTrue(cookie.startswith("admin_session="))

                connection.request("GET", "/api/admin/access", headers={"Cookie": cookie})
                empty_response = connection.getresponse()
                empty_payload = json.loads(empty_response.read())
                self.assertEqual(empty_response.status, 200)
                self.assertEqual(empty_payload["activation_codes"], [])
                self.assertEqual(empty_payload["sync_tokens"], [])

                connection.request(
                    "POST",
                    "/api/admin/activation-codes",
                    body=json.dumps(
                        {
                            "device_id": "bean_not_registered",
                            "label": "should fail",
                            "expires_at": "",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Cookie": cookie},
                )
                rejected_issue_response = connection.getresponse()
                rejected_issue_payload = json.loads(rejected_issue_response.read())
                self.assertEqual(rejected_issue_response.status, 400)
                self.assertIn("先登记", rejected_issue_payload["error"])

                connection.request(
                    "POST",
                    "/api/admin/devices",
                    body=json.dumps(
                        {
                            "device_id": "bean_ye_3",
                            "label": "第三台测试机",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Cookie": cookie},
                )
                register_response = connection.getresponse()
                register_payload = json.loads(register_response.read())
                self.assertEqual(register_response.status, 201)
                self.assertEqual(register_payload["device_id"], "bean_ye_3")

                connection.request(
                    "GET",
                    "/api/admin/dashboard",
                    headers={"Cookie": cookie},
                )
                dashboard_response = connection.getresponse()
                dashboard_payload = json.loads(dashboard_response.read())
                self.assertEqual(dashboard_response.status, 200)
                self.assertEqual(dashboard_payload["summary"]["known_devices"], 3)
                self.assertEqual(
                    [item["device_id"] for item in dashboard_payload["devices"]],
                    ["bean_ye_1", "bean_ye_2", "bean_ye_3"],
                )

                connection.request(
                    "POST",
                    "/api/admin/activation-codes",
                    body=json.dumps(
                        {
                            "device_id": "bean_ye_3",
                            "label": "third mac",
                            "expires_at": "",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Cookie": cookie},
                )
                issue_response = connection.getresponse()
                issue_payload = json.loads(issue_response.read())
                self.assertEqual(issue_response.status, 201)
                self.assertTrue(issue_payload["activation_code"].startswith("ARB-"))
                self.assertEqual(issue_payload["expires_at"], "")

                connection.request("GET", "/api/admin/access", headers={"Cookie": cookie})
                access_response = connection.getresponse()
                access_payload = json.loads(access_response.read())
                self.assertEqual(access_payload["activation_codes"][0]["status"], "active")
                code_id = access_payload["activation_codes"][0]["id"]

                connection.request(
                    "POST",
                    "/api/admin/activation-codes/revoke",
                    body=json.dumps({"code_id": code_id}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Cookie": cookie},
                )
                revoke_response = connection.getresponse()
                self.assertEqual(revoke_response.status, 200)

                repository = JobRepository(database_path)
                activation_row = repository.list_sync_activation_codes()[0]
                repository.close()
                self.assertTrue(str(activation_row["revoked_at"]))
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_admin_dashboard_combines_user_device_job_and_sync_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "dashboard.db"
            repository = JobRepository(database_path)
            user = repository.create_user_account("owner@example.com", "not-used")
            repository.bind_user_recorder_device(int(user["id"]), "bean_ye_1")
            repository.discover(
                [
                    RecordingObject("recordings/bean_ye_1/REC_001.wav", "etag-1", 42, None),
                    RecordingObject(
                        "recordings/bean-F865DF90A994/REC_TEST.wav",
                        "test-etag",
                        42,
                        None,
                    ),
                    RecordingObject(
                        "recordings/REC_20260818_000000_0/P000.wav",
                        "legacy-etag",
                        42,
                        None,
                    ),
                ]
            )
            job = repository.claim_next(max_attempts=3, retry_delay_seconds=1)
            assert job is not None
            repository.fail(job.id, "ASR unavailable")
            activation_code = SyncAuthorizationService(repository).issue_activation_code(
                "bean_ye_1",
                label="ops mac",
                replace_existing=True,
            )
            SyncAuthorizationService(repository).activate(activation_code, "ops mac")
            repository.close()

            class Handler(StatusHandler):
                pass

            Handler.database_path = database_path
            Handler.admin_password = "test-admin-password"
            Handler.admin_sessions = {}
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request(
                    "POST",
                    "/api/admin/login",
                    body=json.dumps({"password": "test-admin-password"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                login_response = connection.getresponse()
                login_response.read()
                cookie = login_response.getheader("Set-Cookie", "").split(";", 1)[0]

                with patch(
                    "status_server.ModelRegistry.status",
                    return_value={
                        "selected_model_id": "local-asr",
                        "models": [{"id": "local-asr", "name": "本地 ASR", "healthy": True}],
                    },
                ):
                    connection.request(
                        "GET",
                        "/api/admin/dashboard",
                        headers={"Cookie": cookie},
                    )
                    response = connection.getresponse()
                    payload = json.loads(response.read())

                self.assertEqual(response.status, 200)
                self.assertEqual(payload["summary"]["users"], 1)
                self.assertEqual(payload["summary"]["bound_devices"], 1)
                self.assertEqual(payload["summary"]["active_sync_clients"], 1)
                self.assertEqual(payload["summary"]["jobs_failed"], 1)
                self.assertEqual(payload["users"][0]["device_id"], "bean_ye_1")
                self.assertEqual(payload["devices"][0]["owner_email"], "owner@example.com")
                self.assertEqual(payload["devices"][0]["jobs_failed"], 1)
                self.assertEqual(
                    [item["device_id"] for item in payload["devices"]],
                    ["bean_ye_1", "bean_ye_2"],
                )
                self.assertTrue(payload["access"]["activation_codes"][0]["replaces_previous"])
                self.assertEqual(payload["asr"]["selected_model_id"], "local-asr")
                self.assertEqual(payload["summary"]["known_devices"], 2)
                self.assertNotIn(
                    "bean-F865DF90A994",
                    [item["device_id"] for item in payload["devices"]],
                )
                self.assertNotIn("legacy", [item["device_id"] for item in payload["devices"]])
                self.assertFalse(
                    any(
                        item["device_id"].startswith("REC_")
                        for item in payload["devices"]
                    )
                )
                self.assertTrue(
                    any(
                        item["device_id"] == "bean-F865DF90A994"
                        for item in payload["recent_jobs"]
                    )
                )
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_admin_can_list_runs_and_queue_one_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "runs.db"
            repository = JobRepository(database_path)
            repository.discover(
                [RecordingObject("recordings/bean_ye_1/REC_001.wav", "etag-1", 42, None)]
            )
            job = repository.claim_next_transcription(3, 1)
            assert job is not None
            repository.save_transcript(
                job.id,
                "这是完整转写内容，用于确认管理员只会看到预览。",
                "notes/bean_ye_1/REC_001.md",
                "zh",
                "模型一",
                2.0,
                0.1,
                0.2,
                transcription_model_id="model-one",
            )
            run_id = repository.start_transcription_run(job.id, "model-one")
            repository.complete_transcription_run(
                run_id,
                "模型一",
                "zh",
                2.0,
                0.1,
                0.2,
                "这是完整转写内容，用于确认管理员只会看到预览。",
            )
            repository.close()

            class Handler(StatusHandler):
                pass

            Handler.database_path = database_path
            Handler.admin_password = "test-admin-password"
            Handler.admin_sessions = {}
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request("GET", "/api/admin/transcription-runs")
                unauthorized = connection.getresponse()
                self.assertEqual(unauthorized.status, 401)
                unauthorized.read()

                connection.request(
                    "POST",
                    "/api/admin/login",
                    body=json.dumps({"password": "test-admin-password"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                login = connection.getresponse()
                login.read()
                cookie = login.getheader("Set-Cookie", "").split(";", 1)[0]

                connection.request(
                    "GET",
                    "/api/admin/transcription-runs",
                    headers={"Cookie": cookie},
                )
                listed = connection.getresponse()
                listed_payload = json.loads(listed.read())
                self.assertEqual(listed.status, 200)
                self.assertEqual(listed_payload["runs"][0]["model_id"], "model-one")
                self.assertLessEqual(len(listed_payload["runs"][0]["transcript_preview"]), 240)

                connection.request(
                    "POST",
                    "/api/admin/transcription-runs/%d/replay" % run_id,
                    body=b"{}",
                    headers={"Content-Type": "application/json", "Cookie": cookie},
                )
                queued = connection.getresponse()
                queued_payload = json.loads(queued.read())
                self.assertEqual(queued.status, 202)
                self.assertEqual(queued_payload["source_run_id"], run_id)

                connection.request(
                    "POST",
                    "/api/admin/transcription-runs/%d/replay" % run_id,
                    body=b"{}",
                    headers={"Content-Type": "application/json", "Cookie": cookie},
                )
                duplicate = connection.getresponse()
                self.assertEqual(duplicate.status, 409)
                duplicate.read()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_user_can_register_restore_session_and_logout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "users.db"

            class Handler(StatusHandler):
                pass

            Handler.database_path = database_path
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request(
                    "POST",
                    "/api/auth/register",
                    body=json.dumps(
                        {"email": "person@example.com", "password": "passw0rd!"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                register_response = connection.getresponse()
                register_payload = json.loads(register_response.read())
                self.assertEqual(register_response.status, 201)
                self.assertEqual(register_payload["user"]["email"], "person@example.com")
                token = register_payload["token"]
                self.assertGreater(len(token), 32)

                connection.request(
                    "GET",
                    "/api/auth/me",
                    headers={"Authorization": "Bearer %s" % token},
                )
                me_response = connection.getresponse()
                me_payload = json.loads(me_response.read())
                self.assertEqual(me_response.status, 200)
                self.assertEqual(me_payload["user"]["email"], "person@example.com")

                connection.request(
                    "POST",
                    "/api/auth/change-password",
                    body=json.dumps(
                        {
                            "current_password": "incorrect",
                            "new_password": "renewedpass!",
                        }
                    ).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer %s" % token,
                    },
                )
                incorrect_password_response = connection.getresponse()
                self.assertEqual(incorrect_password_response.status, 401)
                incorrect_password_response.read()

                connection.request(
                    "POST",
                    "/api/auth/change-password",
                    body=json.dumps(
                        {
                            "current_password": "passw0rd!",
                            "new_password": "renewedpass!",
                        }
                    ).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer %s" % token,
                    },
                )
                change_response = connection.getresponse()
                self.assertEqual(change_response.status, 200)
                self.assertTrue(json.loads(change_response.read())["changed"])

                connection.request(
                    "POST",
                    "/api/auth/login",
                    body=json.dumps(
                        {"email": "person@example.com", "password": "passw0rd!"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                old_password_login_response = connection.getresponse()
                self.assertEqual(old_password_login_response.status, 401)
                old_password_login_response.read()

                connection.request(
                    "POST",
                    "/api/auth/login",
                    body=json.dumps(
                        {
                            "email": "person@example.com",
                            "password": "renewedpass!",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                new_password_login_response = connection.getresponse()
                self.assertEqual(new_password_login_response.status, 200)
                new_password_login_response.read()

                connection.request(
                    "POST",
                    "/api/auth/logout",
                    headers={"Authorization": "Bearer %s" % token},
                )
                logout_response = connection.getresponse()
                self.assertEqual(logout_response.status, 200)
                logout_response.read()

                connection.request(
                    "GET",
                    "/api/auth/me",
                    headers={"Authorization": "Bearer %s" % token},
                )
                expired_response = connection.getresponse()
                self.assertEqual(expired_response.status, 401)
                expired_response.read()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_signed_in_user_can_switch_the_obsidian_sync_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "users.db"

            class Handler(StatusHandler):
                pass

            Handler.database_path = database_path
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request(
                    "POST",
                    "/api/auth/register",
                    body=json.dumps(
                        {"email": "person@example.com", "password": "passw0rd!"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                token = json.loads(connection.getresponse().read())["token"]
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer %s" % token,
                }

                connection.request(
                    "GET",
                    "/api/mobile/obsidian-sync",
                    headers={"Authorization": "Bearer %s" % token},
                )
                unbound_response = connection.getresponse()
                unbound = json.loads(unbound_response.read())
                self.assertEqual(unbound_response.status, 200)
                self.assertFalse(unbound["bound"])

                connection.request(
                    "POST",
                    "/api/mobile/recorder-binding",
                    body=json.dumps({"device_id": "bean-a1"}).encode("utf-8"),
                    headers=headers,
                )
                binding_response = connection.getresponse()
                binding = json.loads(binding_response.read())
                self.assertEqual(binding_response.status, 200)
                self.assertTrue(binding["bound"])

                connection.request(
                    "POST",
                    "/api/mobile/obsidian-sync/activation",
                    body=json.dumps({"target_name": "工作电脑"}).encode("utf-8"),
                    headers=headers,
                )
                issued_response = connection.getresponse()
                issued = json.loads(issued_response.read())
                self.assertEqual(issued_response.status, 201)
                self.assertTrue(issued["activation_code"].startswith("ARB-"))
                self.assertTrue(issued["replaces_previous"])

                connection.request(
                    "POST",
                    "/api/sync/activate",
                    body=json.dumps(
                        {
                            "activation_code": issued["activation_code"],
                            "client_label": "Obsidian: Work Vault",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                activated_response = connection.getresponse()
                self.assertEqual(activated_response.status, 200)
                activated_response.read()

                connection.request(
                    "GET",
                    "/api/mobile/obsidian-sync",
                    headers={"Authorization": "Bearer %s" % token},
                )
                status_response = connection.getresponse()
                status = json.loads(status_response.read())
                self.assertEqual(status_response.status, 200)
                self.assertTrue(status["bound"])
                self.assertEqual(len(status["targets"]), 1)
                self.assertEqual(status["targets"][0]["label"], "Obsidian: Work Vault")
                self.assertEqual(status["targets"][0]["last_used_at"], "")

                connection.request(
                    "POST",
                    "/api/mobile/obsidian-sync/activation",
                    body=json.dumps({"target_name": "家里电脑"}).encode("utf-8"),
                    headers=headers,
                )
                replacement = json.loads(connection.getresponse().read())
                connection.request(
                    "POST",
                    "/api/sync/activate",
                    body=json.dumps(
                        {
                            "activation_code": replacement["activation_code"],
                            "client_label": "Obsidian: Home Vault",
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                replacement_response = connection.getresponse()
                self.assertEqual(replacement_response.status, 200)
                replacement_response.read()

                connection.request(
                    "GET",
                    "/api/mobile/obsidian-sync",
                    headers={"Authorization": "Bearer %s" % token},
                )
                switched = json.loads(connection.getresponse().read())
                self.assertTrue(switched["bound"])
                self.assertEqual(switched["targets"][0]["label"], "Obsidian: Home Vault")
                self.assertEqual(len(switched["targets"]), 1)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_ai_failed_job_can_be_retried_without_transcription(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jobs.db"
            repository = JobRepository(database_path)
            repository.discover(
                [RecordingObject("recordings/bean-a1b2c3/REC_002.wav", "etag-2", 42, None)]
            )
            job = repository.claim_next_transcription(3, 1)
            assert job is not None
            repository.save_transcript(
                job.id,
                "已保存的原始转写。",
                "notes/bean-a1b2c3/REC_002.md",
                "zh",
                "fake-asr",
                1.0,
                0.1,
                0.2,
            )
            note = repository.claim_next_note(3, 1)
            assert note is not None
            repository.mark_note_ready(job.id, 0.1)
            ai_job = repository.claim_next_ai(1, 1)
            assert ai_job is not None
            repository.fail_ai(job.id, "模型不可用")
            repository.close()

            class Handler(StatusHandler):
                pass

            Handler.database_path = database_path
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=3
                )
                connection.request("POST", "/api/jobs/%d/retry-ai" % job.id, body=b"{}")
                response = connection.getresponse()
                payload = json.loads(response.read())
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

            repository = JobRepository(database_path)
            saved = repository.list_jobs()[0]
            repository.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "ai_pending")
        self.assertEqual(saved["status"], "ai_pending")
        self.assertEqual(saved["attempts"], 1)
        self.assertEqual(saved["ai_attempts"], 0)

    def test_sync_api_uses_one_time_activation_and_hides_unfinished_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "jobs.db"
            repository = JobRepository(database_path)
            repository.discover(
                [
                    RecordingObject("recordings/bean-a1/REC_001.wav", "recording-1", 42, None),
                    RecordingObject("recordings/bean-a1/REC_003.wav", "recording-3", 42, None),
                ]
            )
            completed, unfinished = repository.list_jobs()
            repository.complete(
                int(completed["id"]),
                "完整转写",
                "notes/bean-a1/REC_001.md",
                filtered_text="最终内容",
                optimized_text="优化内容",
            )
            activation_code = SyncAuthorizationService(repository).issue_activation_code("bean-a1")
            repository.close()

            class Handler(StatusHandler):
                pass

            Handler.database_path = database_path
            Handler.sync_store_factory = self.FakeSyncStore
            Handler.sync_notes_prefix = "notes/"
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                activate_body = json.dumps(
                    {"activation_code": activation_code, "client_label": "test mac"}
                ).encode("utf-8")
                connection.request(
                    "POST",
                    "/api/sync/activate",
                    body=activate_body,
                    headers={"Content-Type": "application/json"},
                )
                activated_response = connection.getresponse()
                activated = json.loads(activated_response.read())
                self.assertEqual(activated_response.status, 200)
                self.assertEqual(activated["device_id"], "bean-a1")
                token = activated["sync_token"]

                connection.request(
                    "GET",
                    "/api/sync/notes",
                    headers={"Authorization": "Bearer " + token},
                )
                notes_response = connection.getresponse()
                notes_payload = json.loads(notes_response.read())
                self.assertEqual(notes_response.status, 200)
                self.assertEqual(notes_payload["notes"], [
                    {
                        "id": int(completed["id"]),
                        "path": "REC_001.md",
                        "etag": "note-etag",
                        "updated_at": str(completed["updated_at"]),
                    }
                ])

                connection.request(
                    "GET",
                    "/api/sync/notes/%d" % int(completed["id"]),
                    headers={"Authorization": "Bearer " + token},
                )
                note_response = connection.getresponse()
                self.assertEqual(note_response.status, 200)
                self.assertEqual(note_response.read().decode("utf-8"), "# 已完成\n")

                connection.request(
                    "GET",
                    "/api/sync/notes/%d" % int(unfinished["id"]),
                    headers={"Authorization": "Bearer " + token},
                )
                unfinished_response = connection.getresponse()
                self.assertEqual(unfinished_response.status, 404)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
