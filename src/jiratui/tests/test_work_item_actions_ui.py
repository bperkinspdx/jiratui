from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiratui.api_controller.controller import APIControllerResponse
from jiratui.config import (
    PullRequestsConfiguration,
    StatusSectionConfiguration,
    WorkItemActionsConfiguration,
)
from jiratui.models import (
    IssueStatus,
    IssueTransition,
    IssueTransitionState,
    IssueType,
    JiraIssue,
    JiraIssueSearchResponse,
)
from jiratui.pull_requests.models import PullRequestRef, PullRequestState, WorkItemPullRequests
from jiratui.widgets.screen import MainScreen
from jiratui.widgets.screens.repository_picker import RepositoryPickerScreen


def _work_item(key: str, status: str) -> JiraIssue:
    return JiraIssue(
        id=key.split('-')[1],
        key=key,
        summary=f'summary of {key}',
        status=IssueStatus(id='1', name=status),
        issue_type=IssueType(id='1', name='Story'),
    )


def _transition(transition_id: str, status: str) -> IssueTransition:
    return IssueTransition(
        id=transition_id,
        name=status,
        to_state=IssueTransitionState(id=f's{transition_id}', name=status),
    )


@pytest.fixture()
def actions_app(app, tmp_path):
    app.config.jira_account_id = 'account-1'
    app.config.search_results_truncate_work_item_summary = 0
    app.config.search_results_style_work_item_status = False
    app.config.search_results_style_work_item_type = False
    app.config.search_results_per_page = 30
    app.config.status_sections = [
        StatusSectionConfiguration(label='Ready', statuses=['Ready']),
        StatusSectionConfiguration(label='In Progress', statuses=['In Progress']),
        StatusSectionConfiguration(label='Code Review', statuses=['Code Review']),
        StatusSectionConfiguration(label='Testing', statuses=['Ready for Testing']),
    ]
    app.config.work_item_actions = WorkItemActionsConfiguration(
        claude_command='claude "/flow {key}"',
        repositories={'core': str(tmp_path / 'core'), 'v7': str(tmp_path / 'v7')},
    )
    (tmp_path / 'core').mkdir()
    (tmp_path / 'v7').mkdir()
    return app


async def _show(main_screen: MainScreen, work_item: JiraIssue, pilot) -> None:
    results = JiraIssueSearchResponse(issues=[work_item])
    table = main_screen.search_results_table
    table.set_initial_results_set(results)
    table.search_results = results
    table.focus()
    table.move_cursor(row=table.get_row_index(f'{work_item.id}#{work_item.key}'))
    await pilot.pause()


@patch('jiratui.widgets.screen.MainScreen._reload_results')
@patch('jiratui.api_controller.controller.APIController.transition_issue_status')
@patch('jiratui.api_controller.controller.APIController.transitions')
@patch('jiratui.api_controller.controller.APIController.assign_work_item')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_pick_up_assigns_then_moves_to_in_progress(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    assign_mock: AsyncMock,
    transitions_mock: AsyncMock,
    transition_issue_status_mock: AsyncMock,
    reload_mock: MagicMock,
    actions_app,
):
    assign_mock.return_value = APIControllerResponse()
    transitions_mock.return_value = APIControllerResponse(
        result=[_transition('5', 'Backlog'), _transition('7', 'In Progress')]
    )
    transition_issue_status_mock.return_value = APIControllerResponse()
    async with actions_app.run_test() as pilot:
        main_screen = cast('MainScreen', actions_app.screen)  # type:ignore[name-defined] # noqa: F821
        await _show(main_screen, _work_item('A-1', 'Ready'), pilot)
        # WHEN
        await pilot.press('P')
        await actions_app.workers.wait_for_complete()
        # THEN
        assign_mock.assert_awaited_once_with('A-1', 'account-1')
        transition_issue_status_mock.assert_awaited_once_with('A-1', 's7')
        reload_mock.assert_called_once()


@patch('jiratui.widgets.screen.MainScreen._reload_results')
@patch('jiratui.api_controller.controller.APIController.transition_issue_status')
@patch('jiratui.api_controller.controller.APIController.assign_work_item')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_pick_up_of_an_item_in_progress_only_assigns_it(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    assign_mock: AsyncMock,
    transition_issue_status_mock: AsyncMock,
    reload_mock: MagicMock,
    actions_app,
):
    assign_mock.return_value = APIControllerResponse()
    async with actions_app.run_test() as pilot:
        main_screen = cast('MainScreen', actions_app.screen)  # type:ignore[name-defined] # noqa: F821
        await _show(main_screen, _work_item('A-1', 'In Progress'), pilot)
        # WHEN
        await pilot.press('P')
        await actions_app.workers.wait_for_complete()
        # THEN
        assign_mock.assert_awaited_once_with('A-1', 'account-1')
        transition_issue_status_mock.assert_not_awaited()


@pytest.mark.parametrize(
    'key, expected_status_id', [('greater_than_sign', 's13'), ('less_than_sign', 's10')]
)
@patch('jiratui.widgets.screen.MainScreen._reload_results')
@patch('jiratui.api_controller.controller.APIController.transition_issue_status')
@patch('jiratui.api_controller.controller.APIController.transitions')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_moving_along_the_board(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    transitions_mock: AsyncMock,
    transition_issue_status_mock: AsyncMock,
    reload_mock: MagicMock,
    key: str,
    expected_status_id: str,
    actions_app,
):
    transitions_mock.return_value = APIControllerResponse(
        result=[
            _transition('11', 'Backlog'),
            _transition('13', 'Ready for Testing'),
            _transition('10', 'In Progress'),
        ]
    )
    transition_issue_status_mock.return_value = APIControllerResponse()
    async with actions_app.run_test() as pilot:
        main_screen = cast('MainScreen', actions_app.screen)  # type:ignore[name-defined] # noqa: F821
        await _show(main_screen, _work_item('A-1', 'Code Review'), pilot)
        # WHEN
        await pilot.press(key)
        await actions_app.workers.wait_for_complete()
        # THEN
        transition_issue_status_mock.assert_awaited_once_with('A-1', expected_status_id)
        reload_mock.assert_called_once()


@patch('jiratui.api_controller.controller.APIController.assign_work_item')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_actions_need_a_highlighted_work_item(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    assign_mock: AsyncMock,
    actions_app,
):
    async with actions_app.run_test() as pilot:
        # WHEN
        await pilot.press('P')
        await actions_app.workers.wait_for_complete()
        # THEN
        assign_mock.assert_not_awaited()
        assert any('Highlight a work item' in str(n.message) for n in actions_app._notifications)


def _with_pull_request(app, repository: str = 'core') -> WorkItemPullRequests:
    app.config.pull_requests = PullRequestsConfiguration(refresh_seconds=3600)
    return WorkItemPullRequests(
        'A-1',
        [
            PullRequestState(
                ref=PullRequestRef(
                    url=f'https://github.com/acme/{repository}/pull/4',
                    owner='acme',
                    repository=repository,
                    number=4,
                    jira_status='OPEN',
                ),
                state='OPEN',
            )
        ],
    )


@patch('jiratui.pull_requests.service.PullRequestService.summaries')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_open_pull_request_opens_its_url(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    summaries_mock: AsyncMock,
    actions_app,
):
    summaries_mock.return_value = {'A-1': _with_pull_request(actions_app)}
    async with actions_app.run_test() as pilot:
        main_screen = cast('MainScreen', actions_app.screen)  # type:ignore[name-defined] # noqa: F821
        actions_app.open_url = MagicMock()
        await _show(main_screen, _work_item('A-1', 'Code Review'), pilot)
        # WHEN
        await pilot.press('O')
        await actions_app.workers.wait_for_complete()
        # THEN
        actions_app.open_url.assert_called_once_with('https://github.com/acme/core/pull/4')


@patch('jiratui.widgets.screen.subprocess.Popen')
@patch('jiratui.pull_requests.service.PullRequestService.summaries')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_hand_to_claude_starts_in_the_pull_request_repository(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    summaries_mock: AsyncMock,
    popen_mock: MagicMock,
    actions_app,
    tmp_path,
):
    summaries_mock.return_value = {'A-1': _with_pull_request(actions_app, 'v7')}
    async with actions_app.run_test() as pilot:
        main_screen = cast('MainScreen', actions_app.screen)  # type:ignore[name-defined] # noqa: F821
        await _show(main_screen, _work_item('A-1', 'In Progress'), pilot)
        with patch('jiratui.widgets.screen.shutil.which', side_effect=lambda name: f'{name}.exe'):
            # WHEN
            await pilot.press('C')
            await actions_app.workers.wait_for_complete()
        # THEN
        arguments = popen_mock.call_args.args[0]
        assert arguments[0] == 'wt.exe'
        assert arguments[arguments.index('--startingDirectory') + 1] == str(tmp_path / 'v7')
        assert arguments[arguments.index('--title') + 1] == 'A-1'


@patch('jiratui.widgets.screen.subprocess.Popen')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_hand_to_claude_without_a_pull_request_asks_for_the_repository(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    popen_mock: MagicMock,
    actions_app,
    tmp_path,
):
    async with actions_app.run_test() as pilot:
        main_screen = cast('MainScreen', actions_app.screen)  # type:ignore[name-defined] # noqa: F821
        await _show(main_screen, _work_item('A-1', 'Ready'), pilot)
        with patch('jiratui.widgets.screen.shutil.which', side_effect=lambda name: f'{name}.exe'):
            # WHEN
            await pilot.press('C')
            await actions_app.workers.wait_for_complete()
            await pilot.pause()
            # THEN
            assert isinstance(actions_app.screen, RepositoryPickerScreen)
            # WHEN the first, default repository is chosen
            await pilot.press('enter')
            await pilot.pause()
        # THEN
        arguments = popen_mock.call_args.args[0]
        assert arguments[arguments.index('--startingDirectory') + 1] == str(tmp_path / 'core')


@patch('jiratui.widgets.screen.subprocess.Popen')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_hand_to_claude_refuses_items_in_other_statuses(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    popen_mock: MagicMock,
    actions_app,
):
    async with actions_app.run_test() as pilot:
        main_screen = cast('MainScreen', actions_app.screen)  # type:ignore[name-defined] # noqa: F821
        await _show(main_screen, _work_item('A-1', 'Code Review'), pilot)
        # WHEN
        await pilot.press('C')
        await actions_app.workers.wait_for_complete()
        # THEN
        popen_mock.assert_not_called()
        assert any(
            'Claude Code takes Ready, In Progress work items' in str(n.message)
            for n in actions_app._notifications
        )
