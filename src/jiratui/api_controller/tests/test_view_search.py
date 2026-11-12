from unittest.mock import AsyncMock, call, patch

import pytest

from jiratui.api_controller.controller import (
    VIEW_SEARCH_CLOUD_PAGE_SIZE,
    APIController,
    APIControllerResponse,
)
from jiratui.config import WorkItemsViewConfiguration
from jiratui.constants import ISSUE_SEARCH_DEFAULT_MAX_RESULTS
from jiratui.models import (
    IssueStatus,
    IssueType,
    JiraIssue,
    JiraIssueSearchResponse,
    RelatedJiraIssue,
)
from jiratui.utils.search import DEFAULT_WORK_ITEM_SEARCH_FIELDS
from jiratui.utils.views import DEFAULT_VIEW_ORDER_BY


def _work_item(key: str, linked: list[str] | None = None) -> JiraIssue:
    return JiraIssue(
        id=key.split('-')[1],
        key=key,
        summary=key,
        status=IssueStatus(id='1', name='Ready'),
        related_issues=[
            RelatedJiraIssue(
                id=linked_key.split('-')[1],
                key=linked_key,
                summary=linked_key,
                status=IssueStatus(id='1', name='Ready'),
                issue_type=IssueType(id='1', name='Bug'),
            )
            for linked_key in linked or []
        ],
    )


def _page(keys: list[str], **kwargs) -> APIControllerResponse:
    return APIControllerResponse(
        result=JiraIssueSearchResponse(issues=[_work_item(key) for key in keys], **kwargs)
    )


@pytest.mark.asyncio
@patch.object(APIController, 'search_issues', new_callable=AsyncMock)
async def test_view_search_reads_every_cloud_page(
    search_issues_mock: AsyncMock, jira_api_controller: APIController
):
    # GIVEN
    search_issues_mock.side_effect = [
        _page(['A-1', 'A-2'], next_page_token='t2', is_last=False),
        _page(['A-3'], next_page_token=None, is_last=True),
    ]
    view = WorkItemsViewConfiguration(label='Mine', jql='assignee = currentUser()')
    # WHEN
    response = await jira_api_controller.search_view_work_items(view, fields=['id', 'key'])
    # THEN
    assert response.success is True
    assert [work_item.key for work_item in response.result.issues] == ['A-1', 'A-2', 'A-3']
    assert response.result.is_last is True
    assert response.result.total == 3
    jql = f'assignee = currentUser() {DEFAULT_VIEW_ORDER_BY}'
    assert search_issues_mock.await_args_list == [
        call(
            jql_query=jql,
            fields=['id', 'key'],
            next_page_token=None,
            limit=VIEW_SEARCH_CLOUD_PAGE_SIZE,
        ),
        call(
            jql_query=jql,
            fields=['id', 'key'],
            next_page_token='t2',
            limit=VIEW_SEARCH_CLOUD_PAGE_SIZE,
        ),
    ]


@pytest.mark.asyncio
@patch.object(APIController, 'search_issues', new_callable=AsyncMock)
async def test_view_search_stops_at_max_results(
    search_issues_mock: AsyncMock, jira_api_controller: APIController
):
    # GIVEN
    search_issues_mock.side_effect = [
        _page(['A-1', 'A-2', 'A-3'], next_page_token='t2', is_last=False),
    ]
    view = WorkItemsViewConfiguration(label='Mine', jql='assignee = currentUser()')
    # WHEN
    response = await jira_api_controller.search_view_work_items(view, max_results=2)
    # THEN
    assert [work_item.key for work_item in response.result.issues] == ['A-1', 'A-2']
    assert response.result.is_last is False
    search_issues_mock.assert_awaited_once()


@pytest.mark.asyncio
@patch.object(APIController, 'search_issues_by_page_number', new_callable=AsyncMock)
async def test_view_search_reads_every_data_center_page(
    search_issues_by_page_number_mock: AsyncMock, jira_api_controller_for_jira_dc: APIController
):
    # GIVEN
    search_issues_by_page_number_mock.side_effect = [
        _page(['A-1', 'A-2'], total=3, offset=0),
        _page(['A-3'], total=3, offset=2),
    ]
    view = WorkItemsViewConfiguration(label='Mine', jql='project = A ORDER BY key')
    # WHEN
    response = await jira_api_controller_for_jira_dc.search_view_work_items(view)
    # THEN
    assert [work_item.key for work_item in response.result.issues] == ['A-1', 'A-2', 'A-3']
    assert response.result.is_last is True
    assert search_issues_by_page_number_mock.await_args_list == [
        call(
            jql_query='project = A ORDER BY key',
            fields=DEFAULT_WORK_ITEM_SEARCH_FIELDS,
            page=page,
            limit=ISSUE_SEARCH_DEFAULT_MAX_RESULTS,
        )
        for page in (1, 2)
    ]


@pytest.mark.asyncio
@patch.object(APIController, 'search_issues', new_callable=AsyncMock)
async def test_related_view_searches_sub_tasks_and_linked_work_items_of_the_base(
    search_issues_mock: AsyncMock, jira_api_controller: APIController
):
    # GIVEN
    search_issues_mock.side_effect = [
        APIControllerResponse(
            result=JiraIssueSearchResponse(
                issues=[_work_item('A-1', linked=['QA-5']), _work_item('A-2')], is_last=True
            )
        ),
        _page(['QA-5', 'A-9'], is_last=True),
    ]
    view = WorkItemsViewConfiguration(
        label='Bugs on my work', related_to='assignee = currentUser()', jql='issuetype = Bug'
    )
    # WHEN
    response = await jira_api_controller.search_view_work_items(view, fields=['id', 'key'])
    # THEN
    assert [work_item.key for work_item in response.result.issues] == ['QA-5', 'A-9']
    base_call, related_call = search_issues_mock.await_args_list
    assert base_call.kwargs['jql_query'] == 'assignee = currentUser()'
    assert base_call.kwargs['fields'] == [*DEFAULT_WORK_ITEM_SEARCH_FIELDS, 'issuelinks']
    assert related_call.kwargs['jql_query'] == (
        f'(parent in (A-1, A-2) OR key in (QA-5)) AND (issuetype = Bug) {DEFAULT_VIEW_ORDER_BY}'
    )
    assert related_call.kwargs['fields'] == ['id', 'key']


@pytest.mark.asyncio
@patch.object(APIController, 'search_issues', new_callable=AsyncMock)
async def test_related_view_with_an_empty_base_searches_nothing_more(
    search_issues_mock: AsyncMock, jira_api_controller: APIController
):
    # GIVEN
    search_issues_mock.side_effect = [_page([], is_last=True)]
    view = WorkItemsViewConfiguration(
        label='Bugs on my work', related_to='assignee = currentUser()', jql='issuetype = Bug'
    )
    # WHEN
    response = await jira_api_controller.search_view_work_items(view)
    # THEN
    assert response.success is True
    assert response.result.issues == []
    assert response.result.is_last is True
    search_issues_mock.assert_awaited_once()


@pytest.mark.asyncio
@patch.object(APIController, 'search_issues', new_callable=AsyncMock)
async def test_view_search_returns_the_failure_of_a_page(
    search_issues_mock: AsyncMock, jira_api_controller: APIController
):
    # GIVEN
    search_issues_mock.side_effect = [
        _page(['A-1'], next_page_token='t2', is_last=False),
        APIControllerResponse(success=False, error='boom'),
    ]
    view = WorkItemsViewConfiguration(label='Mine', jql='assignee = currentUser()')
    # WHEN
    response = await jira_api_controller.search_view_work_items(view)
    # THEN
    assert response.success is False
    assert response.error == 'boom'
