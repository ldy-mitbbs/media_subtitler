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
        resp = client.get("/api/estimate?selected_file=ep01.mp4")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert data["tokens"]["source"] == "orig_srt"
        assert data["tokens"]["segment_count"] == 1

    def test_estimate_invalid_path_traversal(self, client):
        resp = client.get("/api/estimate?selected_file=../../../etc/passwd")
        assert resp.status_code == 400


class TestApiJobs:
    def test_create_job_no_input_returns_400(self, client):
        resp = client.post("/api/jobs")
        assert resp.status_code == 400

    def test_create_job_with_selected_file(self, client, tmp_path):
        (tmp_path / "ep01.mkv").write_text("fake")
        resp = client.post(
            "/api/jobs",
            data={"selected_file": "ep01.mkv", "mode": "transcribe"},
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True
        assert "job_id" in data

    def test_create_job_translate_only_without_orig_srt_returns_400(self, client, tmp_path):
        (tmp_path / "ep01.mkv").write_text("fake")
        resp = client.post(
            "/api/jobs",
            data={"selected_file": "ep01.mkv", "mode": "translate"},
        )
        assert resp.status_code == 400

    def test_create_job_translate_only_with_orig_srt(self, client, tmp_path):
        (tmp_path / "ep01.mkv").write_text("fake")
        (tmp_path / "ep01.orig.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhi\n\n",
            encoding="utf-8",
        )
        resp = client.post(
            "/api/jobs",
            data={"selected_file": "ep01.mkv", "mode": "translate"},
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["success"] is True

    def test_create_job_with_upload(self, client, tmp_path):
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
        assert resp.status_code == 200
        assert data["success"] is True
        assert (tmp_path / "uploads" / "test_vid.mkv").exists()

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
            data={"selected_file": "ep01.mkv", "mode": "transcribe"},
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


class TestSafeFilenameRoute:
    def test_upload_cjk_filename(self, client, tmp_path):
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
        assert resp.status_code == 200
        assert data["success"] is True
        assert (tmp_path / "uploads" / "さすらい署長.mkv").exists()

    def test_upload_bad_filename_returns_400(self, client):
        import io
        resp = client.post(
            "/api/jobs",
            data={
                "media_file": (io.BytesIO(b"fake"), ""),
                "mode": "transcribe",
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
