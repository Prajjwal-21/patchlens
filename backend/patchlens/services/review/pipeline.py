"""The review pipeline.

    diff text
      -> parsed diff
      -> symbol mapping (indexed snapshot + post-change file text)
      -> hybrid retrieval (context, related tests)
      -> deterministic checks (line + AST)
      -> LLM findings (labelled mock or configured provider)
      -> schema/location/evidence validation + dedupe
      -> persistence (accepted, needs-verification and rejected findings)
      -> optional publication hook

Invariants:

* every stored finding cites an added line of this diff that was checked against
  the diff text (``line_verified=True``) - or it is stored as ``rejected``;
* ``Review.is_mock_provider`` and each finding's ``provider``/``model`` reflect the
  provider that actually produced it; mock output is never marked ``verified``;
* stage transitions are committed as they happen so status polling is truthful;
* a model failure does not discard deterministic results; the review is marked
  failed with the reason and the partial findings remain auditable.
"""

from __future__ import annotations

import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from patchlens.core.config import Settings, get_settings
from patchlens.core.enums import (
    ConfidenceBasis,
    PublishState,
    RetrievalStrategy,
    ReviewStatus,
    VerificationStatus,
)
from patchlens.core.errors import ProviderError
from patchlens.core.logging import describe_exception, get_logger, log_extra
from patchlens.core.security import content_fingerprint
from patchlens.db.models.repository import Repository
from patchlens.db.models.review import Finding, Review
from patchlens.db.models.testing import TestCandidate
from patchlens.services.embedding.base import EmbeddingProvider
from patchlens.services.llm.base import LLMProvider
from patchlens.services.providers.factory import build_embedding_provider, build_llm_provider
from patchlens.services.retrieval.service import CodeRetriever, RetrievalBundle
from patchlens.services.review.checks import CheckRunResult, run_checks
from patchlens.services.review.checks_common import CheckContext
from patchlens.services.review.diff import parse_diff
from patchlens.services.review.prompting import build_review_request
from patchlens.services.review.symbol_mapping import (
    SymbolMapping,
    build_queries,
    map_changed_symbols,
)
from patchlens.services.review.types import FindingDraft, severity_rank
from patchlens.services.review.validation import (
    ValidationResult,
    dedupe_drafts,
    finding_fingerprint,
    validate_completion,
)

if TYPE_CHECKING:
    from patchlens.services.testing.generator import GenerationResult

logger = get_logger(__name__)

PIPELINE_VERSION = 1


class Publisher(Protocol):
    """Publication hook (GitHub comment creation lives in the GitHub service)."""

    def publish(self, session: Session, review: Review, findings: list[Finding]) -> dict[str, Any]:
        """Publish findings and return a summary dict; must not invent success."""


@dataclass(slots=True)
class ReviewRequest:
    """Everything needed to create and run one review."""

    repository: Repository
    diff_text: str
    commit_sha: str
    base_sha: str | None = None
    pull_request_id: str | None = None
    trigger: str = "manual"


@dataclass(slots=True)
class ReviewRunResult:
    review: Review
    findings: list[Finding] = field(default_factory=list)
    mapping: SymbolMapping | None = None
    retrieval: RetrievalBundle | None = None
    checks: CheckRunResult | None = None
    validation: ValidationResult | None = None
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    llm_error: str | None = None
    test_candidates: list[TestCandidate] = field(default_factory=list)
    test_generation: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted_count(self) -> int:
        return sum(
            1 for finding in self.findings if finding.verification_status != VerificationStatus.REJECTED.value
        )

    @property
    def verified_count(self) -> int:
        return sum(
            1 for finding in self.findings if finding.verification_status == VerificationStatus.VERIFIED.value
        )


class ReviewPipeline:
    """Runs the review stages for one repository/commit."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        llm_provider: LLMProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        retriever: CodeRetriever | None = None,
        publisher: Publisher | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._llm_provider = llm_provider
        self._embedding_provider = embedding_provider
        self._embedding_error: str | None = None
        self._retriever = retriever
        self.publisher = publisher

    @property
    def llm(self) -> LLMProvider:
        if self._llm_provider is None:
            self._llm_provider = build_llm_provider(self.settings)
        return self._llm_provider

    @property
    def embedding_provider(self) -> EmbeddingProvider | None:
        """Embedding provider for semantic retrieval, or ``None`` if unavailable.

        A missing embedding credential degrades retrieval to lexical search for
        this review; it is recorded as a warning rather than failing the review.
        """
        if self._embedding_provider is not None:
            return self._embedding_provider
        strategy = RetrievalStrategy(self.settings.retrieval_strategy)
        if strategy not in {
            RetrievalStrategy.SEMANTIC,
            RetrievalStrategy.HYBRID,
            RetrievalStrategy.HYBRID_RERANK,
        }:
            return None
        try:
            self._embedding_provider = build_embedding_provider(self.settings)
        except ProviderError as exc:
            self._embedding_error = exc.message
            logger.warning(
                "embedding provider unavailable; retrieval will be lexical only",
                extra=log_extra(**describe_exception(exc)),
            )
        return self._embedding_provider

    @property
    def retriever(self) -> CodeRetriever:
        if self._retriever is None:
            self._retriever = CodeRetriever(
                self.session, settings=self.settings, embedding_provider=self.embedding_provider
            )
        return self._retriever

    # ------------------------------------------------------------------ create
    def get_or_create_review(self, request: ReviewRequest) -> tuple[Review, bool]:
        """Return the review for this revision, creating it if needed.

        Idempotent for pull requests: ``(pull_request_id, commit_sha, strategy)``
        is unique. A completed review is returned unchanged so redelivered
        webhooks do not re-spend model calls.
        """
        diff_hash = content_fingerprint(request.diff_text, length=64)
        strategy = self.settings.retrieval_strategy
        existing: Review | None = None
        if request.pull_request_id:
            existing = self.session.scalar(
                select(Review).where(
                    Review.pull_request_id == request.pull_request_id,
                    Review.commit_sha == request.commit_sha,
                    Review.strategy == strategy,
                )
            )
        if existing is None:
            existing = self.session.scalar(
                select(Review)
                .where(
                    Review.repository_id == request.repository.id,
                    Review.commit_sha == request.commit_sha,
                    Review.strategy == strategy,
                    Review.diff_hash == diff_hash,
                )
                .order_by(Review.created_at.desc())
                .limit(1)
            )
        if existing is not None:
            return existing, False
        review = Review(
            repository_id=request.repository.id,
            pull_request_id=request.pull_request_id,
            commit_sha=request.commit_sha,
            base_sha=request.base_sha,
            strategy=strategy,
            trigger=request.trigger,
            diff_hash=diff_hash,
            status=ReviewStatus.QUEUED.value,
            stage="queued",
            progress=0,
            llm_provider=self.settings.llm_provider,
            llm_model=self.settings.llm_model
            if self.settings.llm_provider != "mock"
            else "deterministic-mock",
            is_mock_provider=self.settings.llm_is_mock,
        )
        self.session.add(review)
        self.session.flush()
        return review, True

    # --------------------------------------------------------------------- run
    def run(self, request: ReviewRequest) -> ReviewRunResult:
        """Execute (or resume) the review for ``request``."""
        review, _created = self.get_or_create_review(request)
        if review.status == ReviewStatus.SUCCEEDED.value:
            logger.info("review already completed; skipping", extra=log_extra(review_id=review.id))
            return ReviewRunResult(
                review=review,
                findings=list(review.findings),
                warnings=["review for this commit already succeeded; reusing the stored result"],
            )
        started = time.perf_counter()
        review.status = ReviewStatus.RUNNING.value
        review.started_at = review.started_at or datetime.now(UTC)
        review.error = None
        self._stage(review, "parsing_diff", 5)

        result = ReviewRunResult(review=review)
        try:
            parsed = parse_diff(request.diff_text)
            review.changed_files = len(parsed.files)
            review.changed_lines = parsed.added_lines + parsed.removed_lines
            self._stage(review, "mapping_symbols", 15)

            mapping = map_changed_symbols(
                self.session, repository=request.repository, diff=parsed, settings=self.settings
            )
            result.mapping = mapping
            review.repository_version_id = mapping.version_id
            self._stage(review, "retrieving_context", 30)

            queries = build_queries(mapping.changed_symbols, parsed)
            exclude = frozenset(
                symbol.symbol_id for symbol in mapping.changed_symbols if symbol.symbol_id is not None
            )
            bundle = self.retriever.retrieve(
                repository=request.repository,
                queries=queries or [request.repository.full_name],
                exclude_symbol_ids=exclude,
            )
            result.retrieval = bundle
            result.warnings.extend(bundle.warnings)
            if self._embedding_error is not None:
                result.warnings.append(
                    f"embeddings unavailable ({self._embedding_error}); retrieval ran without the semantic half"
                )
            review.retrieved_context_count = bundle.findings_context_count
            self._stage(review, "running_checks", 45)

            context = CheckContext(
                diff=parsed,
                settings=self.settings,
                file_texts=mapping.file_texts,
                changed_symbols=mapping.changed_symbols,
                test_matches=mapping.test_matches,
            )
            checks = run_checks(context)
            result.checks = checks
            deterministic = dedupe_drafts(checks.findings)
            self._stage(review, "generating_findings", 60)

            llm_drafts: list[FindingDraft] = []
            validation: ValidationResult | None = None
            llm_error: str | None = None
            completion_meta: dict[str, Any] = {}
            try:
                llm_request = build_review_request(
                    settings=self.settings,
                    repository_full_name=request.repository.full_name,
                    commit_sha=request.commit_sha,
                    diff=parsed,
                    changed_symbols=mapping.changed_symbols,
                    deterministic_findings=deterministic,
                    retrieved_context=[item.to_dict() for item in bundle.context],
                    related_tests=[item.to_dict() for item in bundle.related_tests],
                )
                completion = self.llm.complete(llm_request)
                validation = validate_completion(
                    completion,
                    diff=parsed,
                    deterministic_findings=deterministic,
                    changed_symbols=mapping.changed_symbols,
                    file_texts=mapping.file_texts,
                    max_findings=self.settings.review_max_findings,
                )
                llm_drafts = validation.accepted
                completion_meta = {
                    "provider": completion.provider,
                    "model": completion.model,
                    "is_mock": completion.is_mock,
                    "finish_reason": completion.finish_reason,
                    "latency_ms": completion.latency_ms,
                    "http_attempts": completion.http_attempts,
                    "usage": completion.usage.to_dict(),
                }
                if validation.parse_error:
                    llm_error = f"model response rejected: {validation.parse_error}"
            except ProviderError as exc:
                llm_error = f"model provider failed: {exc.message}"
                logger.warning(
                    "review continued without model findings",
                    extra=log_extra(review_id=review.id, **describe_exception(exc)),
                )
            self._stage(review, "validating_findings", 80)

            result.validation = validation
            result.llm_error = llm_error
            drafts = [*deterministic, *llm_drafts]
            self._stage(review, "persisting_findings", 90)
            findings = self._persist_findings(
                review,
                drafts=drafts,
                rejected=validation.rejected if validation is not None else [],
                mapping=mapping,
                bundle=bundle,
                request=request,
            )
            result.findings = findings

            review.finding_count = sum(
                1
                for finding in findings
                if finding.verification_status
                in {
                    VerificationStatus.VERIFIED.value,
                    VerificationStatus.NEEDS_MANUAL_VERIFICATION.value,
                    VerificationStatus.AI_SUGGESTED.value,
                }
            )
            review.verified_finding_count = sum(
                1 for finding in findings if finding.verification_status == VerificationStatus.VERIFIED.value
            )
            review.llm_provider = completion_meta.get("provider", self.settings.llm_provider)
            review.llm_model = completion_meta.get("model", self.settings.llm_model)
            review.is_mock_provider = bool(completion_meta.get("is_mock", self.settings.llm_is_mock))
            review.usage = completion_meta.get("usage", {})

            self._stage(review, "generating_tests", 92)
            generation, generation_summary = self._generate_tests(
                review, mapping, bundle, findings, request, llm_error=llm_error
            )
            result.test_candidates = generation
            result.test_generation = generation_summary
            if generation_summary.get("state") in {"failed", "rejected"}:
                result.warnings.append(
                    "test generation did not produce a valid candidate: "
                    + "; ".join(str(note) for note in generation_summary.get("notes") or [])[:500]
                )
            if generation_summary.get("usage", {}).get("requests"):
                review.usage = {
                    **(review.usage or {}),
                    "test_generation": generation_summary["usage"],
                }
            review.metadata_json = self._metadata(
                request=request,
                mapping=mapping,
                bundle=bundle,
                checks=checks,
                validation=validation,
                completion_meta=completion_meta,
                llm_error=llm_error,
                test_generation=generation_summary,
            )
            review.duration_ms = int((time.perf_counter() - started) * 1000)
            if llm_error:
                review.status = ReviewStatus.FAILED.value
                review.stage = "model_failed"
                review.progress = 100
                review.error = llm_error
                self._publish_if_configured(review, findings, result)
            else:
                review.status = ReviewStatus.SUCCEEDED.value
                review.stage = "succeeded"
                review.progress = 100
                self._publish_if_configured(review, findings, result)
            review.finished_at = datetime.now(UTC)
            self.session.commit()
            logger.info(
                "review finished",
                extra=log_extra(
                    review_id=review.id,
                    status=review.status,
                    findings=len(findings),
                    verified=review.verified_finding_count,
                    duration_ms=review.duration_ms,
                    is_mock=review.is_mock_provider,
                ),
            )
            return result
        except Exception as exc:
            self.session.rollback()
            self._mark_failed(review, exc, started)
            logger.exception("review failed", extra=log_extra(review_id=review.id))
            raise

    # ------------------------------------------------------------- persistence
    def _persist_findings(
        self,
        review: Review,
        *,
        drafts: list[FindingDraft],
        rejected: list[FindingDraft],
        mapping: SymbolMapping,
        bundle: RetrievalBundle,
        request: ReviewRequest,
    ) -> list[Finding]:
        rows: list[Finding] = []
        pending_dedupe: list[tuple[Finding, str]] = []
        fingerprints: set[str] = set()
        retrieval_by_path: dict[str, list[dict[str, Any]]] = {}
        for item in [*bundle.context, *bundle.related_tests]:
            retrieval_by_path.setdefault(item.path, []).append(item.to_dict())

        for draft in [*drafts, *rejected]:
            fingerprint = finding_fingerprint(draft)
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            row = Finding(
                review_id=review.id,
                repository_id=review.repository_id,
                repository_version_id=mapping.version_id,
                commit_sha=request.commit_sha,
                path=draft.path,
                start_line=draft.start_line,
                end_line=draft.end_line,
                line_verified=draft.line_verified,
                rule_id=draft.rule_id,
                category=draft.category_value,
                severity=draft.severity_value,
                severity_rank=severity_rank(draft.severity_value),
                title=draft.title,
                explanation=draft.explanation,
                evidence=[item.to_dict() for item in draft.evidence],
                suggestion=draft.suggestion,
                suggested_test=draft.suggested_test,
                verification_status=draft.verification_value,
                confidence=draft.confidence,
                confidence_basis=(
                    draft.confidence_basis_value
                    if draft.confidence is not None
                    else ConfidenceBasis.UNAVAILABLE.value
                ),
                detector=draft.detector,
                provider=draft.provider,
                model=draft.model,
                fingerprint=fingerprint,
                dedupe_of_finding_id=draft.dedupe_of,
                rejection_reason=draft.rejection_reason,
                retrieval_evidence=retrieval_by_path.get(draft.path, [])[:3],
                publish_state=PublishState.NOT_PUBLISHED.value,
            )
            if draft.dedupe_of:
                pending_dedupe.append((row, draft.dedupe_of))
            self.session.add(row)
            rows.append(row)
        try:
            self.session.flush()
        except IntegrityError:  # pragma: no cover - guarded by the in-memory set
            self.session.rollback()
            logger.warning(
                "duplicate finding fingerprint survived dedupe", extra=log_extra(review_id=review.id)
            )
            raise
        for row, reference in pending_dedupe:
            target = next(
                (
                    candidate
                    for candidate in rows
                    if candidate.rule_id == reference or candidate.title == reference
                ),
                None,
            )
            if target is not None and target.id != row.id:
                row.dedupe_of_finding_id = target.id
        return rows

    def _generate_tests(
        self,
        review: Review,
        mapping: SymbolMapping,
        bundle: RetrievalBundle,
        findings: list[Finding],
        request: ReviewRequest,
        *,
        llm_error: str | None,
    ) -> tuple[list[TestCandidate], dict[str, Any]]:
        """Generate test candidates as optional review enrichment.

        Never raises: a provider/validation failure is recorded and the review
        result is preserved. Candidates are never executed here.
        """
        from patchlens.services.testing.generator import (
            generate_candidates,
            targets_from_mapping,
        )

        if not self.settings.test_generation_enabled:
            return [], {
                "state": "disabled",
                "detail": "TEST_GENERATION_ENABLED=false; no candidate tests were requested",
                "execution": {"mode": self.settings.test_execution_mode, "executed": 0},
            }
        if llm_error:
            return [], {
                "state": "skipped",
                "detail": "the review model failed, so test generation was not attempted",
                "execution": {"mode": self.settings.test_execution_mode, "executed": 0},
            }
        targets = targets_from_mapping(
            mapping,
            bundle,
            findings,
            limit=self.settings.test_generation_max_candidates,
        )
        if not targets:
            return [], {
                "state": "skipped",
                "detail": "no changed non-test symbols with source were available for test generation",
                "execution": {"mode": self.settings.test_execution_mode, "executed": 0},
            }
        try:
            provider = self._llm_provider or self.llm
            generated: GenerationResult = generate_candidates(
                self.session,
                repository=request.repository,
                targets=targets,
                settings=self.settings,
                llm_provider=provider,
                review=review,
                commit_sha=request.commit_sha,
            )
        except ProviderError as exc:
            detail = f"test generation provider failed: {exc.message}"
            logger.warning(
                "test generation failed; review preserved",
                extra=log_extra(review_id=review.id, **describe_exception(exc)),
            )
            return [], {
                "state": "failed",
                "detail": detail[:1_000],
                "notes": [detail[:1_000]],
                "execution": {"mode": self.settings.test_execution_mode, "executed": 0},
            }
        except Exception as exc:  # pragma: no cover - defensive boundary
            detail = f"test generation failed: {type(exc).__name__}: {exc}"
            logger.exception("test generation failed unexpectedly", extra=log_extra(review_id=review.id))
            return [], {
                "state": "failed",
                "detail": detail[:1_000],
                "notes": [detail[:1_000]],
                "execution": {"mode": self.settings.test_execution_mode, "executed": 0},
            }

        summary = generated.summary()
        summary["execution"] = {
            "mode": self.settings.test_execution_mode,
            "executed": 0,
            "note": (
                "generation is not execution; request execution explicitly and see "
                "/api/v1/test-execution for the available sandbox"
            ),
        }
        statuses: dict[str, int] = {}
        for candidate in [*generated.candidates, *generated.reused]:
            statuses[candidate.status] = statuses.get(candidate.status, 0) + 1
        summary["statuses"] = statuses
        if generated.candidates and all(
            candidate.status == "execution_disabled" for candidate in generated.candidates
        ):
            summary["detail"] = (
                "candidate tests were generated but not executed because no execution sandbox is enabled"
            )
        return generated.candidates, summary

    def _publish_if_configured(
        self, review: Review, findings: list[Finding], result: ReviewRunResult
    ) -> None:
        publishable = [
            finding
            for finding in findings
            if finding.verification_status
            in {VerificationStatus.VERIFIED.value, VerificationStatus.NEEDS_MANUAL_VERIFICATION.value}
        ]
        for finding in findings:
            if finding not in publishable and finding.publish_state == PublishState.NOT_PUBLISHED.value:
                finding.publish_state = PublishState.SKIPPED.value
                finding.publish_detail = (
                    f"verification status '{finding.verification_status}' is not publishable"
                )
        if self.publisher is None:
            skipped_detail = (
                "publication skipped: GitHub publishing is disabled or the App is not "
                "configured (GITHUB_PUBLISH_ENABLED, GITHUB_APP_ID, GITHUB_PRIVATE_KEY)"
            )
            for finding in publishable:
                finding.publish_state = PublishState.SKIPPED.value
                finding.publish_detail = skipped_detail
            review.publish_state = PublishState.SKIPPED.value
            review.publish_detail = skipped_detail
            review.metadata_json = {
                **(review.metadata_json or {}),
                "publication": {
                    "state": PublishState.SKIPPED.value,
                    "detail": skipped_detail,
                    "eligible": len(publishable),
                },
            }
            return
        try:
            summary = self.publisher.publish(self.session, review, publishable)
            state = str(summary.get("state") or "failed")
            try:
                publish_state = PublishState(state)
            except ValueError:
                publish_state = PublishState.FAILED
                summary = {**summary, "detail": f"publisher returned an unknown state '{state}'"}
            review.publish_state = publish_state.value
            review.publish_detail = str(summary.get("detail") or "")[:2_000] or None
            if publish_state is PublishState.PUBLISHED:
                review.published_at = datetime.now(UTC)
                url = summary.get("review_url")
                if isinstance(url, str) and url:
                    review.published_comment_url = url
            review.metadata_json = {**(review.metadata_json or {}), "publication": summary}
        except Exception as exc:
            failure = describe_exception(exc)
            review.publish_state = PublishState.FAILED.value
            review.publish_detail = str(failure["message"])[:2_000]
            for finding in publishable:
                if finding.publish_state == PublishState.NOT_PUBLISHED.value:
                    finding.publish_state = PublishState.FAILED.value
                    finding.publish_detail = str(failure["message"])[:1_000]
            result.warnings.append(f"publication failed: {failure['message']}")
            review.metadata_json = {
                **(review.metadata_json or {}),
                "publication": {"state": PublishState.FAILED.value, "detail": failure["message"]},
            }

    # ------------------------------------------------------------------ helpers
    def _stage(self, review: Review, stage: str, progress: int) -> None:
        review.stage = stage
        review.progress = progress
        self.session.commit()

    def _mark_failed(self, review: Review, exc: Exception, started: float) -> None:
        with suppress(Exception):  # row may never have reached the database
            self.session.refresh(review)
        review.status = ReviewStatus.FAILED.value
        review.stage = "failed"
        review.error = f"{type(exc).__name__}: {exc}"[:2_000]
        review.duration_ms = int((time.perf_counter() - started) * 1000)
        review.finished_at = datetime.now(UTC)
        self.session.add(review)
        self.session.commit()

    def _metadata(
        self,
        *,
        request: ReviewRequest,
        mapping: SymbolMapping,
        bundle: RetrievalBundle,
        checks: CheckRunResult,
        validation: ValidationResult | None,
        completion_meta: dict[str, Any],
        llm_error: str | None,
        test_generation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        changed_non_test = [
            symbol for symbol in mapping.changed_symbols if not symbol.is_test and symbol.kind != "module"
        ]
        related_tests: list[dict[str, Any]] = []
        seen_tests: set[tuple[str, str]] = set()
        for changed_symbol, matches in mapping.test_matches.items():
            for match in matches:
                payload = match.to_dict()
                key = (str(payload.get("test_path")), str(payload.get("test_name")))
                if key in seen_tests:
                    continue
                seen_tests.add(key)
                related_tests.append({**payload, "changed_symbol": changed_symbol})
        return {
            "pipeline_version": PIPELINE_VERSION,
            "diff_hash": content_fingerprint(request.diff_text, length=64),
            "strategy": self.settings.retrieval_strategy,
            "embedding_provider": self.settings.embedding_provider,
            "embedding_model": self.settings.embedding_model,
            "symbol_mapping": {
                "changed_symbols": len(mapping.changed_symbols),
                "files_read": sorted(mapping.file_texts),
                "indexed_files": len(mapping.indexed_paths),
                "reparse_failures": mapping.reparse_failures,
                "notes": mapping.notes,
            },
            "checks": checks.to_dict(),
            "retrieval": bundle.result.to_dict(),
            "validation": validation.to_dict() if validation is not None else None,
            "validation_notes": validation.notes if validation is not None else None,
            "model": completion_meta,
            "llm_error": llm_error,
            "degraded": llm_error is not None,
            "test_intelligence": {
                "related_tests": related_tests[:50],
                "changed_non_test_symbols": len(changed_non_test),
                "symbols_with_related_tests": len(mapping.test_matches),
                "symbols_without_related_tests": max(0, len(changed_non_test) - len(mapping.test_matches)),
                "candidates": test_generation or {"state": "disabled"},
            },
            "test_generation": test_generation,
        }


def run_review(
    session: Session,
    *,
    repository: Repository,
    diff_text: str,
    commit_sha: str,
    base_sha: str | None = None,
    pull_request_id: str | None = None,
    trigger: str = "manual",
    settings: Settings | None = None,
    llm_provider: LLMProvider | None = None,
    retriever: CodeRetriever | None = None,
    publisher: Publisher | None = None,
) -> ReviewRunResult:
    """Convenience wrapper used by the API, CLI and the demo workflow."""
    pipeline = ReviewPipeline(
        session,
        settings=settings,
        llm_provider=llm_provider,
        retriever=retriever,
        publisher=publisher,
    )
    return pipeline.run(
        ReviewRequest(
            repository=repository,
            diff_text=diff_text,
            commit_sha=commit_sha,
            base_sha=base_sha,
            pull_request_id=pull_request_id,
            trigger=trigger,
        )
    )


def eligible_for_publication(finding: Finding) -> bool:
    """Only verified / needs-verification findings may become PR comments."""
    return finding.verification_status in {
        VerificationStatus.VERIFIED.value,
        VerificationStatus.NEEDS_MANUAL_VERIFICATION.value,
    }


__all__ = [
    "PIPELINE_VERSION",
    "Publisher",
    "ReviewPipeline",
    "ReviewRequest",
    "ReviewRunResult",
    "eligible_for_publication",
    "run_review",
]
