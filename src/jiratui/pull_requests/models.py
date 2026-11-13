"""The state of the pull requests linked to work items, as read from Jira, GitHub and Qodo."""

from dataclasses import dataclass, field
from enum import Enum


class ReviewSource(Enum):
    """Who wrote a piece of review feedback."""

    TEAMMATE = 'teammate'
    QODO = 'qodo'
    ARNICA = 'arnica'
    OTHER_BOT = 'other_bot'


@dataclass(frozen=True)
class PullRequestRef:
    """A pull request that Jira's development panel links to a work item."""

    url: str
    owner: str
    repository: str
    number: int
    jira_status: str = ''
    """The status Jira's development panel records: OPEN, MERGED or DECLINED."""
    source_branch: str | None = None
    destination_branch: str | None = None

    @property
    def is_open(self) -> bool:
        return self.jira_status.upper() == 'OPEN'


@dataclass
class CheckSummary:
    """The checks on a pull request's head commit."""

    passed: int = 0
    failing_names: list[str] = field(default_factory=list)
    pending_names: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + len(self.failing_names) + len(self.pending_names)


@dataclass
class ReviewItem:
    """A review thread, conversation comment or review left on a pull request."""

    source: ReviewSource
    author: str
    kind: str
    """`thread`, `comment` or `review`."""
    excerpt: str
    path: str | None = None
    line: int | None = None
    resolved: bool = False
    outdated: bool = False
    answered: bool = False
    """For a teammate's feedback: whether it is resolved or the pull request's author answered it last."""


@dataclass
class ArnicaFinding:
    """A code risk Arnica reported on a pull request."""

    category: str
    title: str
    severity: str | None
    status: str
    """`open`, `dismissed`, `resolved` or `unknown`."""
    path: str | None = None
    line: int | None = None

    @property
    def is_open(self) -> bool:
        return self.status not in ('dismissed', 'resolved')


@dataclass
class QodoFinding:
    """A finding from Qodo's review session of a pull request."""

    id: str
    title: str
    action_level: str | None
    category: str | None
    status: str
    is_open: bool
    path: str | None = None
    line: int | None = None


@dataclass
class QodoReview:
    """Qodo's review session of a pull request, as its CLI reports it."""

    available: bool
    reason: str | None = None
    """Why the review could not be read, when `available` is `False`."""
    status: str | None = None
    commit_sha: str | None = None
    findings: list[QodoFinding] = field(default_factory=list)


@dataclass
class PullRequestState:
    """Where one pull request stands on GitHub."""

    ref: PullRequestRef
    title: str = ''
    state: str = ''
    """OPEN, MERGED or CLOSED."""
    is_draft: bool = False
    review_decision: str | None = None
    mergeable: str | None = None
    author: str = ''
    head_branch: str = ''
    base_branch: str = ''
    head_sha: str = ''
    checks: CheckSummary = field(default_factory=CheckSummary)
    review_items: list[ReviewItem] = field(default_factory=list)
    arnica_findings: list[ArnicaFinding] = field(default_factory=list)
    stacked_on: str | None = None
    """The key of the work item whose pull request this one's base branch belongs to."""
    qodo: QodoReview | None = None
    error: str | None = None
    """Why GitHub could not be read for this pull request; the other fields are then empty."""

    @property
    def unanswered_teammate_items(self) -> list[ReviewItem]:
        return [
            item
            for item in self.review_items
            if item.source is ReviewSource.TEAMMATE and not item.answered
        ]

    @property
    def unresolved_qodo_threads(self) -> list[ReviewItem]:
        return [
            item
            for item in self.review_items
            if item.source is ReviewSource.QODO and item.kind == 'thread' and not item.resolved
        ]

    @property
    def open_arnica_findings(self) -> list[ArnicaFinding]:
        return [finding for finding in self.arnica_findings if finding.is_open]


@dataclass
class WorkItemPullRequests:
    """The pull requests linked to one work item."""

    work_item_key: str
    pull_requests: list[PullRequestState] = field(default_factory=list)
    error: str | None = None
    """Why Jira's development panel could not be read for this work item."""
