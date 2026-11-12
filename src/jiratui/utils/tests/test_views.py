import pytest

from jiratui.config import StatusSectionConfiguration
from jiratui.models import IssueStatus, JiraIssue
from jiratui.utils.views import (
    DEFAULT_VIEW_ORDER_BY,
    build_related_view_jql,
    build_view_jql,
    group_work_items_by_status,
    split_order_by,
)


def _work_item(key: str, status: str) -> JiraIssue:
    return JiraIssue(
        id=key.split('-')[1], key=key, summary=key, status=IssueStatus(id='1', name=status)
    )


@pytest.mark.parametrize(
    'jql, expected',
    [
        ('assignee = currentUser()', ('assignee = currentUser()', '')),
        (
            'assignee = currentUser() ORDER BY created DESC',
            ('assignee = currentUser()', 'ORDER BY created DESC'),
        ),
        ('project = A order   by key asc', ('project = A', 'order   by key asc')),
        ('summary ~ "order by" AND project = A', ('summary ~ "order by" AND project = A', '')),
        (
            "summary ~ 'sort order by date' ORDER BY rank",
            ("summary ~ 'sort order by date'", 'ORDER BY rank'),
        ),
        ('summary ~ "say \\"order by\\"" ', ('summary ~ "say \\"order by\\""', '')),
        ('  ', ('', '')),
    ],
)
def test_split_order_by(jql: str, expected: tuple[str, str]):
    assert split_order_by(jql) == expected


def test_build_view_jql_orders_by_rank_when_the_view_has_no_order():
    assert build_view_jql('assignee = currentUser()') == (
        f'assignee = currentUser() {DEFAULT_VIEW_ORDER_BY}'
    )


def test_build_view_jql_keeps_the_view_order():
    assert build_view_jql('project = A ORDER BY key DESC') == 'project = A ORDER BY key DESC'


def test_build_related_view_jql_without_base_work_items():
    assert build_related_view_jql('issuetype = Bug', parent_keys=[], linked_keys=['A-9']) is None


def test_build_related_view_jql_with_sub_tasks_and_links():
    assert build_related_view_jql(
        'issuetype = Bug', parent_keys=['A-1', 'A-2'], linked_keys=['B-7', 'A-2', 'B-3', 'B-7']
    ) == (
        f'(parent in (A-1, A-2) OR key in (B-3, B-7)) AND (issuetype = Bug) {DEFAULT_VIEW_ORDER_BY}'
    )


def test_build_related_view_jql_without_links_or_filter_keeps_the_view_order():
    assert build_related_view_jql('ORDER BY created DESC', parent_keys=['A-1'], linked_keys=[]) == (
        '(parent in (A-1)) ORDER BY created DESC'
    )


def test_group_work_items_by_status_follows_the_configured_order():
    sections = [
        StatusSectionConfiguration(label='Ready', statuses=['Ready', 'To Do']),
        StatusSectionConfiguration(label='Doing', statuses=['In Progress']),
        StatusSectionConfiguration(label='Done', statuses=['Done', 'Closed'], collapsed=True),
    ]
    work_items = [
        _work_item('A-1', 'Closed'),
        _work_item('A-2', 'to do'),
        _work_item('A-3', 'Ready'),
        _work_item('A-4', 'Done'),
    ]

    grouped = group_work_items_by_status(work_items, sections)

    assert [(section.label, section.collapsed) for section in grouped] == [
        ('Ready', False),
        ('Done', True),
    ]
    assert [work_item.key for work_item in grouped[0].work_items] == ['A-2', 'A-3']
    assert [work_item.key for work_item in grouped[1].work_items] == ['A-1', 'A-4']


def test_group_work_items_by_status_gives_unconfigured_statuses_their_own_sections():
    sections = [StatusSectionConfiguration(label='Doing', statuses=['In Progress'])]
    work_items = [
        _work_item('A-1', 'Code Review'),
        _work_item('A-2', 'In Progress'),
        _work_item('A-3', 'Testing'),
        _work_item('A-4', 'CODE REVIEW'),
    ]

    grouped = group_work_items_by_status(work_items, sections)

    assert [section.label for section in grouped] == ['Doing', 'Code Review', 'Testing']
    assert [work_item.key for work_item in grouped[1].work_items] == ['A-1', 'A-4']
