from unittest.mock import AsyncMock

import pytest

from jiratui.exceptions import ServiceUnavailableException
from jiratui.pull_requests.models import QodoReview
from jiratui.pull_requests.service import GitHubClient, PullRequestService, QodoClient


def _dev_status(*numbers: int, status: str = 'OPEN') -> dict:
    return {
        'detail': [
            {
                'pullRequests': [
                    {'url': f'https://github.com/acme/app/pull/{number}', 'status': status}
                    for number in numbers
                ]
            }
        ]
    }


def _node(state: str = 'OPEN', head: str = 'feature/a', base: str = 'main') -> dict:
    return {
        'title': 'A change',
        'state': state,
        'headRefName': head,
        'baseRefName': base,
        'headRefOid': 'abc',
        'author': {'login': 'ben'},
    }


@pytest.fixture
def jira() -> AsyncMock:
    client = AsyncMock()
    client.make_request = AsyncMock()
    return client


@pytest.fixture
def github() -> GitHubClient:
    client = GitHubClient()
    client.fetch = AsyncMock()  # type:ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_summaries_read_jira_then_github_and_link_stacks(jira, github):
    jira.make_request.side_effect = [_dev_status(1), _dev_status(2), _dev_status()]
    github.fetch.return_value = {
        'https://github.com/acme/app/pull/1': _node(head='feature/one'),
        'https://github.com/acme/app/pull/2': _node(head='feature/two', base='feature/one'),
    }
    service = PullRequestService(jira, 'https://site.example/', 'GitHubApp', github=github)

    summaries = await service.summaries([('10', 'A-1'), ('11', 'A-2'), ('12', 'A-3')])

    assert list(summaries) == ['A-1', 'A-2', 'A-3']
    assert summaries['A-2'].pull_requests[0].stacked_on == 'A-1'
    assert summaries['A-3'].pull_requests == []
    first_url = jira.make_request.await_args_list[0].kwargs['url']
    assert first_url == (
        'https://site.example/rest/dev-status/latest/issue/detail'
        '?issueId=10&applicationType=GitHubApp&dataType=pullrequest'
    )


@pytest.mark.asyncio
async def test_summaries_report_a_jira_failure_per_work_item(jira, github):
    jira.make_request.side_effect = [ServiceUnavailableException('down'), _dev_status()]
    github.fetch.return_value = {}
    service = PullRequestService(jira, 'https://site.example', 'GitHub', github=github)

    summaries = await service.summaries([('10', 'A-1'), ('11', 'A-2')])

    assert summaries['A-1'].error == 'Jira could not be read: down'
    assert summaries['A-2'].error is None


@pytest.mark.asyncio
async def test_summaries_report_a_github_failure_per_pull_request(jira, github):
    jira.make_request.return_value = _dev_status(1)
    github.fetch.return_value = {
        'https://github.com/acme/app/pull/1': 'GitHub could not be read: boom'
    }
    service = PullRequestService(jira, 'https://site.example', 'GitHub', github=github)

    summaries = await service.summaries([('10', 'A-1')])

    assert summaries['A-1'].pull_requests[0].error == 'GitHub could not be read: boom'


@pytest.mark.asyncio
async def test_merged_pull_requests_are_read_from_github_once(jira, github):
    jira.make_request.return_value = _dev_status(1, status='MERGED')
    github.fetch.side_effect = [{'https://github.com/acme/app/pull/1': _node(state='MERGED')}, {}]
    service = PullRequestService(jira, 'https://site.example', 'GitHub', github=github)

    await service.summaries([('10', 'A-1')])
    summaries = await service.summaries([('10', 'A-1')])

    assert summaries['A-1'].pull_requests[0].state == 'MERGED'
    assert github.fetch.await_args_list[1].args[0] == []


@pytest.mark.asyncio
async def test_with_qodo_reviews_reads_only_open_pull_requests(jira, github):
    jira.make_request.return_value = _dev_status(1, 2)
    github.fetch.return_value = {
        'https://github.com/acme/app/pull/1': _node(),
        'https://github.com/acme/app/pull/2': _node(state='MERGED', head='feature/b'),
    }
    qodo = QodoClient()
    qodo.review = AsyncMock(return_value=QodoReview(available=True, status='completed'))  # type:ignore[method-assign]
    service = PullRequestService(jira, 'https://site.example', 'GitHub', github=github, qodo=qodo)
    summary = (await service.summaries([('10', 'A-1')]))['A-1']

    detailed = await service.with_qodo_reviews(summary)

    assert [pull_request.qodo is not None for pull_request in detailed.pull_requests] == [
        True,
        False,
    ]
    qodo.review.assert_awaited_once_with('https://github.com/acme/app/pull/1')
    assert all(pull_request.qodo is None for pull_request in summary.pull_requests)


@pytest.mark.asyncio
async def test_github_fetch_reports_a_missing_token_for_every_pull_request(monkeypatch):
    from jiratui.pull_requests.models import PullRequestRef

    monkeypatch.delenv('GH_TOKEN', raising=False)
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    monkeypatch.setattr('jiratui.pull_requests.service.shutil.which', lambda name: None)
    ref = PullRequestRef(
        url='https://github.com/acme/app/pull/1', owner='acme', repository='app', number=1
    )

    results = await GitHubClient().fetch([ref])

    assert results == {ref.url: 'GitHub CLI (gh) is not installed and GH_TOKEN is not set.'}
