"""Reads the pull requests linked to work items from Jira, GitHub and the Qodo CLI."""

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any
from urllib.parse import urlencode

import httpx

from jiratui.exceptions import APIException
from jiratui.pull_requests.models import (
    PullRequestRef,
    PullRequestState,
    QodoReview,
    WorkItemPullRequests,
)
from jiratui.pull_requests.review import (
    build_pull_request_state,
    link_stacked_pull_requests,
    parse_dev_status_pull_requests,
    parse_qodo_output,
)

GITHUB_GRAPHQL_URL = 'https://api.github.com/graphql'
PULL_REQUESTS_PER_GRAPHQL_QUERY = 8
JIRA_REQUESTS_AT_ONCE = 8
SUBPROCESS_TIMEOUT_SECONDS = 90

PULL_REQUEST_FIELDS = """
fragment PullRequestFields on PullRequest {
  title state isDraft reviewDecision mergeable headRefName baseRefName headRefOid
  author { login }
  commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 40) { nodes {
    __typename
    ... on CheckRun { name status conclusion }
    ... on StatusContext { context state }
  } } } } } }
  reviewThreads(first: 60) { nodes {
    isResolved isOutdated path line
    root: comments(first: 1) { nodes { author { login __typename } body } }
    latest: comments(last: 1) { nodes { author { login } } }
  } }
  comments(last: 30) { nodes { author { login __typename } body createdAt } }
  reviews(last: 20) { nodes { author { login __typename } state body submittedAt } }
}
"""
"""Page sizes are kept small: a refresh every two minutes across a few dozen pull requests must stay well inside
GitHub's hourly GraphQL budget, which other tools on the same account share."""


class GitHubClient:
    """Reads pull requests through GitHub's GraphQL API as the account `gh` is signed in to.

    The token comes from `GH_TOKEN` or `GITHUB_TOKEN` when set, else from `gh auth token`.
    """

    def __init__(self) -> None:
        self._token: str | None = None

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        if token := os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'):
            self._token = token
            return token
        if not (gh := shutil.which('gh')):
            raise RuntimeError('GitHub CLI (gh) is not installed and GH_TOKEN is not set.')
        completed = await asyncio.to_thread(
            subprocess.run,
            [gh, 'auth', 'token'],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise RuntimeError(f'gh auth token failed: {completed.stderr.strip() or "no token"}')
        self._token = completed.stdout.strip()
        return self._token

    async def fetch(self, refs: Sequence[PullRequestRef]) -> dict[str, dict[str, Any] | str]:
        """Returns each pull request's GraphQL node by URL, or the reason it could not be read."""

        results: dict[str, dict[str, Any] | str] = {}
        if not refs:
            return results
        try:
            token = await self._get_token()
        except RuntimeError as error:
            return {ref.url: str(error) for ref in refs}
        batches = [
            refs[start : start + PULL_REQUESTS_PER_GRAPHQL_QUERY]
            for start in range(0, len(refs), PULL_REQUESTS_PER_GRAPHQL_QUERY)
        ]
        async with httpx.AsyncClient(timeout=30) as client:
            for batch_results in await asyncio.gather(
                *(self._fetch_batch(client, token, batch) for batch in batches)
            ):
                results.update(batch_results)
        return results

    async def _fetch_batch(
        self, client: httpx.AsyncClient, token: str, refs: Sequence[PullRequestRef]
    ) -> dict[str, dict[str, Any] | str]:
        selections = '\n'.join(
            f'pr{index}: repository(owner: {json.dumps(ref.owner)}, name: {json.dumps(ref.repository)}) '
            f'{{ pullRequest(number: {ref.number}) {{ ...PullRequestFields }} }}'
            for index, ref in enumerate(refs)
        )
        query = f'query {{\n{selections}\n}}\n{PULL_REQUEST_FIELDS}'
        try:
            response = await client.post(
                GITHUB_GRAPHQL_URL,
                json={'query': query},
                headers={'Authorization': f'bearer {token}', 'User-Agent': 'jiratui'},
            )
            response.raise_for_status()
            answer = response.json()
        except (httpx.HTTPError, ValueError) as error:
            return {ref.url: f'GitHub could not be read: {error}' for ref in refs}
        data = answer.get('data') or {}
        failure = '; '.join(str(error.get('message')) for error in answer.get('errors') or [])
        results: dict[str, dict[str, Any] | str] = {}
        for index, ref in enumerate(refs):
            node = ((data.get(f'pr{index}') or {}).get('pullRequest')) or None
            results[ref.url] = node if node else (failure or 'GitHub returned no pull request.')
        return results


class QodoClient:
    """Reads a pull request's Qodo review session through the Qodo CLI."""

    @staticmethod
    def find_cli() -> str | None:
        candidates = []
        if qodo_home := os.environ.get('QODO_HOME'):
            candidates += [Path(qodo_home) / 'bin' / 'qodo.exe', Path(qodo_home) / 'bin' / 'qodo']
        candidates += [
            Path.home() / '.qodo' / 'bin' / 'qodo.exe',
            Path.home() / '.qodo' / 'bin' / 'qodo',
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return shutil.which('qodo')

    async def review(self, pull_request_url: str) -> QodoReview:
        if not (cli := self.find_cli()):
            return QodoReview(available=False, reason='The Qodo CLI is not installed.')
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [
                    cli,
                    'read',
                    'pr-review-session',
                    'findings',
                    '--pr-url',
                    pull_request_url,
                    '--json',
                ],
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return QodoReview(available=False, reason=f'The Qodo CLI failed: {error}')
        return parse_qodo_output(completed.stdout or completed.stderr or '')


class PullRequestService:
    """Gathers the pull requests of work items: the links from Jira, their state from GitHub, and Qodo's review.

    Merged and declined pull requests are read from GitHub once; their state no longer changes.
    """

    def __init__(
        self,
        jira_client: Any,
        jira_base_url: str,
        application_type: str,
        github: GitHubClient | None = None,
        qodo: QodoClient | None = None,
    ) -> None:
        """Initializes the service.

        Args:
            jira_client: the application's async Jira client, whose `make_request` accepts an absolute URL on the
            Jira site.
            jira_base_url: the base URL of the Jira site.
            application_type: the `applicationType` Jira's dev-status API files the pull requests under.
            github: the GitHub client; a new one by default.
            qodo: the Qodo client; a new one by default.
        """

        self._jira = jira_client
        self._dev_status_url = f'{jira_base_url.rstrip("/")}/rest/dev-status/latest/issue/detail'
        self._application_type = application_type
        self._github = github or GitHubClient()
        self._qodo = qodo or QodoClient()
        self._finished: dict[str, PullRequestState] = {}

    async def pull_request_refs(self, work_item_id: str) -> list[PullRequestRef]:
        """Returns the pull requests Jira's development panel links to the work item with `work_item_id`."""

        query = urlencode(
            {
                'issueId': work_item_id,
                'applicationType': self._application_type,
                'dataType': 'pullrequest',
            }
        )
        answer = await self._jira.make_request(
            httpx.AsyncClient.get, url=f'{self._dev_status_url}?{query}'
        )
        return parse_dev_status_pull_requests(answer or {})

    async def summaries(
        self, work_items: Iterable[tuple[str, str]]
    ) -> dict[str, WorkItemPullRequests]:
        """Reads the pull requests of every work item, given as `(id, key)` pairs, keyed by work item key."""

        work_items = list(work_items)
        semaphore = asyncio.Semaphore(JIRA_REQUESTS_AT_ONCE)

        async def read_refs(work_item_id: str) -> list[PullRequestRef] | str:
            async with semaphore:
                try:
                    return await self.pull_request_refs(work_item_id)
                except APIException as error:
                    return f'Jira could not be read: {error}'

        refs_by_item = await asyncio.gather(
            *(read_refs(work_item_id) for work_item_id, _ in work_items)
        )
        to_fetch = list(
            {
                ref.url: ref
                for refs in refs_by_item
                if isinstance(refs, list)
                for ref in refs
                if ref.url not in self._finished
            }.values()
        )
        nodes = await self._github.fetch(to_fetch)
        results: dict[str, WorkItemPullRequests] = {}
        for (_, key), refs in zip(work_items, refs_by_item, strict=True):
            if isinstance(refs, str):
                results[key] = WorkItemPullRequests(work_item_key=key, error=refs)
                continue
            results[key] = WorkItemPullRequests(
                work_item_key=key,
                pull_requests=[self._state(ref, nodes.get(ref.url)) for ref in refs],
            )
        link_stacked_pull_requests(results.values())
        return results

    async def with_qodo_reviews(self, work_item: WorkItemPullRequests) -> WorkItemPullRequests:
        """Returns `work_item` with Qodo's review read for each of its open pull requests."""

        pull_requests = []
        for pull_request in work_item.pull_requests:
            if pull_request.error is None and pull_request.state == 'OPEN':
                pull_request = replace(
                    pull_request, qodo=await self._qodo.review(pull_request.ref.url)
                )
            pull_requests.append(pull_request)
        return replace(work_item, pull_requests=pull_requests)

    def _state(self, ref: PullRequestRef, node: dict[str, Any] | str | None) -> PullRequestState:
        if (finished := self._finished.get(ref.url)) is not None:
            return replace(finished, ref=ref)
        if isinstance(node, str):
            return PullRequestState(ref=ref, error=node)
        state = build_pull_request_state(ref, node)
        if state.error is None and state.state in ('MERGED', 'CLOSED'):
            self._finished[ref.url] = state
        return state
