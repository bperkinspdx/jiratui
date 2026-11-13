import base64

from jiratui.config import StatusSectionConfiguration
from jiratui.models import IssueTransition, IssueTransitionState
from jiratui.pull_requests.models import PullRequestRef, PullRequestState, WorkItemPullRequests
from jiratui.work_item_actions import (
    claude_launch_arguments,
    find_column_transition,
    find_transition_to_status,
    pull_request_repository,
    pull_request_urls,
)

SECTIONS = [
    StatusSectionConfiguration(label='Backlog', statuses=['Backlog']),
    StatusSectionConfiguration(label='Ready', statuses=['Ready', 'To Do']),
    StatusSectionConfiguration(label='Blocked', labels=['Blocked']),
    StatusSectionConfiguration(label='In Progress', statuses=['In Progress']),
    StatusSectionConfiguration(label='Code Review', statuses=['Code Review']),
    StatusSectionConfiguration(label='Ready for Testing', statuses=['Ready for Testing']),
    StatusSectionConfiguration(label='Done', statuses=['Done'], collapsed=True),
]


def _transition(transition_id: str, status: str) -> IssueTransition:
    return IssueTransition(
        id=transition_id,
        name=f'to {status}',
        to_state=IssueTransitionState(id=f's{transition_id}', name=status),
    )


CODE_REVIEW_TRANSITIONS = [
    _transition('11', 'Backlog'),
    _transition('14', "Won't do"),
    _transition('13', 'Ready for Testing'),
    _transition('10', 'In Progress'),
]


def test_find_transition_to_status_ignores_case():
    assert find_transition_to_status(CODE_REVIEW_TRANSITIONS, 'in progress').id == '10'
    assert find_transition_to_status(CODE_REVIEW_TRANSITIONS, 'Testing') is None


def test_the_next_and_previous_columns_from_code_review():
    assert find_column_transition('Code Review', SECTIONS, CODE_REVIEW_TRANSITIONS, 1).id == '13'
    assert find_column_transition('code review', SECTIONS, CODE_REVIEW_TRANSITIONS, -1).id == '10'


def test_a_move_never_jumps_past_an_unreachable_adjacent_column():
    transitions = [
        _transition('21', 'Code Review'),
        _transition('22', 'Backlog'),
        _transition('23', 'Done'),
    ]

    assert find_column_transition('To Do', SECTIONS, transitions, 1) is None
    assert find_column_transition('To Do', SECTIONS, transitions, -1).id == '22'
    assert find_column_transition('Backlog', SECTIONS, [_transition('14', "Won't do")], 1) is None


def test_the_adjacent_column_s_statuses_are_tried_in_their_configured_order():
    sections = [
        StatusSectionConfiguration(label='Review', statuses=['PM Review']),
        StatusSectionConfiguration(label='Done', statuses=['Done', 'Closed', "Won't do"]),
    ]
    transitions = [_transition('41', "Won't do"), _transition('42', 'Closed')]

    assert find_column_transition('PM Review', sections, transitions, 1).id == '42'


def test_label_sections_are_not_columns_and_unknown_statuses_cannot_move():
    assert (
        find_column_transition('Ready for Testing', SECTIONS, [_transition('31', 'Done')], 1).id
        == '31'
    )
    assert find_column_transition('Done', SECTIONS, CODE_REVIEW_TRANSITIONS, 1) is None
    assert find_column_transition('PM Review', SECTIONS, CODE_REVIEW_TRANSITIONS, 1) is None


def _pull_request(number: int, jira_status: str, state: str = '') -> PullRequestState:
    return PullRequestState(
        ref=PullRequestRef(
            url=f'https://github.com/acme/repo{number}/pull/{number}',
            owner='acme',
            repository=f'repo{number}',
            number=number,
            jira_status=jira_status,
        ),
        state=state,
    )


def test_pull_request_repository_and_urls_prefer_open_pull_requests():
    work_item = WorkItemPullRequests(
        'A-1', [_pull_request(1, 'MERGED', 'MERGED'), _pull_request(2, 'OPEN', 'OPEN')]
    )

    assert pull_request_repository(work_item) == 'repo2'
    assert pull_request_urls(work_item) == ['https://github.com/acme/repo2/pull/2']


def test_pull_request_repository_and_urls_fall_back_to_the_last_pull_request():
    work_item = WorkItemPullRequests(
        'A-1', [_pull_request(1, 'MERGED', 'MERGED'), _pull_request(3, 'DECLINED', 'CLOSED')]
    )

    assert pull_request_repository(work_item) == 'repo3'
    assert pull_request_urls(work_item) == ['https://github.com/acme/repo3/pull/3']
    assert pull_request_repository(None) is None
    assert pull_request_urls(WorkItemPullRequests('A-2')) == []


def test_claude_launch_arguments_carry_the_command_unaltered():
    command = 'claude "/ssi-dev-beta:wi-workflow ERP-1"; Write-Host done'

    arguments = claude_launch_arguments('wt.exe', 'pwsh.exe', 'C:/src/app', 'ERP-1', command)

    assert arguments[:11] == [
        'wt.exe',
        '--window',
        '0',
        'new-tab',
        '--title',
        'ERP-1',
        '--startingDirectory',
        'C:/src/app',
        'pwsh.exe',
        '-NoExit',
        '-EncodedCommand',
    ]
    assert base64.b64decode(arguments[11]).decode('utf-16-le') == command
    assert ';' not in arguments[11]
