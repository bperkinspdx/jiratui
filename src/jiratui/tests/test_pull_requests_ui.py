from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from rich.console import Console

from jiratui.api_controller.controller import APIControllerResponse
from jiratui.config import PullRequestsConfiguration, WorkItemsViewConfiguration
from jiratui.models import IssueStatus, IssueType, JiraIssue, JiraIssueSearchResponse
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
from jiratui.widgets.pull_requests import (
    PullRequestsWidget,
    describe_work_item_pull_requests,
    pull_request_badge,
)
from jiratui.widgets.screen import MainScreen
from jiratui.widgets.search import PULL_REQUEST_COLUMN


def _ref(number: int) -> PullRequestRef:
    return PullRequestRef(
        url=f'https://github.com/acme/app/pull/{number}',
        owner='acme',
        repository='app',
        number=number,
    )


def _open_pull_request(number: int = 7, **kwargs) -> PullRequestState:
    default = PullRequestState(
        ref=_ref(number),
        title='Add a thing',
        state='OPEN',
        head_branch='feature/b',
        base_branch='feature/a',
        head_sha='abc',
        checks=CheckSummary(passed=1, pending_names=['build', 'deploy']),
        review_decision='APPROVED',
        review_items=[
            ReviewItem(ReviewSource.TEAMMATE, 'alice', 'comment', 'Why?', answered=False),
            ReviewItem(
                ReviewSource.TEAMMATE, 'bob', 'thread', 'Done', resolved=True, answered=True
            ),
            ReviewItem(ReviewSource.QODO, 'qodo-x', 'thread', 'Bug', path='a.py', line=1),
            ReviewItem(ReviewSource.QODO, 'qodo-x', 'thread', 'Bug 2', path='a.py', line=2),
        ],
        arnica_findings=[ArnicaFinding('Injection', 'Path traversal', 'Medium', 'open', 'b.py', 3)],
        stacked_on='A-1',
    )
    return replace(default, **kwargs)


def _plain(renderable) -> str:
    console = Console(width=200, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def test_badge_of_an_open_pull_request():
    badge = pull_request_badge(WorkItemPullRequests('A-2', [_open_pull_request()]))

    assert badge.plain == '● …1/3 ✔ @1 Q2 ⚠1 ↳A-1'


def test_badge_of_several_and_of_finished_pull_requests():
    two_open = WorkItemPullRequests(
        'A-2',
        [
            _open_pull_request(
                7,
                checks=CheckSummary(passed=3),
                review_items=[],
                arnica_findings=[],
                stacked_on=None,
            ),
            _open_pull_request(
                8,
                checks=CheckSummary(failing_names=['lint (FAILURE)']),
                review_decision='CHANGES_REQUESTED',
                review_items=[],
                arnica_findings=[],
                stacked_on=None,
                is_draft=True,
            ),
        ],
    )
    merged = WorkItemPullRequests('A-3', [PullRequestState(ref=_ref(9), state='MERGED')])

    assert pull_request_badge(two_open).plain == '2× ● ✓3/3 ✔ | ◌ ✗1 ✎'
    assert pull_request_badge(merged).plain == '◆'
    assert (
        pull_request_badge(WorkItemPullRequests('A-4', error='Jira could not be read')).plain == '?'
    )
    assert pull_request_badge(WorkItemPullRequests('A-5')).plain == ''


def test_the_tab_describes_checks_review_and_feedback_by_source():
    pull_request = _open_pull_request(
        qodo=QodoReview(
            available=True,
            status='completed',
            commit_sha='abc',
            findings=[
                QodoFinding(
                    'f1',
                    'Mismatched profile',
                    'remediation_recommended',
                    'Security',
                    'pending',
                    True,
                    'a.py',
                    8,
                ),
                QodoFinding('f2', 'Old finding', 'informational', 'Style', 'implemented', False),
            ],
        )
    )

    text = _plain(describe_work_item_pull_requests(WorkItemPullRequests('A-2', [pull_request])))

    assert '#7 ● Open' in text
    assert 'feature/b → feature/a   stacked on A-1' in text
    assert '… 1/3 passed, running: build, deploy · approved' in text
    assert 'Teammates — 1 unanswered' in text
    assert '● alice comment: Why?' in text
    assert '○ bob thread: Done' in text
    assert 'Qodo — review completed at head, 1 open finding(s)' in text
    assert 'Mismatched profile [pending]' in text
    assert 'Old finding [implemented]' in text
    assert 'Arnica — 1 open' in text
    assert 'Path traversal [open]' in text


def test_the_tab_falls_back_to_qodo_threads_when_its_review_cannot_be_read():
    pull_request = _open_pull_request(
        qodo=QodoReview(available=False, reason='The Qodo CLI is not installed.')
    )

    text = _plain(describe_work_item_pull_requests(WorkItemPullRequests('A-2', [pull_request])))

    assert 'Qodo — The Qodo CLI is not installed.; unresolved threads on GitHub:' in text
    assert '● a.py:2 Bug 2' in text


def _work_item(key: str) -> JiraIssue:
    return JiraIssue(
        id=key.split('-')[1],
        key=key,
        summary=f'summary of {key}',
        status=IssueStatus(id='1', name='Code Review'),
        issue_type=IssueType(id='1', name='Story'),
    )


@pytest.fixture()
def pull_request_app(app):
    app.config.pull_requests = PullRequestsConfiguration(
        application_type='GitHubApp', refresh_seconds=3600
    )
    app.config.views = [WorkItemsViewConfiguration(label='Mine', jql='assignee = currentUser()')]
    app.config.search_results_truncate_work_item_summary = 0
    app.config.search_results_style_work_item_status = False
    app.config.search_results_style_work_item_type = False
    return app


@patch('jiratui.pull_requests.service.PullRequestService.with_qodo_reviews')
@patch('jiratui.pull_requests.service.PullRequestService.summaries')
@patch('jiratui.api_controller.controller.APIController.search_view_work_items')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_loading_results_fills_the_pr_column_and_selecting_fills_the_tab(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    search_view_work_items_mock: AsyncMock,
    summaries_mock: AsyncMock,
    with_qodo_reviews_mock: AsyncMock,
    pull_request_app,
):
    search_view_work_items_mock.return_value = APIControllerResponse(
        result=JiraIssueSearchResponse(issues=[_work_item('A-2'), _work_item('A-3')], is_last=True)
    )
    summaries = {
        'A-2': WorkItemPullRequests('A-2', [_open_pull_request()]),
        'A-3': WorkItemPullRequests('A-3'),
    }
    summaries_mock.return_value = summaries
    with_qodo_reviews_mock.side_effect = lambda work_item: work_item
    async with pull_request_app.run_test() as pilot:
        main_screen = cast('MainScreen', pull_request_app.screen)  # type:ignore[name-defined] # noqa: F821
        # WHEN
        await pilot.press('alt+1')
        await pull_request_app.workers.wait_for_complete()
        await pilot.pause()
        # THEN
        table = main_screen.search_results_table
        assert PULL_REQUEST_COLUMN in table.columns
        assert table.get_cell('2#A-2', PULL_REQUEST_COLUMN).plain == '● …1/3 ✔ @1 Q2 ⚠1 ↳A-1'
        assert summaries_mock.await_args.args[0] == [('2', 'A-2'), ('3', 'A-3')]
        # WHEN
        await main_screen.load_pull_request_details('2', 'A-2')
        await pull_request_app.workers.wait_for_complete()
        # THEN
        widget = main_screen.query_one(PullRequestsWidget)
        assert widget.work_item_key == 'A-2'
        with_qodo_reviews_mock.assert_awaited_with(summaries['A-2'])
        assert summaries_mock.await_count == 1


@patch('jiratui.pull_requests.service.PullRequestService.summaries')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_reload_without_an_active_view_reads_the_pull_requests_again(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    summaries_mock: AsyncMock,
    pull_request_app,
):
    summaries_mock.return_value = {'A-2': WorkItemPullRequests('A-2')}
    async with pull_request_app.run_test() as pilot:
        main_screen = cast('MainScreen', pull_request_app.screen)  # type:ignore[name-defined] # noqa: F821
        results = JiraIssueSearchResponse(issues=[_work_item('A-2')])
        main_screen.search_results_table.set_initial_results_set(results)
        main_screen.search_results_table.search_results = results
        # WHEN
        await pilot.press('alt+r')
        await pull_request_app.workers.wait_for_complete()
        # THEN
        summaries_mock.assert_awaited_once_with([('2', 'A-2')])


@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_no_pr_column_or_tab_without_pull_request_settings(
    fetch_projects_mock, fetch_issue_types_mock, fetch_statuses_mock, app
):
    app.config.search_results_truncate_work_item_summary = 0
    app.config.search_results_style_work_item_status = False
    app.config.search_results_style_work_item_type = False
    async with app.run_test():
        main_screen = cast('MainScreen', app.screen)  # type:ignore[name-defined] # noqa: F821
        main_screen.search_results_table.search_results = JiraIssueSearchResponse(
            issues=[_work_item('A-2')]
        )
        # THEN
        assert PULL_REQUEST_COLUMN not in main_screen.search_results_table.columns
        assert main_screen.pull_requests_widget is None
        assert main_screen.pull_request_service is None
