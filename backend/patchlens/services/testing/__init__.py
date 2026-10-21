"""Test intelligence: related-test context, candidate generation and execution."""

from patchlens.services.testing.execution import (
    ExecutionOutcome,
    SandboxStatus,
    execute_candidate,
    mark_execution_disabled,
    mark_execution_error,
    resolve_sandbox,
    run_test_source,
)
from patchlens.services.testing.generator import (
    GenerationResult,
    TestTargetSpec,
    generate_candidates,
    targets_from_indexed,
    targets_from_mapping,
    validate_generated_test,
)

__all__ = [
    "ExecutionOutcome",
    "GenerationResult",
    "SandboxStatus",
    "TestTargetSpec",
    "execute_candidate",
    "generate_candidates",
    "mark_execution_disabled",
    "mark_execution_error",
    "resolve_sandbox",
    "run_test_source",
    "targets_from_indexed",
    "targets_from_mapping",
    "validate_generated_test",
]
