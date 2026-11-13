"""The PR badge of a search results row and the PRs tab of the selected work item."""

from dataclasses import replace

from rich.console import Group
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from jiratui.pull_requests.models import (
    PullRequestState,
    QodoReview,
    ReviewItem,
    ReviewSource,
    WorkItemPullRequests,
)

GREEN = '#3fb950'
YELLOW = '#d29922'
RED = '#f85149'
PURPLE = '#a371f7'
MUTED = '#8b949e'

STATE_GLYPHS = {
    'OPEN': ('●', GREEN),
    'DRAFT': ('◌', MUTED),
    'MERGED': ('◆', PURPLE),
    'CLOSED': ('✕', RED),
}


def _state_key(pull_request: PullRequestState) -> str:
    return 'DRAFT' if pull_request.state == 'OPEN' and pull_request.is_draft else pull_request.state


def pull_request_badge(work_item: WorkItemPullRequests) -> Text:
    """Summarizes a work item's pull requests in a few glyphs for its search results row.

    Open pull requests are shown; with none open, the last one's state. Each shows its state (● open, ◌ draft,
    ◆ merged, ✕ closed), its checks (✓ passed/total, … while some run, ✗ failing), an approval ✔ or changes
    requested ✎, then the teammate feedback not answered (@), Qodo threads not resolved (Q) and Arnica findings open
    (⚠), and ↳ the work item whose pull request it is stacked on. Every glyph is one cell wide in any terminal, so
    the column lines up.
    """

    if work_item.error:
        return Text('?', style=MUTED)
    shown = [pr for pr in work_item.pull_requests if pr.state == 'OPEN'] or work_item.pull_requests[
        -1:
    ]
    badge = Text()
    if len(shown) > 1:
        badge.append(f'{len(shown)}× ', style=MUTED)
    for index, pull_request in enumerate(shown):
        if index:
            badge.append(' | ', style=MUTED)
        badge.append_text(_pull_request_badge(pull_request))
    return badge


def _pull_request_badge(pull_request: PullRequestState) -> Text:
    if pull_request.error:
        return Text('?', style=MUTED)
    glyph, color = STATE_GLYPHS.get(_state_key(pull_request), ('?', MUTED))
    parts: list[tuple[str, str]] = [(glyph, color)]
    if pull_request.state == 'OPEN':
        checks = pull_request.checks
        if checks.failing_names:
            parts.append((f'✗{len(checks.failing_names)}', RED))
        elif checks.pending_names:
            parts.append((f'…{checks.passed}/{checks.total}', YELLOW))
        elif checks.total:
            parts.append((f'✓{checks.passed}/{checks.total}', GREEN))
        if pull_request.review_decision == 'APPROVED':
            parts.append(('✔', GREEN))
        elif pull_request.review_decision == 'CHANGES_REQUESTED':
            parts.append(('✎', RED))
        if unanswered := len(pull_request.unanswered_teammate_items):
            parts.append((f'@{unanswered}', 'bold'))
        if qodo := len(pull_request.unresolved_qodo_threads):
            parts.append((f'Q{qodo}', YELLOW))
        if arnica := len(pull_request.open_arnica_findings):
            parts.append((f'⚠{arnica}', RED))
        if pull_request.stacked_on:
            parts.append((f'↳{pull_request.stacked_on}', MUTED))
    badge = Text()
    for index, (text, style) in enumerate(parts):
        if index:
            badge.append(' ')
        badge.append(text, style=style)
    return badge


class PullRequestsWidget(VerticalScroll, can_focus=True):
    """The PRs tab: every pull request of the selected work item, with its checks and review.

    Feedback is grouped by source: teammates (unanswered first, resolved dimmed), Qodo's findings from its review
    session (closed ones dimmed), and Arnica's code risks (closed ones dimmed).
    """

    def __init__(self) -> None:
        super().__init__(id='pull-requests')
        self._work_item_key: str | None = None
        self._shown: WorkItemPullRequests | None = None

    def compose(self):
        yield Static('', id='pull-requests-content')

    @property
    def work_item_key(self) -> str | None:
        """The key of the work item whose pull requests the tab shows or is loading."""

        return self._work_item_key

    @property
    def content(self) -> Static:
        return self.query_one('#pull-requests-content', expect_type=Static)

    def clear(self) -> None:
        self._work_item_key = None
        self._shown = None
        self.content.update('')

    def show_loading(self, work_item_key: str) -> None:
        self._work_item_key = work_item_key
        self._shown = None
        self.content.update(Text(f'Loading the pull requests of {work_item_key}…', style=MUTED))

    def show(self, work_item: WorkItemPullRequests) -> None:
        self._work_item_key = work_item.work_item_key
        self._shown = work_item
        self.content.update(describe_work_item_pull_requests(work_item))

    def with_shown_qodo_reviews(self, work_item: WorkItemPullRequests) -> WorkItemPullRequests:
        """Returns `work_item` carrying the Qodo reviews the tab shows for the same pull requests, if any."""

        if self._shown is None or self._shown.work_item_key != work_item.work_item_key:
            return work_item
        reviews = {
            pull_request.ref.url: pull_request.qodo
            for pull_request in self._shown.pull_requests
            if pull_request.qodo is not None
        }
        return replace(
            work_item,
            pull_requests=[
                replace(pull_request, qodo=reviews.get(pull_request.ref.url, pull_request.qodo))
                for pull_request in work_item.pull_requests
            ],
        )


def describe_work_item_pull_requests(work_item: WorkItemPullRequests) -> Group:
    """Renders the PRs tab's content for one work item."""

    if work_item.error:
        return Group(Text(work_item.error, style=RED))
    if not work_item.pull_requests:
        return Group(
            Text(f'No pull requests are linked to {work_item.work_item_key}.', style=MUTED)
        )
    blocks: list[Text] = []
    for pull_request in work_item.pull_requests:
        if blocks:
            blocks.append(Text(''))
        blocks.extend(_describe_pull_request(pull_request))
    return Group(*blocks)


def _describe_pull_request(pull_request: PullRequestState) -> list[Text]:
    ref = pull_request.ref
    heading = Text(f'#{ref.number} ', style='bold')
    if pull_request.error:
        return [heading + Text(pull_request.error, style=RED), Text(ref.url, style=MUTED)]
    glyph, color = STATE_GLYPHS.get(_state_key(pull_request), ('?', MUTED))
    heading.append(f'{glyph} {_state_key(pull_request).capitalize()}', style=color)
    lines = [heading, Text(pull_request.title)]
    branches = Text(f'{pull_request.head_branch} → {pull_request.base_branch}', style=MUTED)
    if pull_request.stacked_on:
        branches.append(f'   stacked on {pull_request.stacked_on}', style=YELLOW)
    lines += [branches, Text(ref.url, style=MUTED)]
    if pull_request.state != 'OPEN':
        return lines
    lines.append(_describe_status(pull_request))
    lines += _describe_teammates(pull_request.review_items)
    lines += _describe_qodo(pull_request)
    lines += _describe_arnica(pull_request)
    return lines


def _describe_status(pull_request: PullRequestState) -> Text:
    checks = pull_request.checks
    status = Text()
    if checks.failing_names:
        status.append(
            f'✗ {len(checks.failing_names)} failing: {", ".join(checks.failing_names)}', style=RED
        )
    elif checks.pending_names:
        status.append(
            f'… {checks.passed}/{checks.total} passed, running: {", ".join(checks.pending_names)}',
            style=YELLOW,
        )
    elif checks.total:
        status.append(f'✓ all {checks.total} checks passed', style=GREEN)
    else:
        status.append('no checks', style=MUTED)
    decision = {
        'APPROVED': ('approved', GREEN),
        'CHANGES_REQUESTED': ('changes requested', RED),
        'REVIEW_REQUIRED': ('review required', YELLOW),
    }.get(pull_request.review_decision or '', ('no review decision', MUTED))
    status.append(' · ')
    status.append(decision[0], style=decision[1])
    if pull_request.mergeable == 'CONFLICTING':
        status.append(' · ')
        status.append('merge conflicts', style=RED)
    return status


def _describe_teammates(items: list[ReviewItem]) -> list[Text]:
    teammates = [item for item in items if item.source is ReviewSource.TEAMMATE]
    unanswered = [item for item in teammates if not item.answered]
    heading = Text('\nTeammates', style='bold')
    heading.append(f' — {len(unanswered)} unanswered' if teammates else ' — none', style=MUTED)
    lines = [heading]
    for item in sorted(teammates, key=lambda entry: (entry.answered, entry.resolved)):
        style = 'dim' if item.resolved else ('' if item.answered else 'bold')
        where = f' {item.path}:{item.line}' if item.path else ''
        line = Text(
            f'  {"○" if item.answered else "●"} {item.author} {item.kind}{where}: ', style=style
        )
        line.append(item.excerpt, style=style)
        lines.append(line)
    return lines


def _describe_qodo(pull_request: PullRequestState) -> list[Text]:
    review: QodoReview | None = pull_request.qodo
    heading = Text('\nQodo', style='bold')
    lines = [heading]
    if review is None or not review.available:
        reason = review.reason if review else 'its review session is still loading'
        heading.append(f' — {reason}; unresolved threads on GitHub:', style=MUTED)
        for item in pull_request.unresolved_qodo_threads:
            where = f'{item.path}:{item.line} ' if item.path else ''
            lines.append(Text(f'  ● {where}{item.excerpt}'))
        return lines
    open_findings = [finding for finding in review.findings if finding.is_open]
    freshness = 'at head' if review.commit_sha == pull_request.head_sha else 'behind head'
    heading.append(
        f' — review {review.status} {freshness}, {len(open_findings)} open finding(s)', style=MUTED
    )
    for finding in sorted(review.findings, key=lambda entry: not entry.is_open):
        style = '' if finding.is_open else 'dim'
        level = (finding.action_level or '').replace('_', ' ')
        where = f' · {finding.path}:{finding.line}' if finding.path else ''
        lines.append(
            Text(
                f'  {"●" if finding.is_open else "○"} {level} · {finding.category}{where}',
                style=style,
            )
        )
        lines.append(Text(f'    {finding.title} [{finding.status}]', style=style))
    return lines


def _describe_arnica(pull_request: PullRequestState) -> list[Text]:
    findings = pull_request.arnica_findings
    heading = Text('\nArnica', style='bold')
    heading.append(
        f' — {len(pull_request.open_arnica_findings)} open' if findings else ' — no findings',
        style=MUTED,
    )
    lines = [heading]
    for finding in sorted(findings, key=lambda entry: not entry.is_open):
        style = '' if finding.is_open else 'dim'
        where = f' · {finding.path}:{finding.line}' if finding.path else ''
        lines.append(
            Text(
                f'  {"●" if finding.is_open else "○"} {finding.severity} {finding.category}{where}',
                style=style,
            )
        )
        lines.append(Text(f'    {finding.title} [{finding.status}]', style=style))
    return lines
