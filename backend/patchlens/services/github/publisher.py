"""Publish review findings to GitHub as inline comments plus a neutral check run.

Publication is deliberately conservative:

* only findings whose verification status is ``verified`` or
  ``needs_manual_verification`` are eligible (the pipeline enforces this too);
* severity is filtered by ``GITHUB_COMMENT_SEVERITIES`` and the number of comments
  is capped by ``GITHUB_MAX_COMMENTS_PER_REVIEW``;
* anchors must be verified against the new side of the diff and be safe relative
  paths when ``GITHUB_REQUIRE_VERIFIED_ANCHORS`` is on;
* the review event is always ``COMMENT`` - PatchLens never approves or blocks a
  merge, and the check run is always ``neutral`` for the same reason;
* comment text is redacted and never includes raw code excerpts, so a hard-coded
  secret found by a check is not copied into a public pull request comment.

Every outcome is persisted per finding. A failed API call marks findings failed
with the provider's bounded message; it does not pretend anything was published.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from patchlens.core.config import Settings
from patchlens.core.enums import PublishState, Severity, VerificationStatus
from patchlens.core.errors import ProviderError
from patchlens.core.logging import get_logger, log_extra, redact
from patchlens.core.security import is_safe_relative_path
from patchlens.db.models.repository import Repository
from patchlens.db.models.review import Finding, Review
from patchlens.services.github.client import GitHubClient, build_github_client
from patchlens.services.review.pipeline import eligible_for_publication

logger = get_logger(__name__)

MAX_COMMENT_BODY = 6_000
MAX_SUGGESTION_BODY = 2_000

_VERIFICATION_LABEL = {
    VerificationStatus.VERIFIED.value: "verified by static analysis",
    VerificationStatus.NEEDS_MANUAL_VERIFICATION.value: "needs manual verification",
}


class GitHubPublisher:
    """``Publisher`` implementation for GitHub pull requests."""

    def __init__(self, client: GitHubClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    def publish(self, session: Session, review: Review, findings: list[Finding]) -> dict[str, Any]:
        if review.publish_state == PublishState.PUBLISHED.value:
            return {
                "state": PublishState.PUBLISHED.value,
                "detail": "review was already published; no second request was made",
                "published": 0,
                "eligible": len(findings),
                "idempotent": True,
            }

        pull_request = review.pull_request
        if pull_request is None:
            return self._skip(findings, "review is not attached to a pull request")
        repository = session.get(Repository, review.repository_id)
        installation_id = repository.installation_id if repository is not None else None
        if not installation_id:
            return self._skip(
                findings,
                "repository has no GitHub App installation id; webhook deliveries set it",
            )

        allowed_severities = {severity.lower() for severity in self.settings.github_comment_severities}
        selected: list[Finding] = []
        skipped: list[Finding] = []
        for finding in findings:
            reason: str | None = None
            if not eligible_for_publication(finding):
                reason = f"verification status '{finding.verification_status}' is not publishable"
            elif finding.severity.lower() not in allowed_severities:
                reason = (
                    f"severity '{finding.severity}' is outside GITHUB_COMMENT_SEVERITIES "
                    f"({sorted(allowed_severities)})"
                )
            elif self.settings.github_require_verified_anchors and not finding.line_verified:
                reason = "anchor line was not verified against the new side of the diff"
            elif not is_safe_relative_path(finding.path):
                reason = "path is not a safe repository-relative path"
            if reason is not None:
                _mark(finding, PublishState.SKIPPED, reason)
                skipped.append(finding)
            else:
                selected.append(finding)

        # Deterministic order, then the configured cap. A finding dropped by the cap
        # is recorded as skipped rather than silently omitted.
        selected.sort(
            key=lambda finding: (
                -finding.severity_rank,
                finding.path,
                finding.start_line,
                finding.rule_id,
            )
        )
        cap = self.settings.github_max_comments_per_review
        for finding in selected[cap:]:
            _mark(
                finding,
                PublishState.SKIPPED,
                f"comment limit GITHUB_MAX_COMMENTS_PER_REVIEW={cap} reached",
            )
            skipped.append(finding)
        selected = selected[:cap]

        if not selected:
            detail = "no eligible findings matched the publication policy"
            if skipped:
                detail += f" ({len(skipped)} skipped)"
            return self._skip(findings, detail)

        body = _review_body(review, selected, skipped)
        comments = [
            {
                "path": finding.path,
                "line": finding.start_line,
                "side": "RIGHT",
                "body": _comment_body(review, finding),
            }
            for finding in selected
        ]
        try:
            result = self.client.create_review(
                repository=pull_request.repository.full_name
                if pull_request.repository is not None
                else str(repository.full_name if repository is not None else ""),
                number=pull_request.number,
                installation_id=installation_id,
                commit_id=review.commit_sha,
                body=body,
                comments=comments,
            )
        except Exception as exc:
            detail = redact(exc)[:1_000]
            for finding in selected:
                _mark(finding, PublishState.FAILED, detail)
            logger.warning(
                "github review publication failed",
                extra=log_extra(review_id=review.id, findings=len(selected)),
            )
            raise

        published_at = datetime.now(UTC)
        review_url = _optional_str(result.get("html_url")) or pull_request.html_url
        for finding in selected:
            _mark(
                finding,
                PublishState.PUBLISHED,
                "published in the pull request review",
                url=review_url,
                published_at=published_at,
            )

        check_run = self._create_check_run(
            review,
            selected,
            repository_full_name=str(repository.full_name if repository is not None else ""),
            installation_id=installation_id,
        )
        logger.info(
            "github review published",
            extra=log_extra(
                review_id=review.id,
                comments=len(selected),
                check_run=check_run.get("state"),
            ),
        )
        return {
            "state": PublishState.PUBLISHED.value,
            "detail": f"published {len(selected)} inline comment(s)",
            "published": len(selected),
            "eligible": len(findings),
            "skipped": len(skipped),
            "review_url": review_url,
            "check_run": check_run,
            "provider": "github",
        }

    # ------------------------------------------------------------------ helpers
    def _skip(self, findings: list[Finding], detail: str) -> dict[str, Any]:
        for finding in findings:
            if finding.publish_state == PublishState.NOT_PUBLISHED.value:
                _mark(finding, PublishState.SKIPPED, detail)
        return {
            "state": PublishState.SKIPPED.value,
            "detail": detail,
            "published": 0,
            "eligible": len(findings),
            "provider": "github",
        }

    def _create_check_run(
        self,
        review: Review,
        selected: list[Finding],
        *,
        repository_full_name: str,
        installation_id: str,
    ) -> dict[str, Any]:
        """Create a neutral check run with annotations; never a pass/fail verdict."""
        if not repository_full_name:
            return {"state": "failed", "detail": "repository name is missing"}
        annotations = [
            {
                "path": finding.path,
                "start_line": finding.start_line,
                "end_line": max(finding.end_line, finding.start_line),
                "annotation_level": _annotation_level(finding),
                "title": redact(finding.title)[:255],
                "message": redact(finding.explanation)[:1_000],
            }
            for finding in selected
        ]
        summary = (
            f"{len(selected)} finding(s) published. PatchLens never approves or blocks a merge; "
            "this check is informational."
        )
        try:
            result = self.client.create_check_run(
                repository=repository_full_name,
                installation_id=installation_id,
                name="PatchLens",
                head_sha=review.commit_sha,
                conclusion="neutral",
                title="PatchLens review",
                summary=summary,
                annotations=annotations,
            )
        except ProviderError as exc:
            logger.warning(
                "github check run failed",
                extra=log_extra(review_id=review.id, error=redact(exc)[:300]),
            )
            return {"state": "failed", "detail": redact(exc)[:500]}
        except Exception as exc:
            # The comments are already published. A malformed check-run payload or
            # an unexpected transport failure is recorded as a partial publication,
            # not allowed to rewrite the review as a failed publication.
            logger.exception("github check run failed unexpectedly", extra=log_extra(review_id=review.id))
            return {"state": "failed", "detail": redact(exc)[:500]}
        return {
            "state": "created",
            "url": _optional_str(result.get("html_url")),
            "annotations": len(annotations),
        }


def build_publisher(settings: Settings, *, client: GitHubClient | None = None) -> GitHubPublisher | None:
    """Return a publisher only when publication is explicitly enabled and configured.

    ``None`` is not an error: the pipeline records publication as skipped with the
    reason. This keeps a local checkout or a credential-less demo working.
    """
    if not settings.github_publish_enabled or not settings.github_configured:
        return None
    return GitHubPublisher(client or build_github_client(settings), settings)


def _comment_body(review: Review, finding: Finding) -> str:
    label = _VERIFICATION_LABEL.get(finding.verification_status, finding.verification_status)
    lines = [
        f"**{redact(finding.title).strip()}**",
        "",
        f"`{finding.rule_id}` · {finding.category} · {finding.severity} · {label}",
        "",
        redact(finding.explanation).strip()[:MAX_COMMENT_BODY],
    ]
    if finding.suggestion:
        lines.extend(
            [
                "",
                "**Suggested fix**",
                redact(finding.suggestion).strip()[:MAX_SUGGESTION_BODY],
            ]
        )
    provider_note = "Mock provider output" if review.is_mock_provider else f"Provider: {review.llm_provider}"
    lines.extend(
        [
            "",
            f"_PatchLens · {provider_note} · finding `{finding.id}`. No raw code excerpt is included._",
        ]
    )
    return "\n".join(lines)[:65_000]


def _review_body(review: Review, selected: list[Finding], skipped: list[Finding]) -> str:
    verified = sum(
        1 for finding in selected if finding.verification_status == VerificationStatus.VERIFIED.value
    )
    manual = len(selected) - verified
    lines = [
        "## PatchLens review",
        "",
        f"Reviewed commit `{review.commit_sha[:12]}`. "
        f"**{len(selected)} finding(s)** are included inline: "
        f"{verified} verified, {manual} needing manual verification.",
    ]
    if skipped:
        lines.append(f"{len(skipped)} finding(s) were not published by policy (severity, anchor or limit).")
    if review.is_mock_provider:
        lines.extend(
            [
                "",
                "This review was produced by the **deterministic mock provider**, not a neural "
                "model. It is suitable for testing the integration, not for judging code quality.",
            ]
        )
    lines.extend(
        [
            "",
            "PatchLens never approves, requests changes, or blocks a merge; a human decides.",
        ]
    )
    return "\n".join(lines)


def _mark(
    finding: Finding,
    state: PublishState,
    detail: str,
    *,
    url: str | None = None,
    published_at: datetime | None = None,
) -> None:
    finding.publish_state = state.value
    finding.publish_detail = redact(detail)[:1_000]
    if url:
        finding.published_comment_url = url
    if published_at is not None:
        finding.published_at = published_at


def _annotation_level(finding: Finding) -> str:
    if finding.severity in {Severity.CRITICAL.value, Severity.HIGH.value} and (
        finding.verification_status == VerificationStatus.VERIFIED.value
    ):
        return "failure"
    return "warning"


def _optional_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


__all__ = ["GitHubPublisher", "build_publisher"]
