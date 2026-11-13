"""Turns raw Jira, GitHub and Qodo answers into pull request state.

The rules match the team's `get-pr-review-state.ps1`: a teammate is a GitHub account of type User, Qodo comments as a
bot whose login starts with `qodo`, Arnica as `arnica-github-connector`, and a teammate's feedback is answered once
it is resolved or the pull request's author replied after it.
"""

from collections.abc import Iterable
import json
import re
from typing import Any

from jiratui.pull_requests.models import (
    ArnicaFinding,
    CheckSummary,
    PullRequestRef,
    PullRequestState,
    QodoFinding,
    QodoReview,
    ReviewItem,
    ReviewSource,
    WorkItemPullRequests,
)

QODO_LOGIN = re.compile(r'^qodo', re.IGNORECASE)
ARNICA_LOGIN = re.compile(r'^arnica-github-connector(\[bot\])?$', re.IGNORECASE)
BOT_COMMAND = re.compile(r'^\s*(qodo\b|/agentic_review\b|\[(arnica|a)\])', re.IGNORECASE)
"""A comment addressed to a bot answers no teammate."""

QODO_CLOSED_STATUSES = {
    'implemented',
    'full_implementation',
    'dismissed',
    'detected_after_merge',
    'outdated',
}
"""Qodo attribution statuses that close a finding; any other status, including one Qodo adds later, leaves it open."""

_PULL_REQUEST_URL = re.compile(
    r'^https://[^/]+/(?P<owner>[^/]+)/(?P<repository>[^/]+)/pull/(?P<number>\d+)'
)
_ARNICA_HEADING = re.compile(r'(?m)^###\s+(?P<category>.+?)\s+Risk:\s*(?P<title>.+?)\s*$')
_ARNICA_SEVERITY = re.compile(r'\*\*Severity\*\*:\s*(?P<value>[A-Za-z]+)')
_ARNICA_STATUS = re.compile(r'\*\*Status\*\*:\s*(?P<value>[A-Za-z]+)')
_QODO_TRACE_LINE = re.compile(r'(?m)^\s*trace [0-9a-fA-F]+\s*$')


def parse_pull_request_ref(item: dict[str, Any]) -> PullRequestRef | None:
    """Reads one pull request from Jira's dev-status detail answer; `None` when its URL is not a pull request's."""

    url = str(item.get('url') or '')
    if not (match := _PULL_REQUEST_URL.match(url)):
        return None
    return PullRequestRef(
        url=url,
        owner=match['owner'],
        repository=match['repository'],
        number=int(match['number']),
        jira_status=str(item.get('status') or ''),
        source_branch=(item.get('source') or {}).get('branch'),
        destination_branch=(item.get('destination') or {}).get('branch'),
    )


def parse_dev_status_pull_requests(answer: dict[str, Any]) -> list[PullRequestRef]:
    """Reads every pull request from Jira's dev-status detail answer, one per URL."""

    refs: dict[str, PullRequestRef] = {}
    for detail in answer.get('detail') or []:
        for item in detail.get('pullRequests') or []:
            if (ref := parse_pull_request_ref(item)) is not None:
                refs.setdefault(ref.url, ref)
    return list(refs.values())


def review_source(login: str, typename: str | None) -> ReviewSource:
    """Classifies the author of a piece of review feedback."""

    if ARNICA_LOGIN.match(login):
        return ReviewSource.ARNICA
    if QODO_LOGIN.match(login):
        return ReviewSource.QODO
    if typename == 'User' and not login.endswith('[bot]'):
        return ReviewSource.TEAMMATE
    return ReviewSource.OTHER_BOT


def excerpt(text: str | None, length: int = 160) -> str:
    """Returns the first non-empty line of `text`, without Markdown heading marks, cut to `length` characters."""

    for line in (text or '').splitlines():
        if stripped := line.strip().lstrip('#').strip():
            return stripped[:length]
    return ''


def parse_arnica_finding(
    body: str | None, path: str | None = None, line: int | None = None
) -> ArnicaFinding | None:
    """Reads an Arnica finding from the body of the bot's comment; `None` when the comment is not a finding."""

    body = body or ''
    if not (heading := _ARNICA_HEADING.search(body)):
        return None
    status_text = (match['value'] if (match := _ARNICA_STATUS.search(body)) else '').lower()
    return ArnicaFinding(
        category=heading['category'].strip(),
        title=re.sub(r'\s{2,}', ' ', heading['title'].strip()),
        severity=match['value'] if (match := _ARNICA_SEVERITY.search(body)) else None,
        status=status_text if status_text in ('open', 'dismissed', 'resolved') else 'unknown',
        path=path,
        line=line,
    )


def summarize_checks(contexts: Iterable[dict[str, Any]]) -> CheckSummary:
    """Counts the checks of a status rollup that passed, failed and are still running."""

    summary = CheckSummary()
    for check in contexts:
        if not check:
            continue
        name = str(check.get('name') or check.get('context') or '')
        conclusion = str(check.get('conclusion') or check.get('state') or '')
        if check.get('__typename') == 'StatusContext':
            done = conclusion not in ('PENDING', 'EXPECTED')
        else:
            done = check.get('status') == 'COMPLETED'
        if not done:
            summary.pending_names.append(name)
        elif conclusion in ('SUCCESS', 'NEUTRAL', 'SKIPPED'):
            summary.passed += 1
        else:
            summary.failing_names.append(f'{name} ({conclusion})')
    return summary


def build_pull_request_state(ref: PullRequestRef, node: dict[str, Any] | None) -> PullRequestState:
    """Builds a pull request's state from the GitHub GraphQL node the fork's query returns."""

    if not node:
        return PullRequestState(ref=ref, error='GitHub returned no pull request at this address.')
    author = ((node.get('author') or {}).get('login')) or ''
    head_commit = ((node.get('commits') or {}).get('nodes') or [{}])[-1] or {}
    rollup = ((head_commit.get('commit') or {}).get('statusCheckRollup')) or {}
    state = PullRequestState(
        ref=ref,
        title=node.get('title') or '',
        state=node.get('state') or '',
        is_draft=bool(node.get('isDraft')),
        review_decision=node.get('reviewDecision'),
        mergeable=node.get('mergeable'),
        author=author,
        head_branch=node.get('headRefName') or '',
        base_branch=node.get('baseRefName') or '',
        head_sha=(node.get('headRefOid') or '').lower(),
        checks=summarize_checks((rollup.get('contexts') or {}).get('nodes') or []),
    )
    _read_threads(state, (node.get('reviewThreads') or {}).get('nodes') or [])
    _read_conversation(
        state,
        (node.get('comments') or {}).get('nodes') or [],
        (node.get('reviews') or {}).get('nodes') or [],
    )
    return state


def _read_threads(state: PullRequestState, threads: list[dict[str, Any]]) -> None:
    for thread in threads:
        roots = (thread.get('root') or {}).get('nodes') or []
        if not roots:
            continue
        root = roots[0]
        login = (root.get('author') or {}).get('login') or ''
        source = review_source(login, (root.get('author') or {}).get('__typename'))
        latest = ((thread.get('latest') or {}).get('nodes') or [{}])[0] or {}
        latest_login = (latest.get('author') or {}).get('login') or ''
        resolved = bool(thread.get('isResolved'))
        if source is ReviewSource.ARNICA:
            if finding := parse_arnica_finding(
                root.get('body'), thread.get('path'), thread.get('line')
            ):
                state.arnica_findings.append(finding)
            continue
        if login == state.author:
            continue
        state.review_items.append(
            ReviewItem(
                source=source,
                author=login,
                kind='thread',
                excerpt=excerpt(root.get('body')),
                path=thread.get('path'),
                line=thread.get('line'),
                resolved=resolved,
                outdated=bool(thread.get('isOutdated')),
                answered=resolved or latest_login == state.author,
            )
        )


def _read_conversation(
    state: PullRequestState, comments: list[dict[str, Any]], reviews: list[dict[str, Any]]
) -> None:
    author_replies = sorted(
        str(comment.get('createdAt') or '')
        for comment in comments
        if (comment.get('author') or {}).get('login') == state.author
        and not BOT_COMMAND.match(comment.get('body') or '')
    )
    last_author_reply = author_replies[-1] if author_replies else ''
    for comment in comments:
        login = (comment.get('author') or {}).get('login') or ''
        if login == state.author:
            continue
        source = review_source(login, (comment.get('author') or {}).get('__typename'))
        if source is ReviewSource.ARNICA:
            if finding := parse_arnica_finding(comment.get('body')):
                state.arnica_findings.append(finding)
            continue
        state.review_items.append(
            ReviewItem(
                source=source,
                author=login,
                kind='comment',
                excerpt=excerpt(comment.get('body')),
                answered=last_author_reply > str(comment.get('createdAt') or ''),
            )
        )
    for review in reviews:
        login = (review.get('author') or {}).get('login') or ''
        body = review.get('body') or ''
        if login == state.author or not body.strip():
            continue
        if review.get('state') not in ('COMMENTED', 'CHANGES_REQUESTED'):
            continue
        state.review_items.append(
            ReviewItem(
                source=review_source(login, (review.get('author') or {}).get('__typename')),
                author=login,
                kind='review',
                excerpt=excerpt(body),
                answered=last_author_reply > str(review.get('submittedAt') or ''),
            )
        )


def parse_qodo_output(output: str) -> QodoReview:
    """Reads the JSON `qodo read pr-review-session findings --json` prints, after the CLI's trace line is removed."""

    text = _QODO_TRACE_LINE.sub('', output).strip()
    try:
        answer = json.loads(text)
    except ValueError:
        return QodoReview(
            available=False, reason=f'The Qodo CLI did not answer JSON: {excerpt(text)}'
        )
    if not isinstance(answer, dict):
        return QodoReview(available=False, reason='The Qodo CLI answered an unexpected shape.')
    if error := answer.get('error'):
        message = error.get('message') if isinstance(error, dict) else error
        return QodoReview(available=False, reason=f'Qodo: {message}')
    data = answer['result'] if isinstance(answer.get('result'), dict) else answer
    session = data.get('review_session')
    review = QodoReview(
        available=True,
        status='none' if session is None else str(session.get('status') or ''),
        commit_sha=None if session is None else str(session.get('commit_sha') or '').lower(),
    )
    for finding in data.get('findings') or []:
        status = str(finding.get('attribution_status') or '')
        location = finding.get('location') or {}
        review.findings.append(
            QodoFinding(
                id=str(finding.get('id') or ''),
                title=str(finding.get('title') or ''),
                action_level=finding.get('action_level'),
                category=finding.get('category'),
                status=status,
                is_open=status not in QODO_CLOSED_STATUSES,
                path=location.get('file_path'),
                line=location.get('start_line'),
            )
        )
    return review


def link_stacked_pull_requests(work_items: Iterable[WorkItemPullRequests]) -> None:
    """Marks each pull request whose base branch is another listed work item's pull request branch as stacked on it."""

    work_items = list(work_items)
    key_by_branch = {
        (
            pull_request.ref.owner,
            pull_request.ref.repository,
            pull_request.head_branch,
        ): item.work_item_key
        for item in work_items
        for pull_request in item.pull_requests
        if pull_request.head_branch
    }
    for item in work_items:
        for pull_request in item.pull_requests:
            key = key_by_branch.get(
                (pull_request.ref.owner, pull_request.ref.repository, pull_request.base_branch)
            )
            pull_request.stacked_on = key if key and key != item.work_item_key else None
