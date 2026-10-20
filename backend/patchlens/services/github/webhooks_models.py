"""Pydantic models for the webhook payloads PatchLens acts on.

GitHub's payloads are large and evolve. These models validate the fields the
integration uses and ignore everything else; missing optional fields do not fail a
delivery. ``extra="ignore"`` is deliberate: a new GitHub field must never break
review delivery.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class GitHubAccount(_Payload):
    login: str = ""
    type: str = "Organization"


class GitHubRepositoryRef(_Payload):
    full_name: str
    name: str = ""
    private: bool = False
    default_branch: str = "main"
    html_url: str | None = None
    owner: GitHubAccount | None = None


class GitHubInstallationRef(_Payload):
    id: int


class GitHubInstallationInfo(GitHubInstallationRef):
    account: GitHubAccount | None = None
    repository_selection: str = "selected"
    permissions: dict[str, Any] = Field(default_factory=dict)
    events: list[str] = Field(default_factory=list)
    suspended_at: str | None = None


class GitHubRef(_Payload):
    ref: str = ""
    sha: str = ""


class GitHubUser(_Payload):
    login: str = ""


class GitHubPullRequestData(_Payload):
    number: int
    title: str = ""
    state: str = "open"
    draft: bool = False
    html_url: str | None = None
    user: GitHubUser | None = None
    head: GitHubRef
    base: GitHubRef
    changed_files: int = 0
    additions: int = 0
    deletions: int = 0


class PullRequestEvent(_Payload):
    action: str
    number: int
    pull_request: GitHubPullRequestData
    repository: GitHubRepositoryRef
    installation: GitHubInstallationRef | None = None
    sender: GitHubUser | None = None


class InstallationEvent(_Payload):
    action: str
    installation: GitHubInstallationInfo
    repositories: list[GitHubRepositoryRef] = Field(default_factory=list)


class InstallationRepositoriesEvent(_Payload):
    action: str
    installation: GitHubInstallationRef
    repositories_added: list[GitHubRepositoryRef] = Field(default_factory=list)
    repositories_removed: list[GitHubRepositoryRef] = Field(default_factory=list)


__all__ = [
    "InstallationEvent",
    "InstallationRepositoriesEvent",
    "PullRequestEvent",
]
