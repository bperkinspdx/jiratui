from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from jiratui.api_controller.controller import APIControllerResponse
from jiratui.config import StatusSectionConfiguration, WorkItemsViewConfiguration
from jiratui.models import IssueStatus, IssueType, JiraIssue, JiraIssueSearchResponse
from jiratui.widgets.screen import MainScreen
from jiratui.widgets.search import SECTION_ROW_KEY_PREFIX
from jiratui.widgets.views import ViewsBar

SECTIONS = [
    StatusSectionConfiguration(label='Ready', statuses=['Ready']),
    StatusSectionConfiguration(label='Code Review', statuses=['Code Review']),
    StatusSectionConfiguration(label='Done', statuses=['Done', 'Closed'], collapsed=True),
]

VIEWS = [
    WorkItemsViewConfiguration(label='My work', jql='assignee = currentUser()'),
    WorkItemsViewConfiguration(label='Pick up', jql='status = Ready AND assignee is EMPTY'),
]


def _work_item(key: str, status: str) -> JiraIssue:
    return JiraIssue(
        id=key.split('-')[1],
        key=key,
        summary=f'summary of {key}',
        status=IssueStatus(id='1', name=status),
        issue_type=IssueType(id='1', name='Story'),
    )


WORK_ITEMS = [
    _work_item('A-1', 'Code Review'),
    _work_item('A-2', 'Done'),
    _work_item('A-3', 'Ready'),
    _work_item('A-4', 'Code Review'),
    _work_item('A-5', 'Testing'),
]


@pytest.fixture()
def sectioned_app(app):
    app.config.status_sections = SECTIONS
    app.config.search_results_truncate_work_item_summary = 0
    app.config.search_results_style_work_item_status = False
    app.config.search_results_style_work_item_type = False
    return app


def _row_keys(screen: MainScreen) -> list[str]:
    return [str(row.key.value) for row in screen.search_results_table.ordered_rows]


def _first_cells(screen: MainScreen) -> list[str]:
    table = screen.search_results_table
    return [
        f'{table.get_row_at(index)[0]} {table.get_row_at(index)[1]}'
        for index in range(table.row_count)
    ]


@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_results_are_grouped_into_status_sections(
    fetch_projects_mock, fetch_issue_types_mock, fetch_statuses_mock, sectioned_app
):
    async with sectioned_app.run_test():
        main_screen = cast('MainScreen', sectioned_app.screen)  # type:ignore[name-defined] # noqa: F821
        # WHEN
        main_screen.search_results_table.search_results = JiraIssueSearchResponse(issues=WORK_ITEMS)
        # THEN
        assert _row_keys(main_screen) == [
            f'{SECTION_ROW_KEY_PREFIX}0',
            '3#A-3',
            f'{SECTION_ROW_KEY_PREFIX}1',
            '1#A-1',
            '4#A-4',
            f'{SECTION_ROW_KEY_PREFIX}2',
            f'{SECTION_ROW_KEY_PREFIX}3',
            '5#A-5',
        ]
        assert _first_cells(main_screen) == [
            '▼ Ready (1)',
            '1 A-3',
            '▼ Code Review (2)',
            '2 A-1',
            '3 A-4',
            '▶ Done (1)',
            '▼ Testing (1)',
            '5 A-5',
        ]
        assert main_screen.search_results_table.work_item_count == 5


@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_selecting_a_heading_toggles_its_section(
    fetch_projects_mock, fetch_issue_types_mock, fetch_statuses_mock, sectioned_app
):
    async with sectioned_app.run_test() as pilot:
        main_screen = cast('MainScreen', sectioned_app.screen)  # type:ignore[name-defined] # noqa: F821
        table = main_screen.search_results_table
        table.search_results = JiraIssueSearchResponse(issues=WORK_ITEMS)
        table.focus()
        done_heading = f'{SECTION_ROW_KEY_PREFIX}2'
        table.move_cursor(row=table.get_row_index(done_heading))
        await pilot.pause()
        # WHEN
        await pilot.press('enter')
        await pilot.pause()
        # THEN
        assert '2#A-2' in _row_keys(main_screen)
        assert table.cursor_row == table.get_row_index(done_heading)
        assert table.current_work_item_key is None
        # WHEN
        await pilot.press('enter')
        await pilot.pause()
        # THEN
        assert '2#A-2' not in _row_keys(main_screen)


@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_row_index_for_position_follows_the_numbers_on_display(
    fetch_projects_mock, fetch_issue_types_mock, fetch_statuses_mock, sectioned_app
):
    async with sectioned_app.run_test():
        main_screen = cast('MainScreen', sectioned_app.screen)  # type:ignore[name-defined] # noqa: F821
        table = main_screen.search_results_table
        table.search_results = JiraIssueSearchResponse(issues=WORK_ITEMS)
        # THEN
        assert [table.row_index_for_position(position) for position in range(0, 7)] == [
            None,
            1,
            3,
            4,
            None,
            7,
            None,
        ]


@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_results_are_not_grouped_without_status_sections(
    fetch_projects_mock, fetch_issue_types_mock, fetch_statuses_mock, sectioned_app
):
    sectioned_app.config.status_sections = None
    async with sectioned_app.run_test():
        main_screen = cast('MainScreen', sectioned_app.screen)  # type:ignore[name-defined] # noqa: F821
        # WHEN
        main_screen.search_results_table.search_results = JiraIssueSearchResponse(issues=WORK_ITEMS)
        # THEN
        assert _row_keys(main_screen) == ['1#A-1', '2#A-2', '3#A-3', '4#A-4', '5#A-5']


@patch('jiratui.api_controller.controller.APIController.search_view_work_items')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_loading_a_view_shows_its_work_items_and_marks_it_active(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    search_view_work_items_mock: AsyncMock,
    sectioned_app,
):
    sectioned_app.config.views = VIEWS
    sectioned_app.config.show_keybinding_hints = True
    search_view_work_items_mock.return_value = APIControllerResponse(
        result=JiraIssueSearchResponse(issues=WORK_ITEMS, is_last=True, total=5)
    )
    async with sectioned_app.run_test() as pilot:
        main_screen = cast('MainScreen', sectioned_app.screen)  # type:ignore[name-defined] # noqa: F821
        # WHEN
        await pilot.press('alt+2')
        await sectioned_app.workers.wait_for_complete()
        await pilot.pause()
        # THEN
        search_view_work_items_mock.assert_awaited_once()
        assert search_view_work_items_mock.await_args.args[0] == VIEWS[1]
        assert search_view_work_items_mock.await_args.kwargs['max_results'] == 200
        assert main_screen.active_view == 2
        assert '3#A-3' in _row_keys(main_screen)
        views_bar = main_screen.query_one(ViewsBar)
        assert views_bar.active_view == 2
        assert views_bar.describe_view(2) == 'alt+2 Pick up (5)'
        assert views_bar.describe_view(1) == 'alt+1 My work'
        assert main_screen.search_results_container.border_title.endswith('· Pick up')
        assert main_screen.search_results_container.border_subtitle == 'Total: 5'
        assert main_screen.search_results_table.token_by_page == {}


@patch('jiratui.widgets.screen.MainScreen._search_work_items')
@patch('jiratui.api_controller.controller.APIController.search_view_work_items')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_a_search_replaces_the_active_view(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    search_view_work_items_mock: AsyncMock,
    search_work_items_mock: AsyncMock,
    sectioned_app,
):
    from jiratui.widgets.screen import WorkItemSearchResult

    sectioned_app.config.views = VIEWS
    sectioned_app.config.pre_defined_jql_expressions = None
    sectioned_app.config.search_results_per_page = 30
    search_view_work_items_mock.return_value = APIControllerResponse(
        result=JiraIssueSearchResponse(issues=WORK_ITEMS, is_last=True)
    )
    search_work_items_mock.return_value = WorkItemSearchResult(
        response=JiraIssueSearchResponse(issues=WORK_ITEMS[:1]), total=1, start=1, end=1
    )
    async with sectioned_app.run_test() as pilot:
        main_screen = cast('MainScreen', sectioned_app.screen)  # type:ignore[name-defined] # noqa: F821
        await pilot.press('alt+1')
        await sectioned_app.workers.wait_for_complete()
        # WHEN
        await main_screen.action_search()
        await sectioned_app.workers.wait_for_complete()
        await pilot.pause()
        # THEN
        assert main_screen.active_view is None
        assert main_screen.query_one(ViewsBar).active_view is None
        assert _row_keys(main_screen) == [f'{SECTION_ROW_KEY_PREFIX}0', '1#A-1']


@patch('jiratui.api_controller.controller.APIController.search_view_work_items')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_a_view_holding_more_than_max_results_warns(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    search_view_work_items_mock: AsyncMock,
    sectioned_app,
):
    sectioned_app.config.views = VIEWS
    search_view_work_items_mock.return_value = APIControllerResponse(
        result=JiraIssueSearchResponse(issues=WORK_ITEMS, is_last=False)
    )
    async with sectioned_app.run_test() as pilot:
        # WHEN
        await pilot.press('alt+1')
        await sectioned_app.workers.wait_for_complete()
        await pilot.pause()
        # THEN
        assert any(
            'Raise view_max_results' in str(notification.message)
            for notification in sectioned_app._notifications
        )


@patch('jiratui.api_controller.controller.APIController.search_view_work_items')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_the_startup_view_loads_when_the_app_starts(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    search_view_work_items_mock: AsyncMock,
    sectioned_app,
):
    sectioned_app.config.views = VIEWS
    sectioned_app.config.startup_view = 1
    search_view_work_items_mock.return_value = APIControllerResponse(
        result=JiraIssueSearchResponse(issues=WORK_ITEMS, is_last=True)
    )
    async with sectioned_app.run_test() as pilot:
        await sectioned_app.workers.wait_for_complete()
        await pilot.pause()
        main_screen = cast('MainScreen', sectioned_app.screen)  # type:ignore[name-defined] # noqa: F821
        # THEN
        search_view_work_items_mock.assert_awaited_once()
        assert search_view_work_items_mock.await_args.args[0] == VIEWS[0]
        assert main_screen.active_view == 1


@patch('jiratui.api_controller.controller.APIController.search_view_work_items')
@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_a_startup_view_past_the_configured_views_warns_and_loads_nothing(
    fetch_projects_mock,
    fetch_issue_types_mock,
    fetch_statuses_mock,
    search_view_work_items_mock: AsyncMock,
    sectioned_app,
):
    sectioned_app.config.views = VIEWS
    sectioned_app.config.startup_view = 3
    async with sectioned_app.run_test() as pilot:
        await sectioned_app.workers.wait_for_complete()
        await pilot.pause()
        # THEN
        search_view_work_items_mock.assert_not_awaited()
        assert any(
            'startup_view is 3 but 2 views are configured' in str(notification.message)
            for notification in sectioned_app._notifications
        )


@patch('jiratui.widgets.screen.MainScreen.fetch_statuses')
@patch('jiratui.widgets.screen.MainScreen.fetch_issue_types')
@patch('jiratui.widgets.screen.MainScreen.fetch_projects')
@pytest.mark.asyncio
async def test_no_views_bar_without_views(
    fetch_projects_mock, fetch_issue_types_mock, fetch_statuses_mock, sectioned_app
):
    async with sectioned_app.run_test():
        main_screen = cast('MainScreen', sectioned_app.screen)  # type:ignore[name-defined] # noqa: F821
        # THEN
        assert main_screen.views_bar is None
