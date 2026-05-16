from pathlib import Path

import pytest

from app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.config["TESTING"] = True
    app.config["MEDIA_DIR"] = str(tmp_path)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TRANSLATION_BACKEND"] = "ollama"
    app.config["TRANSLATION_MODEL"] = "qwen2.5:14b"
    app.config["TARGET_LANGUAGE"] = "zh"
    with app.test_client() as client:
        yield client


class TestIndex:
    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"drama_subtitler" in resp.data or b"text/html" in resp.content_type.encode()


class TestApiMedia:
    def test_empty_media_list(self, client):
        resp = client.get("/api/media")
        assert resp.status_code == 200
        assert resp.get_json()["files"] == []

    def test_lists_media_files(self, client, tmp_path):
        (tmp_path / "ep01.mkv").write_text("fake")
        resp = client.get("/api/media")
        assert resp.status_code == 200
        assert "ep01.mkv" in resp.get_json()["files"]

    def test_ignores_non_media(self, client, tmp_path):
        (tmp_path / "notes.txt").write_text("hello")
        resp = client.get("/api/media")
        assert resp.get_json()["files"] == []


class TestFileDialog:
    def test_open_file_dialog_returns_selected_local_path(self, client, tmp_path, mocker):
        media = tmp_path / "ep01.mkv"
        media.write_bytes(b"fake")
        mocker.patch("app.routes._open_native_file_dialog", return_value=str(media))

        resp = client.post("/api/dialog/open")
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert data["path"] == str(media.resolve())

    def test_open_file_dialog_reports_cancel(self, client, mocker):
        mocker.patch("app.routes._open_native_file_dialog", return_value=None)

        resp = client.post("/api/dialog/open")
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is False
        assert data["canceled"] is True


class TestFinderShortcut:
    def test_finder_shortcut_status_on_macos(self, client, mocker):
        mocker.patch("app.routes.sys.platform", "darwin")
        mocker.patch("app.routes._finder_shortcut_app_path", return_value=Path("/tmp/Finder.app"))

        resp = client.get("/api/finder-shortcut")
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert data["supported"] is True
        assert data["installed"] is False

    def test_finder_shortcut_installs_on_macos(self, client, tmp_path, mocker):
        app_path = tmp_path / "Drama Subtitler Start Job.app"
        installer = tmp_path / "install.sh"
        installer.write_text("#!/bin/zsh\n", encoding="utf-8")

        def fake_run(*args, **kwargs):
            app_path.mkdir()
            mock = mocker.Mock()
            mock.returncode = 0
            mock.stdout = "Installed"
            mock.stderr = ""
            return mock

        mocker.patch("app.routes.sys.platform", "darwin")
        mocker.patch("app.routes._finder_shortcut_app_path", return_value=app_path)
        mocker.patch("app.routes._finder_shortcut_installer_path", return_value=installer)
        mock_run = mocker.patch("app.routes.subprocess.run", side_effect=fake_run)

        resp = client.post("/api/finder-shortcut")
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert data["installed"] is True
        mock_run.assert_called_once()


class TestApiConfig:
    def test_returns_config(self, client):
        resp = client.get("/api/config")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["target_language"] == "zh"
        assert data["translation"]["backend"] == "ollama"


class TestApiEstimate:
    def test_missing_params_returns_400(self, client):
        resp = client.get("/api/estimate")
        assert resp.status_code == 400

    def test_estimate_with_existing_orig_srt(self, client, tmp_path):
        media = tmp_path / "ep01.mp4"
        media.write_bytes(b"fake")
        srt = media.with_suffix(".orig.srt")
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n\n",
            encoding="utf-8",
        )
        resp = client.get(f"/api/estimate?local_path={media}")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["tokens"]["source"] == "orig_srt"
        assert data["tokens"]["segment_count"] == 1

    def test_estimate_requires_absolute_local_path(self, client):
        resp = client.get("/api/estimate?local_path=ep01.mp4")
        assert resp.status_code == 400


class TestApiJobs:
    def test_create_job_no_input_returns_400(self, client):
        resp = client.post("/api/jobs")
        assert resp.status_code == 400

    def test_create_job_with_local_path(self, client, tmp_path):
        media = tmp_path / "ep01.mkv"
        media.write_text("fake")
        resp = client.post(
            "/api/jobs",
            data={"local_path": str(media), "mode": "transcribe"},
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert "job_id" in data

    def test_create_job_translate_only_without_orig_srt_returns_400(self, client, tmp_path):
        (tmp_path / "ep01.mkv").write_text("fake")
        media = tmp_path / "ep01.mkv"
        resp = client.post(
            "/api/jobs",
            data={"local_path": str(media), "mode": "translate"},
        )
        assert resp.status_code == 400

    def test_create_job_translate_only_with_orig_srt(self, client, tmp_path):
        media = tmp_path / "ep01.mkv"
        media.write_text("fake")
        (tmp_path / "ep01.orig.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n\n",
            encoding="utf-8",
        )
        resp = client.post(
            "/api/jobs",
            data={"local_path": str(media), "mode": "translate"},
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True

    def test_create_job_rejects_upload_without_local_path(self, client, tmp_path):
        import io
        resp = client.post(
            "/api/jobs",
            data={
                "media_file": (io.BytesIO(b"fake video data"), "test_vid.mkv"),
                "mode": "transcribe",
            },
            content_type="multipart/form-data",
        )
        data = resp.get_json()
        assert resp.status_code == 400
        assert data["success"] is False
        assert not (tmp_path / "uploads" / "test_vid.mkv").exists()

    def test_list_jobs_includes_jobs_started_outside_page(self, client, tmp_path):
        from app.models.subtitle_pipeline import SubtitlePipeline

        media = tmp_path / "ep01.mkv"
        media.write_bytes(b"fake")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(SubtitlePipeline, "process", lambda *args, **kwargs: {"original_srt": "x"})
            create_resp = client.post(
                "/api/jobs",
                data={"local_path": str(media), "mode": "transcribe"},
            )

        job_id = create_resp.get_json()["job_id"]
        resp = client.get("/api/jobs")
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert any(job["job_id"] == job_id and job["media_file"] == "ep01.mkv" for job in data["jobs"])

    def test_job_status_not_found(self, client):
        resp = client.get("/api/jobs/no-such-id")
        assert resp.status_code == 404

    def test_cancel_job_not_found(self, client):
        resp = client.post("/api/jobs/no-such-id/cancel")
        assert resp.status_code == 404

    def test_download_not_ready(self, client):
        resp = client.get("/api/jobs/no-such-id/download/original")
        assert resp.status_code == 404


class TestApiTranslateJob:
    def test_translate_nonexistent_job(self, client):
        resp = client.post("/api/jobs/fake-id/translate")
        data = resp.get_json()
        assert resp.status_code == 404
        assert "Job not found" in data["message"]

    def test_translate_job_not_ready(self, client, tmp_path, mocker):
        from app.models.subtitle_pipeline import SubtitlePipeline

        media = tmp_path / "ep01.mkv"
        media.write_text("fake")

        # Create a job but mock process so it doesn't finish.
        mocker.patch.object(
            SubtitlePipeline, "process", side_effect=RuntimeError("boom")
        )
        resp = client.post(
            "/api/jobs",
            data={"local_path": str(media), "mode": "transcribe"},
        )
        job_id = resp.get_json()["job_id"]

        # Wait briefly for the thread to fail.
        import time
        for _ in range(50):
            status = client.get(f"/api/jobs/{job_id}").get_json()
            if status["status"] == "failed":
                break
            time.sleep(0.02)

        # Now try to translate a failed job.
        resp = client.post(f"/api/jobs/{job_id}/translate")
        data = resp.get_json()
        assert resp.status_code == 400
        assert "not ready" in data["message"]


class TestApiOpenRouter:
    def test_models_returns_list(self, client, mocker):
        mocker.patch(
            "app.routes._fetch_openrouter_pricing",
            return_value={
                "google/gemini-2.5-flash-lite": {
                    "prompt": 0.1 / 1e6,
                    "completion": 0.4 / 1e6,
                    "name": "Gemini Flash",
                    "context_length": 128000,
                    "created": 1700000000,
                }
            },
        )
        resp = client.get("/api/openrouter/models")
        data = resp.get_json()
        assert resp.status_code == 200
        models = data["models"]
        assert len(models) == 1
        assert models[0]["slug"] == "google/gemini-2.5-flash-lite"

    def test_pricing_endpoint(self, client, mocker):
        mocker.patch(
            "app.routes._fetch_openrouter_pricing",
            return_value={"a/b": {"prompt": 1e-6}},
        )
        resp = client.get("/api/openrouter/pricing")
        data = resp.get_json()
        assert resp.status_code == 200
        assert "pricing" in data


class TestLocalPathOnlyJobs:
    def test_upload_cjk_filename_is_not_accepted(self, client, tmp_path):
        import io
        resp = client.post(
            "/api/jobs",
            data={
                "media_file": (io.BytesIO(b"fake"), "さすらい署長.mkv"),
                "mode": "transcribe",
            },
            content_type="multipart/form-data",
        )
        data = resp.get_json()
        assert resp.status_code == 400
        assert data["success"] is False
        assert not (tmp_path / "uploads" / "さすらい署長.mkv").exists()

    def test_relative_local_path_returns_400(self, client):
        resp = client.post(
            "/api/jobs",
            data={"local_path": "episode.mkv", "mode": "transcribe"},
        )
        assert resp.status_code == 400


class TestApiSettings:
    def test_get_settings_returns_dict(self, client):
        resp = client.get("/api/settings")
        data = resp.get_json()
        assert resp.status_code == 200
        assert isinstance(data, dict)
        assert "openrouter_api_key" in data
        assert "deepseek_api_key" in data

    def test_post_settings_persists_and_updates_config(self, client, tmp_path, monkeypatch):
        import config as config_module
        settings_path = tmp_path / "settings.json"
        # Redirect settings file for this test
        monkeypatch.setattr(config_module, "_settings_path", lambda: settings_path)
        # Reset SETTINGS dict so the class sees empty settings
        monkeypatch.setattr(config_module, "SETTINGS", {})

        resp = client.post(
            "/api/settings",
            json={
                "GPU_BASE_URL": "http://192.168.1.100",
                "OPENROUTER_API_KEY": "sk-or-test",
                "TARGET_LANGUAGE": "en",
            },
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["settings"]["GPU_BASE_URL"] == "http://192.168.1.100"
        assert data["settings"]["OPENROUTER_API_KEY"] == "sk-or-test"
        assert data["settings"]["TARGET_LANGUAGE"] == "en"

        # Config should be updated immediately
        assert client.application.config["GPU_BASE_URL"] == "http://192.168.1.100"
        assert client.application.config["OPENROUTER_API_KEY"] == "sk-or-test"
        assert client.application.config["TARGET_LANGUAGE"] == "en"

    def test_post_settings_derives_urls_from_gpu_base_url(self, client, tmp_path, monkeypatch):
        import config as config_module
        settings_path = tmp_path / "settings.json"
        monkeypatch.setattr(config_module, "_settings_path", lambda: settings_path)
        monkeypatch.setattr(config_module, "SETTINGS", {})

        resp = client.post(
            "/api/settings",
            json={"GPU_BASE_URL": "http://gpu.local"},
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["settings"]["GPU_BASE_URL"] == "http://gpu.local"
        assert data["settings"]["REMOTE_WHISPER_BASE_URL"] == "http://gpu.local:5051"
        assert data["settings"]["OLLAMA_BASE_URL"] == "http://gpu.local:11434"

    def test_post_settings_ignores_unknown_keys(self, client):
        resp = client.post(
            "/api/settings",
            json={"UNKNOWN_KEY": "should_be_ignored", "TARGET_LANGUAGE": "ko"},
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert "UNKNOWN_KEY" not in data["settings"]
        assert data["settings"]["TARGET_LANGUAGE"] == "ko"

    def test_post_settings_replaces_blank_asr_model_with_default(self, client, tmp_path, monkeypatch):
        import config as config_module
        settings_path = tmp_path / "settings.json"
        monkeypatch.setattr(config_module, "_settings_path", lambda: settings_path)
        monkeypatch.setattr(config_module, "SETTINGS", {})

        resp = client.post(
            "/api/settings",
            json={"ASR_BACKEND": "faster-whisper", "ASR_MODEL": ""},
        )
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert data["settings"]["ASR_MODEL"] == "large-v3"
        assert client.application.config["ASR_MODEL"] == "large-v3"


class TestServeMediaFile:
    def test_serve_existing_media_file(self, client, tmp_path):
        video = tmp_path / "ep01.mkv"
        video.write_bytes(b"fake video")
        resp = client.get("/api/media/files/ep01.mkv")
        assert resp.status_code == 200
        assert resp.data == b"fake video"

    def test_serve_media_file_path_traversal_blocked(self, client):
        resp = client.get("/api/media/files/../../../etc/passwd")
        assert resp.status_code == 400

    def test_serve_missing_media_file_returns_404(self, client):
        resp = client.get("/api/media/files/nonexistent.mp4")
        assert resp.status_code == 404


class TestOpenJobMedia:
    def test_open_existing_media_uses_sidecar_bilingual_srt(
        self, client, tmp_path, mocker, monkeypatch
    ):
        video = tmp_path / "ep01.mkv"
        video.write_bytes(b"fake")
        bilingual = tmp_path / "ep01.bilingual.srt"
        bilingual.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n你好\n\n",
            encoding="utf-8",
        )

        monkeypatch.setattr("sys.platform", "darwin")
        mocker.patch("shutil.which", return_value="/opt/homebrew/bin/mpv")
        mock_popen = mocker.patch("subprocess.Popen")

        resp = client.post("/api/media/open", data={"selected_file": "ep01.mkv"})
        data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert data["subtitle"] == str(bilingual)
        cmd = mock_popen.call_args.args[0]
        assert cmd[0] == "/opt/homebrew/bin/mpv"
        assert "--sub-auto=no" in cmd
        assert f"--sub-file={bilingual}" in cmd

    def test_open_existing_media_rejects_invalid_path(self, client):
        resp = client.post("/api/media/open", data={"selected_file": "../ep01.mkv"})
        assert resp.status_code == 400

    def test_open_job_media_happy_path(self, client, tmp_path, mocker):
        from app.models.subtitle_pipeline import SubtitlePipeline

        video = tmp_path / "ep01.mkv"
        video.write_bytes(b"fake")
        (tmp_path / "ep01.orig.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n\n",
            encoding="utf-8",
        )

        mocker.patch.object(SubtitlePipeline, "process")
        resp = client.post(
            "/api/jobs",
            data={"local_path": str(video), "mode": "transcribe"},
        )
        job_id = resp.get_json()["job_id"]

        mock_popen = mocker.patch("subprocess.Popen")
        resp2 = client.post(f"/api/jobs/{job_id}/open")
        data = resp2.get_json()
        assert resp2.status_code == 200
        assert data["success"] is True
        mock_popen.assert_called_once()

    def test_open_job_media_prefers_mpv_with_clean_subtitle_options(
        self, client, tmp_path, mocker, monkeypatch
    ):
        from app.models.subtitle_pipeline import SubtitlePipeline

        video = tmp_path / "ep01.mkv"
        video.write_bytes(b"fake")
        bilingual = tmp_path / "ep01.bilingual.srt"
        bilingual.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n你好\n\n",
            encoding="utf-8",
        )

        monkeypatch.setattr("sys.platform", "darwin")
        mocker.patch("shutil.which", return_value="/opt/homebrew/bin/mpv")
        mocker.patch.object(SubtitlePipeline, "process")
        resp = client.post(
            "/api/jobs",
            data={"local_path": str(video), "mode": "transcribe"},
        )
        job_id = resp.get_json()["job_id"]

        mock_popen = mocker.patch("subprocess.Popen")
        resp2 = client.post(f"/api/jobs/{job_id}/open")

        assert resp2.status_code == 200
        cmd = mock_popen.call_args.args[0]
        assert cmd[0] == "/opt/homebrew/bin/mpv"
        assert "--sub-auto=no" in cmd
        assert f"--sub-file={bilingual}" in cmd

    def test_open_job_media_job_not_found(self, client):
        resp = client.post("/api/jobs/fake-id/open")
        assert resp.status_code == 404

    def test_open_job_media_missing_file(self, client, tmp_path, mocker):
        from app.models.subtitle_pipeline import SubtitlePipeline

        video = tmp_path / "ep01.mkv"
        video.write_bytes(b"fake")
        mocker.patch.object(SubtitlePipeline, "process")
        resp = client.post(
            "/api/jobs",
            data={"local_path": str(video), "mode": "transcribe"},
        )
        job_id = resp.get_json()["job_id"]

        # Delete the file after job creation
        video.unlink()
        resp2 = client.post(f"/api/jobs/{job_id}/open")
        assert resp2.status_code == 404
