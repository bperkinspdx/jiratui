from jiratui.pull_requests.models import (
    PullRequestRef,
    PullRequestState,
    ReviewSource,
    WorkItemPullRequests,
)
from jiratui.pull_requests.review import (
    build_pull_request_state,
    excerpt,
    link_stacked_pull_requests,
    parse_arnica_finding,
    parse_dev_status_pull_requests,
    parse_qodo_output,
    review_source,
    summarize_checks,
)

REF = PullRequestRef(
    url='https://github.com/acme/app/pull/7',
    owner='acme',
    repository='app',
    number=7,
    jira_status='OPEN',
)

ARNICA_BODY = """### Injection Risk: Potential   path traversal
**Severity**: Medium
**Status**: Open
Reply with the `[arnica] dismiss` command with code 94917885
"""


def _author(login: str, typename: str = 'User') -> dict:
    return {'login': login, '__typename': typename}


def test_parse_dev_status_pull_requests_reads_each_pull_request_once():
    answer = {
        'detail': [
            {
                'pullRequests': [
                    {
                        'url': 'https://github.com/acme/app/pull/7',
                        'status': 'OPEN',
                        'source': {'branch': 'feature/a'},
                        'destination': {'branch': 'main'},
                    },
                    {'url': 'https://github.com/acme/app/pull/7', 'status': 'OPEN'},
                    {'url': 'https://example.com/not-a-pull-request'},
                ]
            }
        ]
    }

    assert parse_dev_status_pull_requests(answer) == [
        PullRequestRef(
            url='https://github.com/acme/app/pull/7',
            owner='acme',
            repository='app',
            number=7,
            jira_status='OPEN',
            source_branch='feature/a',
            destination_branch='main',
        )
    ]


def test_review_source():
    assert review_source('qodo-for-acme', 'Bot') is ReviewSource.QODO
    assert review_source('arnica-github-connector', 'Bot') is ReviewSource.ARNICA
    assert review_source('arnica-github-connector[bot]', 'Bot') is ReviewSource.ARNICA
    assert review_source('alice', 'User') is ReviewSource.TEAMMATE
    assert review_source('dependabot', 'Bot') is ReviewSource.OTHER_BOT


def test_excerpt_takes_the_first_line_without_heading_marks():
    assert excerpt('\n\n## Title here\nmore') == 'Title here'
    assert excerpt('x' * 200, length=5) == 'xxxxx'
    assert excerpt(None) == ''


def test_parse_arnica_finding():
    finding = parse_arnica_finding(ARNICA_BODY, 'src/a.py', 12)

    assert finding is not None
    assert (finding.category, finding.title, finding.severity, finding.status) == (
        'Injection',
        'Potential path traversal',
        'Medium',
        'open',
    )
    assert (finding.path, finding.line, finding.is_open) == ('src/a.py', 12, True)
    assert parse_arnica_finding(ARNICA_BODY.replace('Open', 'Resolved')).is_open is False
    assert parse_arnica_finding('just a comment') is None


def test_summarize_checks():
    summary = summarize_checks(
        [
            {
                '__typename': 'CheckRun',
                'name': 'build',
                'status': 'COMPLETED',
                'conclusion': 'SUCCESS',
            },
            {
                '__typename': 'CheckRun',
                'name': 'lint',
                'status': 'COMPLETED',
                'conclusion': 'FAILURE',
            },
            {
                '__typename': 'CheckRun',
                'name': 'deploy',
                'status': 'IN_PROGRESS',
                'conclusion': None,
            },
            {'__typename': 'StatusContext', 'context': 'ci/legacy', 'state': 'PENDING'},
            {'__typename': 'StatusContext', 'context': 'ci/ok', 'state': 'SUCCESS'},
        ]
    )

    assert summary.passed == 2
    assert summary.failing_names == ['lint (FAILURE)']
    assert summary.pending_names == ['deploy', 'ci/legacy']
    assert summary.total == 5


def test_build_pull_request_state_classifies_the_review():
    node = {
        'title': 'Add a thing',
        'state': 'OPEN',
        'isDraft': False,
        'reviewDecision': 'APPROVED',
        'mergeable': 'MERGEABLE',
        'headRefName': 'feature/a',
        'baseRefName': 'main',
        'headRefOid': 'ABC123',
        'author': {'login': 'ben'},
        'commits': {'nodes': [{'commit': {'statusCheckRollup': {'contexts': {'nodes': []}}}}]},
        'reviewThreads': {
            'nodes': [
                {
                    'isResolved': False,
                    'isOutdated': False,
                    'path': 'a.py',
                    'line': 3,
                    'root': {
                        'nodes': [{'author': _author('qodo-for-acme', 'Bot'), 'body': 'Bug here'}]
                    },
                    'latest': {'nodes': [{'author': {'login': 'qodo-for-acme'}}]},
                },
                {
                    'isResolved': False,
                    'isOutdated': False,
                    'path': 'b.py',
                    'line': 9,
                    'root': {'nodes': [{'author': _author('alice'), 'body': 'Why this?'}]},
                    'latest': {'nodes': [{'author': {'login': 'ben'}}]},
                },
                {
                    'isResolved': False,
                    'isOutdated': False,
                    'path': 'c.py',
                    'line': 1,
                    'root': {'nodes': [{'author': _author('carol'), 'body': 'Rename?'}]},
                    'latest': {'nodes': [{'author': {'login': 'carol'}}]},
                },
                {
                    'isResolved': True,
                    'isOutdated': False,
                    'path': 'd.py',
                    'line': 2,
                    'root': {
                        'nodes': [
                            {
                                'author': _author('arnica-github-connector', 'Bot'),
                                'body': ARNICA_BODY,
                            }
                        ]
                    },
                    'latest': {'nodes': [{'author': {'login': 'arnica-github-connector'}}]},
                },
            ]
        },
        'comments': {
            'nodes': [
                {
                    'author': _author('dave'),
                    'body': 'Looks off',
                    'createdAt': '2026-11-01T10:00:00Z',
                },
                {'author': _author('ben'), 'body': 'Fixed it', 'createdAt': '2026-11-01T11:00:00Z'},
                {
                    'author': _author('erin'),
                    'body': 'One more',
                    'createdAt': '2026-11-01T12:00:00Z',
                },
                {
                    'author': _author('ben'),
                    'body': 'qodo why?',
                    'createdAt': '2026-11-01T13:00:00Z',
                },
            ]
        },
        'reviews': {
            'nodes': [
                {
                    'author': _author('frank'),
                    'state': 'CHANGES_REQUESTED',
                    'body': 'Needs tests',
                    'submittedAt': '2026-11-01T09:00:00Z',
                },
                {
                    'author': _author('gina'),
                    'state': 'APPROVED',
                    'body': 'LGTM',
                    'submittedAt': '2026-11-01T09:30:00Z',
                },
            ]
        },
    }

    state = build_pull_request_state(REF, node)

    assert (state.title, state.head_sha, state.review_decision) == (
        'Add a thing',
        'abc123',
        'APPROVED',
    )
    assert [item.excerpt for item in state.unresolved_qodo_threads] == ['Bug here']
    assert sorted(item.author for item in state.unanswered_teammate_items) == ['carol', 'erin']
    assert sorted(
        item.author for item in state.review_items if item.source is ReviewSource.TEAMMATE
    ) == ['alice', 'carol', 'dave', 'erin', 'frank']
    assert [(finding.path, finding.is_open) for finding in state.arnica_findings] == [
        ('d.py', True)
    ]
    assert len(state.open_arnica_findings) == 1


def test_build_pull_request_state_without_a_node():
    assert build_pull_request_state(REF, None).error


def test_parse_qodo_output_reads_the_session_after_the_trace_line():
    output = """{"result": {"review_session": {"status": "completed", "commit_sha": "ABC"},
      "findings": [
        {"id": "f1", "title": "Open one", "action_level": "action_required", "category": "Security",
         "attribution_status": "pending", "location": {"file_path": "a.py", "start_line": 4}},
        {"id": "f2", "title": "Closed one", "attribution_status": "implemented"},
        {"id": "f3", "title": "New status", "attribution_status": "something_new"}
      ]}}
    trace 63a1b89bb10950b80ae945b012a70752"""

    review = parse_qodo_output(output)

    assert (review.available, review.status, review.commit_sha) == (True, 'completed', 'abc')
    assert [(finding.id, finding.is_open) for finding in review.findings] == [
        ('f1', True),
        ('f2', False),
        ('f3', True),
    ]
    assert (review.findings[0].path, review.findings[0].line) == ('a.py', 4)


def test_parse_qodo_output_reports_errors_and_missing_sessions():
    assert parse_qodo_output('{"error": {"message": "denied"}}').reason == 'Qodo: denied'
    assert parse_qodo_output('not json').available is False
    assert parse_qodo_output('{"review_session": null, "findings": []}').status == 'none'


def test_link_stacked_pull_requests():
    def item(key: str, head: str, base: str) -> WorkItemPullRequests:
        ref = PullRequestRef(
            url=f'https://github.com/acme/app/pull/{key}', owner='acme', repository='app', number=1
        )
        return WorkItemPullRequests(
            work_item_key=key,
            pull_requests=[PullRequestState(ref=ref, head_branch=head, base_branch=base)],
        )

    bottom = item('A-1', 'feature/one', 'main')
    middle = item('A-2', 'feature/two', 'feature/one')
    top = item('A-3', 'feature/three', 'feature/two')

    link_stacked_pull_requests([top, middle, bottom])

    assert [entry.pull_requests[0].stacked_on for entry in (bottom, middle, top)] == [
        None,
        'A-1',
        'A-2',
    ]
