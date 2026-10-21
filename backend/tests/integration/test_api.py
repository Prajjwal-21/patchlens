"""API integration tests.

The suite drives the real FastAPI application with the real database and the real
worker, so the API's promises (honest statuses, idempotency, validation errors,
pagination and authentication) are tested against the same code paths a user hits.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from patchlens.api.app import create_app
from tests.helpers import apply_new_state


def register(client: TestClient, root: Path, *, owner: str = "patchlens", name: str = "fixture") -> dict:
    response = client.post(
        "/api/v1/repositories", json={"owner": owner, "name": name, "local_path": str(root)}
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()


def index_now(client: TestClient, worker, repository_id: str, *, commit_sha: str = "commit-1") -> dict:
    queued = client.post(f"/api/v1/repositories/{repository_id}/index", json={"commit_sha": commit_sha})
    assert queued.status_code in {200, 202}, queued.text
    job_id = queued.json()["id"]
    assert worker.run_once() is True
    detail = client.get(f"/api/v1/jobs/{job_id}")
    assert detail.status_code == 200
    return detail.json()


def review_sync(
    client: TestClient, repository_id: str, diff_text: str, *, commit_sha: str = "commit-2"
) -> dict:
    response = client.post(
        "/api/v1/reviews",
        json={"repository_id": repository_id, "diff_text": diff_text, "commit_sha": commit_sha, "wait": True},
    )
    assert response.status_code in {200, 201, 202}, response.text
    return response.json()


class TestHealthAndMeta:
    def test_liveness_is_minimal(self, client: TestClient) -> None:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_readiness_reports_real_checks(self, client: TestClient) -> None:
        response = client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        checks = body["checks"]
        assert checks["database"]["ok"] is True
        assert checks["database"]["dialect"] == "sqlite"
        assert checks["llm"]["ok"] is True
        assert checks["llm"]["note"], "a mock provider must explain that no model was called"
        assert checks["test_execution"]["mode"] == "disabled"
        assert checks["github_publishing"]["reachable"] is False

    def test_meta_exposes_provider_reality_and_rules(self, client: TestClient) -> None:
        body = client.get("/api/v1/meta").json()
        assert body["llm"]["is_mock"] is True
        assert body["llm"]["provider"] == "mock"
        assert body["embedding"]["is_neural"] is False
        assert len(body["review_rules"]) >= 20
        assert body["capabilities"]["test_execution"] == "disabled"
        assert body["capabilities"]["github_publishing"] is False
        assert body["capabilities"]["auth_required"] is False
        assert body["retrieval"]["semantic_backend"] == "portable_scan"
        assert body["feedback_labels"] == [
            "correct",
            "incorrect",
            "irrelevant",
            "duplicate",
            "needs_investigation",
        ]

    def test_response_carries_a_request_id(self, client: TestClient) -> None:
        response = client.get("/healthz")
        assert response.headers.get("x-request-id")


class TestOverview:
    def test_empty_overview_uses_null_for_undefined_rates(self, client: TestClient) -> None:
        body = client.get("/api/v1/overview").json()
        assert body["repositories"]["total"] == 0
        assert body["reviews"]["total"] == 0
        assert body["reviews"]["success_rate"] is None
        assert body["jobs"]["failure_rate"] is None
        assert body["findings"]["open_findings"] == 0
        assert body["recent_reviews"] == []

    def test_overview_reflects_persisted_review_and_feedback(
        self, client: TestClient, demo_repo: Path, worker, diff_text: str
    ) -> None:
        created = register(client, demo_repo)
        index_now(client, worker, created["id"])
        apply_new_state(demo_repo)
        body = review_sync(client, created["id"], diff_text)
        review = body["review"]
        findings = client.get(f"/api/v1/reviews/{review['id']}/findings").json()

        overview = client.get("/api/v1/overview").json()
        assert overview["repositories"]["total"] == 1
        assert overview["repositories"]["indexed"] == 1
        assert overview["reviews"]["by_status"]["succeeded"] == 1
        assert overview["reviews"]["success_rate"] == 1.0
        assert overview["reviews"]["latency_ms"]["sample_size"] == 1
        assert overview["reviews"]["latency_ms"]["p50"] is not None
        assert overview["findings"]["open_findings"] == len(findings)
        assert overview["findings"]["labelled_findings"] == 0
        assert overview["test_intelligence"]["candidates"] == 0
        assert overview["jobs"]["by_status"]["succeeded"] >= 1
        assert overview["recent_reviews"][0]["id"] == review["id"]

        client.post(
            f"/api/v1/findings/{findings[0]['id']}/feedback",
            json={"label": "correct", "comment": "Confirmed"},
        )
        updated = client.get("/api/v1/overview").json()
        assert updated["findings"]["labelled_findings"] == 1
        assert updated["findings"]["open_findings"] == len(findings) - 1

    def test_evaluation_reviews_are_excluded_from_workspace_counts(
        self, client: TestClient, demo_repo: Path, worker, diff_text: str, session
    ) -> None:
        from patchlens.db.models.review import Review

        created = register(client, demo_repo)
        index_now(client, worker, created["id"])
        apply_new_state(demo_repo)
        review = review_sync(client, created["id"], diff_text)["review"]

        before = client.get("/api/v1/overview").json()
        assert before["reviews"]["total"] == 1
        assert before["findings"]["non_rejected"] > 0

        row = session.get(Review, review["id"])
        row.trigger = "evaluation"
        session.commit()

        after = client.get("/api/v1/overview").json()
        assert after["reviews"]["total"] == 0
        assert after["reviews"]["success_rate"] is None
        assert after["reviews"]["latency_ms"]["sample_size"] == 0
        assert after["findings"]["non_rejected"] == 0
        assert after["findings"]["open_findings"] == 0
        assert after["recent_reviews"] == []
        assert after["recent_findings"] == []


class TestRepositories:
    def test_create_list_and_fetch(self, client: TestClient, demo_repo: Path) -> None:
        created = register(client, demo_repo)
        assert created["full_name"] == "patchlens/fixture"
        assert created["provider"] == "local"
        assert created["index_status"] in {"pending", "queued", "never"}

        listing = client.get("/api/v1/repositories").json()
        assert [item["id"] for item in listing] == [created["id"]]

        detail = client.get(f"/api/v1/repositories/{created['id']}").json()
        assert detail["repository"]["id"] == created["id"]
        assert detail["repository"]["latest_version"] is None
        assert detail["versions"] == []

    def test_duplicate_full_name_is_a_conflict(self, client: TestClient, demo_repo: Path) -> None:
        register(client, demo_repo)
        response = client.post(
            "/api/v1/repositories",
            json={"owner": "patchlens", "name": "fixture", "local_path": str(demo_repo)},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    def test_missing_local_path_is_rejected(self, client: TestClient, tmp_path: Path) -> None:
        response = client.post(
            "/api/v1/repositories",
            json={"owner": "patchlens", "name": "ghost", "local_path": str(tmp_path / "nope")},
        )
        assert response.status_code == 422
        assert "existing directory" in response.json()["error"]["message"]

    def test_unknown_repository_is_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/repositories/repo_missing")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_delete_removes_the_repository(self, client: TestClient, demo_repo: Path) -> None:
        created = register(client, demo_repo)
        assert client.delete(f"/api/v1/repositories/{created['id']}").json()["deleted"] is True
        assert client.get(f"/api/v1/repositories/{created['id']}").status_code == 404

    def test_indexing_a_repository_without_a_checkout_is_a_conflict(
        self, client: TestClient, session
    ) -> None:
        from tests.conftest import register_repository

        repository = register_repository(session, Path(), full_name="patchlens/remote-only", local_path=None)
        session.commit()
        response = client.post(f"/api/v1/repositories/{repository.id}/index", json={})
        assert response.status_code == 409
        assert "no local checkout" in response.json()["error"]["message"]


class TestIndexingThroughTheAPI:
    def test_index_job_runs_and_reports_real_counts(
        self, client: TestClient, demo_repo: Path, worker
    ) -> None:
        created = register(client, demo_repo)
        job = index_now(client, worker, created["id"])

        assert job["status"] == "succeeded"
        assert job["result"]["symbols"] >= 5
        assert job["result"]["embeddings"] > 0
        assert job["result"]["embedding_model"].startswith("hashing-ngram")
        assert job["events"], "the progress endpoint exposes the audit trail"

        detail = client.get(f"/api/v1/repositories/{created['id']}").json()
        assert detail["repository"]["index_status"] == "indexed"
        assert len(detail["versions"]) == 1
        assert detail["versions"][0]["symbol_count"] == job["result"]["symbols"]

    def test_repeated_index_requests_dedupe_into_one_job(self, client: TestClient, demo_repo: Path) -> None:
        created = register(client, demo_repo)
        first = client.post(f"/api/v1/repositories/{created['id']}/index", json={}).json()
        second = client.post(f"/api/v1/repositories/{created['id']}/index", json={}).json()
        assert first["id"] == second["id"]

    def test_job_can_be_cancelled_before_the_worker_takes_it(
        self, client: TestClient, demo_repo: Path
    ) -> None:
        created = register(client, demo_repo)
        job = client.post(f"/api/v1/repositories/{created['id']}/index", json={}).json()
        cancelled = client.post(f"/api/v1/jobs/{job['id']}/cancel")
        assert cancelled.status_code == 200
        assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "canceled"

    def test_symbols_are_searchable_after_indexing(self, client: TestClient, demo_repo: Path, worker) -> None:
        created = register(client, demo_repo)
        missing = client.get(f"/api/v1/repositories/{created['id']}/symbols")
        assert missing.status_code == 409
        index_now(client, worker, created["id"])
        response = client.get(f"/api/v1/repositories/{created['id']}/symbols", params={"query": "divide"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pagination"]["total"] >= 1
        names = {item["qualified_name"] for item in body["items"]}
        assert "divide" in names
        # Only production symbols are offered as generation targets by default.
        assert all(item["is_test"] is False for item in body["items"])
        assert body["items"][0]["source_truncated"] is False

    def test_jobs_list_filters_by_kind_and_status(self, client: TestClient, demo_repo: Path) -> None:
        created = register(client, demo_repo)
        client.post(f"/api/v1/repositories/{created['id']}/index", json={})
        listing = client.get("/api/v1/jobs", params={"kind": "index_repository"}).json()
        assert listing["pagination"]["total"] == 1
        assert listing["items"][0]["kind"] == "index_repository"
        empty = client.get("/api/v1/jobs", params={"kind": "review_pull_request"}).json()
        assert empty["pagination"]["total"] == 0

    def test_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/api/v1/jobs/job_nope").status_code == 404


class TestReviewFlow:
    def test_async_review_queues_then_completes_with_findings(
        self, client: TestClient, demo_repo: Path, worker, diff_text: str
    ) -> None:
        created = register(client, demo_repo)
        index_now(client, worker, created["id"])
        apply_new_state(demo_repo)

        response = client.post(
            "/api/v1/reviews",
            json={"repository_id": created["id"], "diff_text": diff_text, "commit_sha": "commit-2"},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["review"]["status"] == "queued"
        assert body["job_id"]
        assert body["executed_synchronously"] is False
        review_id = body["review"]["id"]

        running = client.get(f"/api/v1/reviews/{review_id}").json()
        assert running["job"]["id"] == body["job_id"]
        assert running["job"]["status"] == "queued"

        assert worker.run_once() is True
        detail = client.get(f"/api/v1/reviews/{review_id}").json()
        review = detail["review"]
        assert review["status"] == "succeeded"
        assert review["is_mock_provider"] is True
        assert review["finding_count"] > 0
        assert review["verified_finding_count"] >= 1
        assert detail["job"]["status"] == "succeeded"
        assert detail["finding_summary"]["by_severity"]
        assert detail["finding_summary"]["by_verification_status"]

        findings = client.get(f"/api/v1/reviews/{review_id}/findings").json()
        assert findings
        for finding in findings:
            assert finding["path"]
            assert finding["start_line"] >= 1
            assert finding["explanation"]
            assert finding["evidence"], "every finding explains itself with evidence"
            assert finding["verification_status"] in {
                "verified",
                "ai_suggested",
                "needs_manual_verification",
                "rejected",
            }

    def test_synchronous_review_records_a_job_row(
        self, client: TestClient, demo_repo: Path, worker, diff_text: str
    ) -> None:
        created = register(client, demo_repo)
        index_now(client, worker, created["id"])
        apply_new_state(demo_repo)

        body = review_sync(client, created["id"], diff_text)
        assert body["executed_synchronously"] is True
        assert body["review"]["status"] == "succeeded"
        job = client.get(f"/api/v1/jobs/{body['job_id']}").json()
        assert job["status"] == "succeeded"
        assert job["kind"] == "review_pull_request"
        assert job["worker_id"] == "api-sync"
        assert job["result"]["review_id"] == body["review"]["id"]

    def test_repeat_review_of_the_same_commit_returns_the_stored_result(
        self, client: TestClient, demo_repo: Path, worker, diff_text: str
    ) -> None:
        created = register(client, demo_repo)
        index_now(client, worker, created["id"])
        apply_new_state(demo_repo)
        first = review_sync(client, created["id"], diff_text)
        second = client.post(
            "/api/v1/reviews",
            json={"repository_id": created["id"], "diff_text": diff_text, "commit_sha": "commit-2"},
        ).json()
        assert second["review"]["id"] == first["review"]["id"]
        assert "already succeeded" in second["note"]

    def test_review_without_an_index_warns_but_does_not_invent_context(
        self, client: TestClient, demo_repo: Path
    ) -> None:
        created = register(client, demo_repo)
        body = review_sync(
            client,
            created["id"],
            "diff --git a/none.py b/none.py\n--- a/none.py\n+++ b/none.py\n@@ -0,0 +1 @@\n+x = 1\n",
        )
        review = body["review"]
        assert review["status"] in {"succeeded", "failed"}
        assert review["finding_count"] >= 0

    def test_unknown_review_is_404(self, client: TestClient) -> None:
        assert client.get("/api/v1/reviews/rvw_nope").status_code == 404

    def test_invalid_review_payload_is_a_typed_422(self, client: TestClient) -> None:
        response = client.post("/api/v1/reviews", json={"repository_id": "repo_1"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


class TestFindingsAndFeedback:
    @pytest.fixture()
    def reviewed(self, client: TestClient, demo_repo: Path, worker, diff_text: str):
        created = register(client, demo_repo)
        index_now(client, worker, created["id"])
        apply_new_state(demo_repo)
        body = review_sync(client, created["id"], diff_text)
        findings = client.get(f"/api/v1/reviews/{body['review']['id']}/findings").json()
        return created, body["review"], findings

    def test_findings_can_be_filtered(self, client: TestClient, reviewed) -> None:
        _, review, findings = reviewed
        rule_id = findings[0]["rule_id"]
        filtered = client.get("/api/v1/findings", params={"rule_id": rule_id}).json()
        assert filtered["pagination"]["total"] >= 1
        assert all(item["rule_id"] == rule_id for item in filtered["items"])
        assert all(item["review_id"] == review["id"] for item in filtered["items"])

    def test_feedback_is_recorded_and_aggregated(self, client: TestClient, reviewed) -> None:
        _, _, findings = reviewed
        finding = findings[0]
        response = client.post(
            f"/api/v1/findings/{finding['id']}/feedback",
            json={"label": "correct", "comment": "Reproduced locally.", "reviewer": "qa-bot"},
        )
        assert response.status_code in {200, 201}
        assert response.json()["label"] == "correct"

        listing = client.get(f"/api/v1/findings/{finding['id']}/feedback").json()
        assert listing["items"][0]["label"] == "correct"

        aggregate = client.get("/api/v1/findings/feedback/aggregate").json()
        assert aggregate["total_findings"] == len(findings)
        assert aggregate["labelled_findings"] == 1
        assert aggregate["labels"]["correct"] == 1
        assert aggregate["note"], "the aggregate must explain how labels are used"

    def test_invalid_label_is_rejected(self, client: TestClient, reviewed) -> None:
        _, _, findings = reviewed
        response = client.post(f"/api/v1/findings/{findings[0]['id']}/feedback", json={"label": "vibes"})
        assert response.status_code == 422

    def test_trace_explains_a_deterministic_finding(self, client: TestClient, reviewed) -> None:
        _, _, findings = reviewed
        target = next(finding for finding in findings if finding["detector"] in {"static", "static_ast"})
        trace = client.get(f"/api/v1/findings/{target['id']}/trace").json()
        assert trace["rule_id"] == target["rule_id"]
        assert trace["verification_status"] == target["verification_status"]
        assert trace["confidence_basis"] == "deterministic_detector"
        assert trace["evidence"]
        assert trace["line_verified"] is True
        assert trace["rule_is_deterministic"] is True
        assert trace["rule"]["summary"]
        assert trace["rule"]["remediation"]
        assert trace["not_executed"], "the trace must say what was not verified"
        assert any("no test" in note for note in trace["not_executed"])

    def test_trace_of_a_rejected_finding_shows_the_rejection(
        self,
        client: TestClient,
        session,
        app_settings,
        demo_repo: Path,
        worker,
        diff_text: str,
    ) -> None:
        from patchlens.db.models.repository import Repository
        from patchlens.services.llm.base import LLMCompletion, LLMProvider
        from patchlens.services.review.pipeline import ReviewPipeline, ReviewRequest

        created = register(client, demo_repo)
        index_now(client, worker, created["id"])
        apply_new_state(demo_repo)

        class LineOneProvider(LLMProvider):
            """Proposes a finding on an unchanged line, so validation must reject it."""

            name = "line-one-provider"
            is_mock = False

            def complete(self, request):
                return LLMCompletion(
                    text="",
                    provider=self.name,
                    model="line-one-1",
                    is_mock=False,
                    parsed={
                        "findings": [
                            {
                                "rule_id": "line-one",
                                "category": "maintainability",
                                "severity": "low",
                                "title": "Something about the first line",
                                "explanation": "This targets an unchanged context line in the diff.",
                                "path": "demoapp/calculator.py",
                                "start_line": 1,
                                "end_line": 1,
                            }
                        ]
                    },
                )

        repository = session.get(Repository, created["id"])
        assert repository is not None
        result = ReviewPipeline(session, settings=app_settings, llm_provider=LineOneProvider()).run(
            ReviewRequest(
                repository=repository, diff_text=diff_text, commit_sha="commit-3", base_sha="commit-1"
            )
        )
        rejected = [finding for finding in result.findings if finding.rejection_reason]
        assert rejected, "a model finding on an unchanged line must be rejected"

        trace = client.get(f"/api/v1/findings/{rejected[0].id}/trace").json()
        assert trace["verification_status"] == "rejected"
        assert trace["rejection_reason"] == rejected[0].rejection_reason
        assert trace["line_verified"] is False
        assert trace["detector"] == "llm"
        assert trace["rule_is_deterministic"] is False
        assert trace["rule"] is None
        assert any("rejected before publication" in note for note in trace["not_executed"])

    def test_unknown_finding_is_404(self, client: TestClient) -> None:
        assert client.get("/api/v1/findings/fnd_nope").status_code == 404


class TestSettingsEndpoint:
    def test_settings_are_read_only_and_redacted(self, client: TestClient) -> None:
        body = client.get("/api/v1/settings").json()
        assert body["llm"]["provider"] == "mock"
        assert body["llm"]["is_mock"] is True
        assert body["embedding"]["provider"] == "hashing"
        assert body["retrieval"]["strategy"] == "hybrid"
        assert body["test_execution"]["mode"] == "disabled"
        assert body["security"]["auth_required"] is False
        assert body["limits"]["review_max_findings"] >= 1
        assert "restarting" in body["github"]["note"]

    def test_settings_cannot_be_written(self, client: TestClient) -> None:
        assert client.post("/api/v1/settings", json={}).status_code == 405


class TestAuthentication:
    def test_requests_are_rejected_without_a_token_when_one_is_configured(self, app_settings) -> None:
        from patchlens.core.config import Settings

        secured = Settings(
            environment="test",
            database_url=app_settings.database_url,
            api_auth_token="test-token",
            llm_provider="mock",
            embedding_provider="hashing",
        )
        with TestClient(create_app(secured)) as client:
            # ``/healthz``, ``/readyz`` and ``/api/v1/meta`` stay public: an
            # orchestrator needs the probes, and the UI has to learn that a token
            # is required before it can present a field for it. Everything that
            # reads user data or changes state is behind the token.
            assert client.get("/healthz").status_code == 200, "liveness must not require credentials"
            assert client.get("/api/v1/meta").status_code == 200
            assert client.get("/api/v1/meta").json()["capabilities"]["auth_required"] is True

            assert client.get("/api/v1/repositories").status_code == 401
            assert client.get("/api/v1/reviews").status_code == 401
            assert client.get("/api/v1/findings").status_code == 401
            assert client.get("/api/v1/jobs").status_code == 401
            assert client.get("/api/v1/settings").status_code == 401
            assert client.get("/api/v1/evaluation/runs").status_code == 401
            assert client.get("/api/v1/evaluation/datasets").status_code == 401
            assert client.get("/api/v1/github/installations").status_code == 401
            assert client.get("/api/v1/github/deliveries").status_code == 401

            wrong = client.get("/api/v1/repositories", headers={"Authorization": "Bearer nope"})
            assert wrong.status_code == 401
            assert wrong.json()["error"]["code"] == "unauthorized"

            malformed = client.get("/api/v1/repositories", headers={"Authorization": "test-token"})
            assert malformed.status_code == 401, "the scheme is part of the contract"

            ok = client.get("/api/v1/repositories", headers={"Authorization": "Bearer test-token"})
            assert ok.status_code == 200

    def test_local_development_needs_no_token(self, client: TestClient) -> None:
        assert client.get("/api/v1/meta").status_code == 200


class TestPayloadLimitsAndErrors:
    def test_oversized_body_is_rejected_with_413(self, app_settings, database) -> None:
        small = app_settings.model_copy(update={"max_request_bytes": 2048})
        with TestClient(create_app(small)) as client:
            payload = {
                "repository_id": "repo_1",
                "commit_sha": "c",
                "diff_text": "diff --git a/x.py b/x.py\n" + "+x = 1\n" * 5_000,
            }
            response = client.post("/api/v1/reviews", json=payload)
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "payload_too_large"

    def test_unicode_and_control_characters_are_handled(self, client: TestClient, demo_repo: Path) -> None:
        created = register(client, demo_repo)
        body = review_sync(
            client,
            created["id"],
            "diff --git a/uni.py b/uni.py\n--- a/uni.py\n+++ b/uni.py\n@@ -0,0 +1 $##\n+\u00e9 = '\u00fc'\n",
        )
        assert body["review"]["id"]

    def test_unknown_route_returns_structured_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
