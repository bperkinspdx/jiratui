"""Rules for the actions on a selected work item: pick up, move along the board, and hand to Claude Code."""

import base64
from collections.abc import Sequence

from jiratui.config import StatusSectionConfiguration
from jiratui.models import IssueTransition
from jiratui.pull_requests.models import WorkItemPullRequests


def find_transition_to_status(
    transitions: Sequence[IssueTransition], status_name: str
) -> IssueTransition | None:
    """Returns the transition that leads to the status named `status_name`, matching regardless of case."""

    wanted = status_name.casefold()
    return next(
        (transition for transition in transitions if transition.to_state.name.casefold() == wanted),
        None,
    )


def find_column_transition(
    status_name: str,
    sections: Sequence[StatusSectionConfiguration],
    transitions: Sequence[IssueTransition],
    step: int,
) -> IssueTransition | None:
    """Finds the transition that moves a work item to the next (`step=1`) or previous (`step=-1`) board column.

    The columns are the status sections that name statuses, in their configured order; sections that gather work
    items by label are not columns. Only the adjacent column counts, never one beyond it, so a move can never jump,
    e.g., from Backlog straight to a Won't Do in the Done column. Its statuses are tried in their configured order, so
    the first one an available transition reaches is the target.

    Returns:
        The transition, or `None` when the work item's status is in no column, there is no column in that direction,
        or the workflow reaches none of the adjacent column's statuses.
    """

    columns = [section for section in sections if not section.labels and section.statuses]
    current = next(
        (
            position
            for position, section in enumerate(columns)
            if status_name.casefold() in {status.casefold() for status in section.statuses}
        ),
        None,
    )
    if current is None or not 0 <= current + step < len(columns):
        return None
    for status in columns[current + step].statuses:
        if transition := find_transition_to_status(transitions, status):
            return transition
    return None


def pull_request_repository(work_item: WorkItemPullRequests | None) -> str | None:
    """Returns the repository of the work item's first open pull request, else of its last one."""

    if work_item is None or not work_item.pull_requests:
        return None
    open_pull_requests = [
        pull_request for pull_request in work_item.pull_requests if pull_request.ref.is_open
    ]
    return (open_pull_requests or work_item.pull_requests[-1:])[0].ref.repository


def pull_request_urls(work_item: WorkItemPullRequests | None) -> list[str]:
    """Returns the URLs of the work item's open pull requests, else of its last one."""

    if work_item is None or not work_item.pull_requests:
        return []
    open_urls = [
        pull_request.ref.url
        for pull_request in work_item.pull_requests
        if pull_request.state == 'OPEN' or (not pull_request.state and pull_request.ref.is_open)
    ]
    return open_urls or [work_item.pull_requests[-1].ref.url]


def claude_launch_arguments(
    terminal: str, shell: str, directory: str, title: str, command: str
) -> list[str]:
    """Builds the command that opens a Windows Terminal tab in `directory` running `command` in PowerShell.

    The command reaches PowerShell base64-encoded, so neither Windows Terminal, which splits its arguments at `;`,
    nor process-argument quoting can alter it.
    """

    encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    return [
        terminal,
        '--window',
        '0',
        'new-tab',
        '--title',
        title,
        '--startingDirectory',
        directory,
        shell,
        '-NoExit',
        '-EncodedCommand',
        encoded,
    ]
