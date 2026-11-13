from dataclasses import dataclass
from datetime import date
import logging
from pathlib import Path
import shutil
import subprocess

from dateutil.parser import isoparse  # type:ignore[import-untyped]
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalGroup, ItemGrid, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, LoadingIndicator, Select, TabPane
from textual.worker import Worker

from jiratui.actions.constants import SupportedActions
from jiratui.actions.keys import get_application_key_bindings
from jiratui.api_controller.controller import APIController, APIControllerResponse
from jiratui.constants import FULL_TEXT_SEARCH_DEFAULT_MINIMUM_TERM_LENGTH, LOGGER_NAME
from jiratui.models import (
    IssueType,
    JiraBaseIssue,
    JiraIssue,
    JiraIssueSearchResponse,
    JiraUser,
    WorkItemArchivalSyncResponse,
    WorkItemsSearchOrderBy,
)
from jiratui.pull_requests.models import WorkItemPullRequests
from jiratui.pull_requests.service import PullRequestService
from jiratui.utils.history import HistoryEntry
from jiratui.utils.logging import JiraTUILogger
from jiratui.utils.search import build_jql_query, get_work_item_search_fields
from jiratui.utils.ui_actions import Actionable, UIAction
from jiratui.utils.urls import build_external_url_for_issue
from jiratui.utils.views import fields_for_status_sections
from jiratui.widgets.attachments.attachments import IssueAttachmentsWidget, WorkItemAttachments
from jiratui.widgets.comments.comments import IssueCommentsWidget, WorkItemComments
from jiratui.widgets.commons.users import JiraUserInput, UsersAutoComplete
from jiratui.widgets.commons.widgets import ActionableTabbedContent
from jiratui.widgets.create_work_item.screen import AddWorkItemScreen
from jiratui.widgets.filters import (
    ActiveSprintCheckbox,
    IssueSearchCreatedFromWidget,
    IssueSearchCreatedUntilWidget,
    IssueStatusSelectionInput,
    IssueTypeSelectionInput,
    JQLSearchWidget,
    OrderByWidget,
    ProjectSelectionInput,
    WorkItemInputWidget,
)
from jiratui.widgets.messages import SearchWorkItem
from jiratui.widgets.pull_requests import PullRequestsWidget, pull_request_badge
from jiratui.widgets.related_work_items.related_issues import (
    RelatedIssuesWidget,
    WorkItemRelatedItems,
)
from jiratui.widgets.remote_links.links import IssueRemoteLinksWidget
from jiratui.widgets.screens.clone import CloneWorkItemRequest, CloneWorkItemScreen
from jiratui.widgets.screens.context_menu import (
    ContextMenu,
    ContextMenuOptionAction,
    ContextMenuSelection,
)
from jiratui.widgets.screens.history import HistoryScreen
from jiratui.widgets.screens.repository_picker import RepositoryPickerScreen
from jiratui.widgets.screens.text_search import TextSearchScreen
from jiratui.widgets.screens.work_item_quick_view import WorkItemQuickViewScreen
from jiratui.widgets.search import (
    DataTableSearchInput,
    IssuesSearchResultsTable,
    SearchResultsContainer,
)
from jiratui.widgets.views import ViewsBar
from jiratui.widgets.work_item_details.details import IssueDetailsWidget
from jiratui.widgets.work_item_info.info import WorkItemInfoContainer
from jiratui.widgets.work_item_subtasks.subtasks import (
    SubtasksWidget,
    WorkItemSubtasks,
)
from jiratui.work_item_actions import (
    claude_launch_arguments,
    find_column_transition,
    find_transition_to_status,
    pull_request_repository,
    pull_request_urls,
)

VIEWS_WORKER_GROUP = 'views'
"""Loading a view cancels only an earlier view load, never the workers that fill the search filters."""
PULL_REQUESTS_WORKER_GROUP = 'pull-requests'
PULL_REQUEST_DETAILS_WORKER_GROUP = 'pull-request-details'
WORK_ITEM_ACTIONS_WORKER_GROUP = 'work-item-actions'


@dataclass
class WorkItemSearchResult:
    total: int = 0
    start: int = 0
    end: int = 0
    response: JiraIssueSearchResponse | None = None


class MainScreen(Actionable, Screen):
    """The main screen of the application.

    This screen is responsible for:

    - handling different types of work item search
    - handling events for focusing different widgets based on custom key-bindings
    - handling the logic for creating work items
    - handling the logic for copying the key of the currently selected work item
    - handling the logic for copying the url of the currently selected work item
    - loading the work-item views defined in the configuration
    - searching Jira users for the assignee input filter
    - searching projects for the project input
    - searching types of issues for the issue type input filter
    - composing the widgets in the UI

    This screen is actionable, which means that it can handle different actions as selected by the user in the
    configuration file.

    **See Also**:
    - [Use Case: Filter-based search](#use-case-filter-based-search)
    - [Use Case: Text-based search](#use-case-text-based-search)
    - [Use Case: Work item search](#use-case-work-item-search)
    - [Use Case: Create work items](#use-case-create-work-item)
    """

    ACTIONS: list[UIAction] = []
    # set up the key-bindings based on the configuration selected by the user
    key_bindings: dict[str, dict] = get_application_key_bindings()
    for supported_action_id in [
        SupportedActions.FOCUS_PROJECT_FILTER,
        SupportedActions.FOCUS_SEARCH_WORK_ITEM_TYPE_FILTER,
        SupportedActions.FOCUS_SEARCH_WORK_ITEM_STATUS_FILTER,
        SupportedActions.FOCUS_SEARCH_ASSIGNEE_FILTER,
        SupportedActions.FOCUS_SEARCH_WORK_ITEM_KEY_FILTER,
        SupportedActions.FOCUS_SEARCH_CREATED_FROM_FILTER,
        SupportedActions.FOCUS_SEARCH_CREATED_UNTIL_FILTER,
        SupportedActions.FOCUS_SEARCH_SORT_FILTER,
        SupportedActions.FOCUS_SEARCH_SPRINT_FILTER,
        SupportedActions.FOCUS_SEARCH_JQL,
        SupportedActions.FOCUS_SEARCH_RESULTS,
        SupportedActions.FOCUS_WORK_ITEM_INFORMATION_TAB,
        SupportedActions.FOCUS_WORK_ITEM_DETAILS_TAB,
        SupportedActions.FOCUS_WORK_ITEM_COMMENTS_TAB,
        SupportedActions.FOCUS_WORK_ITEM_RELATED_TAB,
        SupportedActions.FOCUS_WORK_ITEM_ATTACHMENTS_TAB,
        SupportedActions.FOCUS_WORK_ITEM_LINKS_TAB,
        SupportedActions.FOCUS_WORK_ITEM_SUBTASKS_TAB,
        SupportedActions.COPY_ISSUE_KEY,
        SupportedActions.COPY_ISSUE_URL,
        SupportedActions.SEARCH,
        SupportedActions.FIND_BY_TEXT,
        SupportedActions.CREATE_WORK_ITEM,
        SupportedActions.SHOW_RECENT_HISTORY,
        SupportedActions.FOCUS_WORK_ITEM_PULL_REQUESTS_TAB,
        SupportedActions.RELOAD_VIEW,
        SupportedActions.PICK_UP,
        SupportedActions.MOVE_TO_NEXT_COLUMN,
        SupportedActions.MOVE_TO_PREVIOUS_COLUMN,
        SupportedActions.OPEN_PULL_REQUEST,
        SupportedActions.HAND_TO_CLAUDE,
    ]:
        data = key_bindings.get(supported_action_id.value, {})
        ACTIONS.append(
            UIAction(
                action=supported_action_id.value,
                keys=data.get('keys', []),
                show=data.get('show', False),
                description=data.get('description'),
                tooltip=data.get('tooltip', ''),
            )
        )

    BINDINGS = [
        *[
            Binding(
                key=','.join(action.keys),
                action=action.action,
                show=action.show,
                description=action.description or '',
                tooltip=action.tooltip,
            )
            for action in ACTIONS
            if isinstance(action.action, str)
        ],
        *[
            Binding(key=key, action=f'load_view({position})', show=False)
            for position, key in enumerate(
                key_bindings.get(SupportedActions.LOAD_VIEW.value, {}).get('keys', []), start=1
            )
        ],
    ]

    def __init__(
        self,
        api: APIController | None = None,
        project_key: str | None = None,
        user_account_id: str | None = None,
        jql_expression_id: int | None = None,
        work_item_key: str | None = None,
        focus_item_on_startup: int | None = None,
    ):
        super().__init__()
        self.api = APIController() if not api else api
        """The API instance used by the screen to interact with the Jira REST API via a an API controller."""
        self.available_issues_status: list[tuple[str, str]] = []
        self.initial_project_key: str | None = project_key
        """A project key to set as the initial value of the projects dropdown widget."""
        self.initial_work_item_key = work_item_key
        """A work item key to set as the initial value of the work-item-key widget."""
        self.initial_assignee_account_id = user_account_id
        """Pre-selected user/assignee account id. This is passed during the initialization of the application."""
        self.initial_jql_expression_id: int | None = jql_expression_id
        """Pre-selected JQL expression ID to load into the JQL expression widget on start-up. This JQL expression will
        be used for searching issues when the user does not select any filter/criteria in the UI. """
        self.focus_item_on_startup = focus_item_on_startup
        """The position of the work item to focus and open on startup. Requires search_on_startup to be enabled."""
        self.current_loaded_work_item_key: str | None = None
        """Track the currently loaded work item key to prevent redundant reloads."""
        self.active_view: int | None = None
        """The 1-based position in `config.views` of the view the search results show; `None` after any other
        search."""
        self.pull_request_service: PullRequestService | None = None
        if settings := self.app.config.pull_requests:  # type:ignore[attr-defined]
            self.pull_request_service = PullRequestService(
                self.api.api.client,
                self.app.config.jira_api_base_url,  # type:ignore[attr-defined]
                settings.application_type,
            )
        self._pull_request_summaries: dict[str, WorkItemPullRequests] = {}
        """The pull requests last read for each work item on display, by key, with their stacking resolved."""
        self.current_key_bindings: dict[str, dict] = get_application_key_bindings()
        self.work_item_tabs_titles: dict = {}
        if self.app.config.show_keybinding_hints:  # type:ignore[attr-defined]
            self.work_item_tabs_titles = {
                'search_results_container': f'Work Items ({self.current_key_bindings.get(SupportedActions.FOCUS_SEARCH_RESULTS.value, {}).get("keys", [])[0]})',
                'work_item_info_container': f'Info ({self.current_key_bindings.get(SupportedActions.FOCUS_WORK_ITEM_INFORMATION_TAB.value, {}).get("keys", [])[0]})',
                'issue_details': f'Details ({self.current_key_bindings.get(SupportedActions.FOCUS_WORK_ITEM_DETAILS_TAB.value, {}).get("keys", [])[0]})',
                'issue_comments': f'Comments ({self.current_key_bindings.get(SupportedActions.FOCUS_WORK_ITEM_COMMENTS_TAB.value, {}).get("keys", [])[0]})',
                'related_issues': f'Related ({self.current_key_bindings.get(SupportedActions.FOCUS_WORK_ITEM_RELATED_TAB.value, {}).get("keys", [])[0]})',
                'attachments': f'Attachments ({self.current_key_bindings.get(SupportedActions.FOCUS_WORK_ITEM_ATTACHMENTS_TAB.value, {}).get("keys", [])[0]})',
                'issue_remote_links': f'Links ({self.current_key_bindings.get(SupportedActions.FOCUS_WORK_ITEM_LINKS_TAB.value, {}).get("keys", [])[0]})',
                'issue_subtasks': f'Subtasks ({self.current_key_bindings.get(SupportedActions.FOCUS_WORK_ITEM_SUBTASKS_TAB.value, {}).get("keys", [])[0]})',
                'pull_requests': f'PRs ({self.current_key_bindings.get(SupportedActions.FOCUS_WORK_ITEM_PULL_REQUESTS_TAB.value, {}).get("keys", [])[0]})',
            }
        self.logger = JiraTUILogger(logging.getLogger(LOGGER_NAME), self.app.config.enable_logging)  # type:ignore[attr-defined]

    @property
    def project_selector(self) -> ProjectSelectionInput:
        return self.query_one(ProjectSelectionInput)

    @property
    def issue_status_selector(self) -> IssueStatusSelectionInput:
        return self.query_one(IssueStatusSelectionInput)

    @property
    def issue_type_selector(self) -> IssueTypeSelectionInput:
        return self.query_one(IssueTypeSelectionInput)

    @property
    def users_selector(self) -> JiraUserInput:
        return self.query_one(JiraUserInput)

    @property
    def run_button(self) -> Button:
        return self.query_one('#run-button', expect_type=Button)

    @property
    def tabs(self) -> ActionableTabbedContent:
        return self.query_one('#tabs', ActionableTabbedContent)

    @property
    def search_results_table(self) -> IssuesSearchResultsTable:
        return self.query_one(IssuesSearchResultsTable)

    @property
    def search_results_filter_input(self) -> DataTableSearchInput:
        return self.query_one(DataTableSearchInput)

    @property
    def search_results_container(self) -> SearchResultsContainer:
        return self.query_one(SearchResultsContainer)

    @property
    def issue_details_widget(self) -> IssueDetailsWidget:
        return self.query_one(IssueDetailsWidget)

    @property
    def issue_comments_widget(self) -> IssueCommentsWidget:
        return self.query_one(IssueCommentsWidget)

    @property
    def related_issues_widget(self) -> RelatedIssuesWidget:
        return self.query_one(RelatedIssuesWidget)

    @property
    def issue_info_container(self) -> WorkItemInfoContainer:
        return self.query_one(WorkItemInfoContainer)

    @property
    def issue_remote_links_widget(self) -> IssueRemoteLinksWidget:
        return self.query_one(IssueRemoteLinksWidget)

    @property
    def issue_child_work_items_widget(self) -> SubtasksWidget:
        return self.query_one(SubtasksWidget)

    @property
    def issue_attachments_widget(self) -> IssueAttachmentsWidget:
        return self.query_one(IssueAttachmentsWidget)

    @property
    def issue_key_input(self) -> WorkItemInputWidget:
        return self.query_one('#input_issue_key', expect_type=WorkItemInputWidget)

    @property
    def jql_expression_input(self) -> JQLSearchWidget:
        return self.query_one('#input_search_term', expect_type=JQLSearchWidget)

    @property
    def order_by_widget(self) -> OrderByWidget:
        return self.query_one(OrderByWidget)

    @property
    def active_sprint_checkbox(self) -> ActiveSprintCheckbox:
        return self.query_one(ActiveSprintCheckbox)

    @property
    def issue_date_from_input(self) -> IssueSearchCreatedFromWidget:
        return self.query_one('#input_date_from', expect_type=IssueSearchCreatedFromWidget)

    @property
    def issue_date_until_input(self) -> IssueSearchCreatedUntilWidget:
        return self.query_one('#input_date_until', expect_type=IssueSearchCreatedUntilWidget)

    @property
    def loading_container(self) -> LoadingIndicator:
        return self.query_one('#main-screen-loading-container', expect_type=LoadingIndicator)

    @property
    def views_bar(self) -> ViewsBar | None:
        return self.query_one_optional(ViewsBar)

    @property
    def pull_requests_widget(self) -> PullRequestsWidget | None:
        return self.query_one_optional(PullRequestsWidget)

    def compose(self) -> ComposeResult:
        """Composes the widgets of the application's main screen.

        Returns:
            An instance of `ComposeResult`.
        """

        # Only render header if tui_custom_title is not explicitly set to empty string
        should_show_header = True
        if self.app.config.tui_custom_title is not None and self.app.config.tui_custom_title == '':  # type:ignore[attr-defined]
            should_show_header = False

        # a loading indicator that will be displayed when the screen is handling a create-work-item event
        loading_container = LoadingIndicator(id='main-screen-loading-container')
        loading_container.display = False

        if should_show_header:
            yield Header(id='app-header', icon='*')
        with Vertical(id='main-container'):
            with HorizontalGroup():
                yield ProjectSelectionInput(
                    projects=[],
                    border_subtitle=(
                        f'{self.current_key_bindings.get(SupportedActions.FOCUS_PROJECT_FILTER.value, {}).get("keys", [])[0]}'
                        if self.app.config.show_keybinding_hints  # type:ignore[attr-defined]
                        else None
                    ),
                )
                yield IssueTypeSelectionInput(
                    types=[],
                    border_subtitle=(
                        f'{self.current_key_bindings.get(SupportedActions.FOCUS_SEARCH_WORK_ITEM_TYPE_FILTER.value, {}).get("keys", [])[0]}'
                        if self.app.config.show_keybinding_hints  # type:ignore[attr-defined]
                        else None
                    ),
                )
                yield IssueStatusSelectionInput(
                    statuses=[],
                    border_subtitle=(
                        f'{self.current_key_bindings.get(SupportedActions.FOCUS_SEARCH_WORK_ITEM_STATUS_FILTER.value, {}).get("keys", [])[0]}'
                        if self.app.config.show_keybinding_hints  # type:ignore[attr-defined]
                        else None
                    ),
                )
                yield JiraUserInput(
                    id='search-filters-input-assignee',
                    border_subtitle=(
                        f'{self.current_key_bindings.get(SupportedActions.FOCUS_SEARCH_ASSIGNEE_FILTER.value, {}).get("keys", [])[0]}'
                        if self.app.config.show_keybinding_hints  # type:ignore[attr-defined]
                        else None
                    ),
                    border_title='Assignee',
                )
            with ItemGrid(classes='bottom-search-bar'):
                yield WorkItemInputWidget(
                    value=self.initial_work_item_key,
                    border_subtitle=(
                        f'{self.current_key_bindings.get(SupportedActions.FOCUS_SEARCH_WORK_ITEM_KEY_FILTER.value, {}).get("keys", [])[0]}'
                        if self.app.config.show_keybinding_hints  # type:ignore[attr-defined]
                        else None
                    ),
                )
                yield IssueSearchCreatedFromWidget(
                    border_subtitle=(
                        f'{self.current_key_bindings.get(SupportedActions.FOCUS_SEARCH_CREATED_FROM_FILTER.value, {}).get("keys", [])[0]}'
                        if self.app.config.show_keybinding_hints  # type:ignore[attr-defined]
                        else None
                    ),
                )
                yield IssueSearchCreatedUntilWidget(
                    border_subtitle=(
                        f'{self.current_key_bindings.get(SupportedActions.FOCUS_SEARCH_CREATED_UNTIL_FILTER.value, {}).get("keys", [])[0]}'
                        if self.app.config.show_keybinding_hints  # type:ignore[attr-defined]
                        else None
                    ),
                )
                yield OrderByWidget(
                    WorkItemsSearchOrderBy.to_choices(),
                    initial_value=self.app.config.search_results_default_order.value,  # type:ignore[attr-defined]
                    border_subtitle=(
                        f'{self.current_key_bindings.get(SupportedActions.FOCUS_SEARCH_SORT_FILTER.value, {}).get("keys", [])[0]}'
                        if self.app.config.show_keybinding_hints  # type:ignore[attr-defined]
                        else None
                    ),
                )
                yield ActiveSprintCheckbox(
                    value=self.app.config.active_sprint_on_startup,  # type:ignore[attr-defined]
                    border_subtitle=(
                        f'{self.current_key_bindings.get(SupportedActions.FOCUS_SEARCH_SPRINT_FILTER.value, {}).get("keys", [])[0]}'
                        if self.app.config.show_keybinding_hints  # type:ignore[attr-defined]
                        else None
                    ),
                )
                yield JQLSearchWidget(
                    border_subtitle=(
                        f'{self.current_key_bindings.get(SupportedActions.FOCUS_SEARCH_JQL.value, {}).get("keys", [])[0]}'
                        if self.app.config.show_keybinding_hints  # type:ignore[attr-defined]
                        else None
                    ),
                )
                yield Button(
                    'Search',
                    id='run-button',
                    variant='warning',
                    disabled=False,
                    flat=True,
                    compact=True,
                )
            yield loading_container
            if views := self.app.config.views:  # type:ignore[attr-defined]
                yield ViewsBar(
                    labels=[view.label for view in views],
                    keys=self.current_key_bindings.get(SupportedActions.LOAD_VIEW.value, {}).get(
                        'keys', []
                    ),
                    show_keys=self.app.config.show_keybinding_hints,  # type:ignore[attr-defined]
                )
            with Horizontal():
                with SearchResultsContainer(
                    id='search_results_container',
                    border_title=self.work_item_tabs_titles.get('search_results_container'),
                ):
                    yield DataTableSearchInput()
                    yield IssuesSearchResultsTable()
                with ActionableTabbedContent(id='tabs'):
                    with TabPane(
                        title=self.work_item_tabs_titles.get('work_item_info_container', 'Info'),
                        classes='summary-description-container',
                        id='tab-pane-info',
                    ):
                        yield WorkItemInfoContainer()
                    with TabPane(title=self.work_item_tabs_titles.get('issue_details', 'Details')):
                        yield IssueDetailsWidget()  # will contain a form to view and update some of the fields of the issue
                    with TabPane(
                        title=self.work_item_tabs_titles.get('issue_comments', 'Comments')
                    ):
                        yield IssueCommentsWidget()
                    with TabPane(title=self.work_item_tabs_titles.get('related_issues', 'Related')):
                        yield RelatedIssuesWidget()
                    with TabPane(
                        title=self.work_item_tabs_titles.get('attachments', 'Attachments')
                    ):
                        yield IssueAttachmentsWidget()
                    with TabPane(
                        title=self.work_item_tabs_titles.get('issue_remote_links', 'Links')
                    ):
                        yield IssueRemoteLinksWidget()
                    with TabPane(
                        title=self.work_item_tabs_titles.get('issue_subtasks', 'Subtasks')
                    ):
                        yield SubtasksWidget()
                    if self.pull_request_service is not None:
                        with TabPane(title=self.work_item_tabs_titles.get('pull_requests', 'PRs')):
                            yield PullRequestsWidget()
        yield Footer(show_command_palette=False, compact=True)

    async def on_mount(self) -> None:
        """Mounts the widgets on the screen.

        This method orchestrates the initial data loading and UI population based on configuration
        settings and launch arguments. It performs the following operations in sequence:

        **Workers (Asynchronous Tasks):**
        - Fetch projects: always runs as a worker to populate the project dropdown.
        - Fetch issue types: runs as a worker unless configured to only fetch projects on startup
          or an initial project key is provided.
        - Fetch statuses: runs as a worker unless configured to only fetch projects on startup
          or an initial project key is provided.
        - Load view: runs as a worker if `startup_view` names a configured view; it takes the place of
          the startup search and needs none of the filters, so it does not wait for the other workers.
        - Fetch work items (search): runs as a worker if `search_on_startup` is enabled and no startup
          view is loaded, after waiting for dependent workers to complete.
        - Focus item: Runs as a worker if `focus_item_on_startup` is specified, after waiting
          for the search to complete.

        **API Calls (Direct):**
        - `get_user(account_id)`: Fetches user details if `initial_assignee_account_id` is provided. Used to validate
        the user exists and retrieve their display name.

        **Widgets Populated:**
        - users_selector: set with the user account ID and display name if `initial_assignee_account_id`
          is provided and the user is found via API.
        - jql_expression_input: set with the JQL expression if `initial_jql_expression_id` is provided
          and a matching pre-defined expression exists in configuration.
        - Project dropdown: populated by the `fetch_projects` worker.
        - Issue types dropdown: Populated by the `fetch_issue_types` worker (if applicable).
        - Status filter: Populated by the `fetch_statuses` worker (if applicable).
        - Work items list: Populated by the search worker (if `search_on_startup` is enabled).

        **Configuration Dependencies:**
        - `on_start_up_only_fetch_projects`: If True, skips fetching issue types and statuses.
        - `startup_view`: The 1-based position in `views` of the view to load on startup.
        - `search_on_startup`: If True, triggers a work item search after initial data is loaded.
        - `pre_defined_jql_expressions`: Dictionary of JQL expressions keyed by expression ID.

        **Execution Order:**
        1. Fetch projects (always).
        2. Fetch issue types and statuses (conditionally, in parallel).
        3. Fetch and set user details (conditionally).
        4. Load and set JQL expression (conditionally).
        5. Load the startup view, or else trigger the search, and focus item (conditionally, with proper await
           sequencing).

        See the following workflow diagram for an overview.

        ```{mermaid}
        flowchart TD
            Start([Start]) --> FetchProjects["Run Worker: Fetch Projects<br/>(Add to workers list)"]
            FetchProjects --> CheckOnStartup{"on_start_up_only_fetch_projects<br/>is False AND<br/>initial_project_key is None?"}
            CheckOnStartup -->|Yes| FetchIssueTypes["Run Worker: Fetch Issue Types"]
            CheckOnStartup -->|Yes| FetchStatuses["Run Worker: Fetch Statuses"]
            CheckOnStartup -->|No| CheckAssignee
            FetchIssueTypes --> CheckAssignee
            FetchStatuses --> CheckAssignee
            CheckAssignee{"initial_assignee_account_id<br/>is set?"}
            CheckAssignee -->|Yes| GetUser["API Call: get_user<br/>(initial_assignee_account_id)"]
            CheckAssignee -->|No| CheckJQL
            GetUser --> UserSuccess{"user_response<br/>success?"}
            UserSuccess -->|Yes| SetUserWidget["Set users_selector<br/>with user details"]
            UserSuccess -->|No| NotifyWarning["Notify: Unable to find user<br/>with account ID"]
            SetUserWidget --> CheckJQL
            NotifyWarning --> CheckJQL
            CheckJQL{"initial_jql_expression_id<br/>is set AND<br/>pre_defined_jql_expressions exist?"}
            CheckJQL -->|Yes| GetExpression{"Get expression from<br/>pre_defined_jql_expressions?"}
            CheckJQL -->|No| CheckStartupView
            GetExpression -->|Found| SetExpression["Set jql_expression_input<br/>with expression<br/>Clean: replace newlines/tabs"]
            GetExpression -->|Not Found| CheckStartupView
            SetExpression --> CheckStartupView
            CheckStartupView{"startup_view names<br/>a configured view?"}
            CheckStartupView -->|Yes| LoadView["Run Worker: Load View<br/>(exclusive in the views group)"]
            CheckStartupView -->|No| CheckSearchOnStartup
            LoadView --> CheckFocusItem
            CheckSearchOnStartup{"search_on_startup<br/>is enabled?"}
            CheckSearchOnStartup -->|No| End([End])
            CheckSearchOnStartup -->|Yes| WaitWorkers["Wait for workers to complete<br/>(projects, issue types, statuses)"]
            WaitWorkers --> RunSearch["Run Worker: Search<br/>(exclusive=True)"]
            RunSearch --> CheckFocusItem{"focus_item_on_startup<br/>is set?"}
            CheckFocusItem -->|No| End
            CheckFocusItem -->|Yes| WaitSearch["Wait for search worker<br/>to complete"]
            WaitSearch --> FocusItem["Run Worker: Focus Item<br/>on Startup"]
            CheckFocusItem -->|Yes, after a view| WaitView["Wait for view worker<br/>to complete"]
            WaitView --> FocusItem
            FocusItem --> End
        ```

        Returns:
            None
        """

        # fetch the list of projects
        workers: list[Worker] = [self.run_worker(self.fetch_projects())]
        if self.pull_request_service is not None:
            self.set_interval(
                self.app.config.pull_requests.refresh_seconds,  # type:ignore[attr-defined]
                self._refresh_pull_requests,
            )
        # if there is an initial value for the project key the worker that fetches the projects will trigger fetching
        # status codes and work item types after the project dropdown is updated with the selection.
        # the same happens when the user configures the app to fetch only projects on start up
        if not self.app.config.on_start_up_only_fetch_projects and not self.initial_project_key:  # type:ignore[attr-defined]
            # in this case we need to fetch users, status codes and work item types
            self.run_worker(self.fetch_issue_types())
            self.run_worker(self.fetch_statuses())

        # if the user launched the app with a pre-defined user account id then let's fetch the details of the user
        # and set the user selection widget with the corresponding user; if any exists
        if self.initial_assignee_account_id:
            user_response: APIControllerResponse = await self.api.get_user(
                self.initial_assignee_account_id
            )
            user_details: JiraUser | None
            if user_response.success and (user_details := user_response.result):
                self.users_selector.set_value(user_details.account_id, user_details.display_name)
            else:
                self.notify(
                    f'Unable to find the user with account ID: {self.initial_assignee_account_id}. Check the configuration and/or the launch arguments',
                    severity='warning',
                    title='Not Found',
                )

        if self.initial_jql_expression_id and (
            pre_defined_jql_expressions := self.app.config.pre_defined_jql_expressions  # type:ignore[attr-defined]
        ):
            if (
                expression_data := pre_defined_jql_expressions.get(self.initial_jql_expression_id)
            ) and (expression := expression_data.get('expression')):
                self.jql_expression_input.expression = expression.replace('\n', ' ').replace(
                    '\t', ' '
                )

        if startup_view := self._startup_view_position():
            view_worker = self.run_worker(
                self.load_view(startup_view), exclusive=True, group=VIEWS_WORKER_GROUP
            )
            if self.focus_item_on_startup:
                await self.app.workers.wait_for_complete([view_worker])
                self.run_worker(self._focus_item_after_startup(self.focus_item_on_startup))
        # Trigger search on startup if enabled
        elif self.app.config.search_on_startup:  # type:ignore[attr-defined]
            # make sure to wait for the related workers so the method that searches work items have the necessary
            # filters set up, e.g. the selected project (if any) and the selected users (if any)
            await self.app.workers.wait_for_complete(workers)
            search_worker = self.run_worker(self.action_search(), exclusive=True)

            # If focus_item_on_startup is specified, wait for search to complete and then focus the item
            if self.focus_item_on_startup:
                await self.app.workers.wait_for_complete([search_worker])
                self.run_worker(self._focus_item_after_startup(self.focus_item_on_startup))

        # mount autocomplete widgets
        await self.mount(
            UsersAutoComplete(
                self.users_selector,
                self.api,
                id='filter-assignee-autocomplete',
                user_search_function=self._search_and_filter_users,
            )
        )

    async def fetch_projects(self) -> None:
        """Fetches the list of available projects.

        If the user pre-selects a project using the configuration setting `default_project_key_or_id` or by passing the
        `--project-key` in the CLI command `jiratui ui --project-key` then the application will only fetch that
        project. This speeds up the launching of the app because less API requests are needed.

        If on the other hand, no project key is pre-selected then the application will fetch all available
        projects. This may require multiple API requests; making the application launch slower.

        If a project is pre-selected the application will pre-select the project from the dropdown list.
        a value if required.

        If no project is found then the application will leave the dropdown empty.

        Returns:
            None
        """

        project_keys: list[str] = []
        if self.initial_project_key:
            if self.app.config.fetch_single_project:  # type:ignore[attr-defined]
                project_keys = [self.initial_project_key]
        response: APIControllerResponse = await self.api.search_projects(keys=project_keys)
        if not response.success:
            self.notify(f'Failed to fetch the list of projects: {response.error}', severity='error')
        projects = response.result or []
        projects = list(projects)  # sort is async!
        projects.sort(key=lambda x: x.name)
        self.project_selector.projects = {
            'projects': projects,
            'selection': self.initial_project_key,
        }

    async def fetch_statuses(self) -> list[tuple[str, str]]:
        """Retrieves the valid status codes depending on the selected project and type of work item.

        Returns:
            A list of tuples with the name and id of every project status code.
        """

        if self.project_selector.selection:
            return await self._fetch_project_statuses(self.project_selector.selection)
        response: APIControllerResponse = await self.api.status()
        if not response.success:
            self.logger.error(
                'Failed to fetch the available status codes', extra={'error': response.error}
            )
            return []
        seen_statuses: set[str] = set()
        statuses: list[tuple[str, str]] = []
        for status in response.result or []:
            if str(status.id) not in seen_statuses:
                seen_statuses.add(str(status.id))
                statuses.append((status.name, str(status.id)))
        return sorted(statuses, key=lambda x: x[0])

    async def _fetch_project_statuses(self, project_key: str) -> list[tuple[str, str]]:
        """Fetches the status codes applicable to a project and optionally to the type of issue selected by the user.

        Args:
            project_key: the key of the project whose status codes we want to retrieve.

        Returns:
            A list of tuples with the name and id of every project status code.
        """

        response: APIControllerResponse = await self.api.get_project_statuses(project_key)
        if not response.success:
            self.logger.error(
                'Failed to retrieve the status codes associated to the project',
                extra={'error': response.error, 'project_key': project_key},
            )
            return []
        result = response.result or {}
        if self.issue_type_selector.selection:
            record: dict = result.get(self.issue_type_selector.selection, {})
            return sorted(
                [(status.name, str(status.id)) for status in record.get('issue_type_statuses', [])],
                key=lambda x: x[0],
            )
        statuses: list[tuple[str, str]] = []
        # make sure we don't have duplicate status codes/ids in the select dropdown
        seen_statuses: set[str] = set()
        for _issue_type_id, data in result.items():
            for status in data.get('issue_type_statuses', []):
                if str(status.id) not in seen_statuses:
                    seen_statuses.add(str(status.id))
                    statuses.append((status.name, str(status.id)))
        return sorted(statuses, key=lambda x: x[0])

    async def fetch_issue_types(self) -> list[tuple[str, str]]:
        """Retrieves the list of type of work items.

        If a project is selected then it will retrieve the types of work items associated to the project; otherwise it
        will retrieve all the possible types of work items.

        Returns:
            A list of tuples with the id of the type of issue and the name of the type of issue.
        """

        types: list[IssueType]
        if self.project_selector.selection:
            response: APIControllerResponse = await self.api.get_issue_types_for_project(
                self.project_selector.selection
            )
            if not response.success:
                return []
            types = response.result or []
            types.sort(key=lambda x: x.name)
            return [(item.name, item.id) for item in types or []]

        # retrieve all available types of work items
        response = await self.api.get_issue_types()
        if not response.success:
            return []
        types = response.result or []
        types.sort(key=lambda x: x.name)
        work_item_types: list[tuple[str, str]] = []
        for item in types:
            if item.scope_project:
                name = f'({item.scope_project.name}) {item.name}'
            else:
                name = item.name
            work_item_types.append((name, item.id))
        return work_item_types

    async def _search_and_filter_users(self, query: str) -> APIControllerResponse:
        # searches and filters users that can be assignees of work items
        if self.project_selector.selection:
            return await self.api.search_users_assignable_to_issue(
                project_id_or_key=self.project_selector.selection, query=query
            )
        return await self.api.search_users(email_or_name=query)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == 'fetch_statuses':
            self.available_issues_status = event.worker.result or []
            self.issue_status_selector.statuses = self.available_issues_status
        elif event.worker.name == 'fetch_issue_types':
            self.issue_type_selector.set_options(event.worker.result or [])

    # Work Item Context Menu - start

    @on(IssuesSearchResultsTable.OpenContextMenu)
    def _open_work_item_context_menu(
        self, message: IssuesSearchResultsTable.OpenContextMenu
    ) -> None:
        # when the user requests opening the context menu from the search results table open the context menu screen
        message.stop()
        self.app.push_screen(
            ContextMenu(message.work_item_key), callback=self._execute_contex_menu_action
        )

    def _execute_contex_menu_action(self, data: ContextMenuSelection | None = None) -> None:
        """Executes the action selected by the user from the context menu.

        Args:
            data: the data returned by the context menu screen. This includes the key of work item that is the target
            of the action selected by the user and, the id of the action to run.

        Returns:
            None
        """

        if data and data.work_item_key and data.action_id:
            if data.action_id == ContextMenuOptionAction.CLONE_WORK_ITEM:
                # open the screen to allow the user to override some fields for cloning
                self.app.push_screen(
                    CloneWorkItemScreen(data.work_item_key), callback=self._clone_work_item
                )
            elif data.action_id == ContextMenuOptionAction.ARCHIVE_WORK_ITEM:
                self.run_worker(self._archive_work_item(data.work_item_key), exclusive=True)

    async def _clone_work_item(self, data: CloneWorkItemRequest | None = None) -> None:
        """Clones a work item.

        Args:
            data: contains the key of the work item to clone and the summary for the new item.

        Returns:
            None
        """

        if data:
            response: APIControllerResponse = await self.api.clone_work_item(
                data.work_item_key, data.summary
            )
            if not response.success or not response.result:
                self.notify('Failed to clone the work item', title='Clone', severity='error')
                self.notify(str(response.error), title='Clone', severity='error')
            else:
                self.notify(f'Item cloned with key: {response.result.key}', title='Clone')

    async def _archive_work_item(self, work_item_key: str) -> None:
        """Archives a work item.

        Args:
            work_item_key: the key of the work item to archive.

        Returns:
            None
        """

        response: APIControllerResponse = await self.api.archive_work_item(work_item_key)
        if not response.success or not response.result:
            self.notify('Failed to archive the work item', title='Archive', severity='error')
            self.notify(str(response.error), title='Archive', severity='error')
        else:
            result: WorkItemArchivalSyncResponse | None = response.result
            if result and result.number_of_issues_updated == 1:
                self.notify(
                    f'Item with key: {work_item_key} archived successfully', title='Archive'
                )
            else:
                self.notify('Failed to archive the work item', title='Archive', severity='error')

    # Work Item Context Menu - end

    @on(Select.Changed, '#jira-project-selector')
    async def handle_project_selection(self, event: Select.Changed) -> None:
        """Handles the selection of a project.

        This will trigger the actions to fetch the types of issues and applicable project status codes. It will also
        update the project key of the autocomplete widget used for searching Jira users.

        Args:
            event: the event triggered by the selection.

        Returns:
            Nothing.
        """

        # fetch issue types for the project
        self.run_worker(self.fetch_issue_types())
        # fetch valid status codes
        self.run_worker(self.fetch_statuses())

    async def _search_work_items(
        self,
        next_page_token: str | None = None,
        calculate_total: bool = True,
        search_term: str | None = None,
        page: int | None = None,
    ) -> WorkItemSearchResult:
        """Searches work items.

        Args:
            next_page_token: if provided then the results page with this token wil be retrieved. This is used for
            pagination of results in Jira Cloud Platform.
            calculate_total: if `True` the method will attempt to estimate the total number of results that match the
            search query.
            search_term: this is the search term used for ull-text search.
            page: if provided, then the results page with this number wil/ be retrieved. This is used for
            pagination of results in Jira Data Center Platform.

        Returns:
            An instance of `WorkItemSearchResult` with the results of the search.
        """

        # extract filters for the search
        search_field_status: int | None = None
        if value := self.issue_status_selector.selection:
            search_field_status = int(value)

        search_field_created_from: date | None = None
        if value := self.issue_date_from_input.value:
            search_field_created_from = isoparse(value).date()

        search_field_created_until: date | None = None
        if value := self.issue_date_until_input.value:
            search_field_created_until = isoparse(value).date()

        search_field_assignee: str | None = self.users_selector.account_id

        search_field_issue_type: int | None = None
        if value := self.issue_type_selector.selection:
            search_field_issue_type = value

        project_key: str | None = None
        if value := self.project_selector.selection:
            project_key = value

        order_by = (
            WorkItemsSearchOrderBy(self.order_by_widget.selection)
            if self.order_by_widget.selection
            else None
        )

        # build the JQL query to search items based on a user-provided JQL query expression or a search term.
        jql_query: str | None = build_jql_query(
            search_term=search_term,
            jql_expression=self.jql_expression_input.value,
            use_advance_search=self.app.config.enable_advanced_full_text_search,  # type:ignore[attr-defined]
        )

        # search work items by different criteria
        response: APIControllerResponse
        if self.app.config.cloud:  # type:ignore[attr-defined]
            response = await self.api.search_issues(
                project_key=project_key,
                created_from=search_field_created_from,
                created_until=search_field_created_until,
                status=search_field_status,
                assignee=search_field_assignee,
                issue_type=search_field_issue_type,
                search_in_active_sprint=self.active_sprint_checkbox.value,
                jql_query=jql_query,
                next_page_token=next_page_token,
                limit=self.app.config.search_results_per_page,  # type:ignore[attr-defined]
                order_by=order_by,
                fields=self._search_result_fields(),
            )
        else:
            response = await self.api.search_issues_by_page_number(
                project_key=project_key,
                created_from=search_field_created_from,
                created_until=search_field_created_until,
                status=search_field_status,
                assignee=search_field_assignee,
                issue_type=search_field_issue_type,
                search_in_active_sprint=self.active_sprint_checkbox.value,
                jql_query=jql_query,
                page=page,
                limit=self.app.config.search_results_per_page,  # type:ignore[attr-defined]
                order_by=order_by,
                fields=self._search_result_fields(),
            )

        if not response.success or response.result is None:
            self.notify(
                'There was an error while performing the search',
                severity='warning',
                title='Work Item Search',
            )
            return WorkItemSearchResult(total=0, start=0, end=0)

        # estimation of search results count is only available in Jira Cloud
        if not self.app.config.cloud:  # type:ignore[attr-defined]
            calculate_total = False

        result: JiraIssueSearchResponse | None = response.result
        estimated_total_issues: int = 0
        if calculate_total:
            counting: APIControllerResponse = await self.api.count_issues(
                project_key=project_key,
                created_from=search_field_created_from,
                created_until=search_field_created_until,
                status=search_field_status,
                assignee=search_field_assignee,
                issue_type=search_field_issue_type,
                jql_query=jql_query,
            )
            if counting.success:
                estimated_total_issues = counting.result
            else:
                estimated_total_issues = 0
                self.notify(
                    'Failed to calculate the number of work items',
                    title='Work Items Search',
                    severity='warning',
                )

        # the actual number of results
        issues_count = len(result.issues)
        return WorkItemSearchResult(
            response=result,
            total=estimated_total_issues if estimated_total_issues else 0,
            start=1 if issues_count else 0,
            end=issues_count,
        )

    async def _search_single_issue(self, issue_key: str) -> WorkItemSearchResult:
        response: APIControllerResponse = await self.api.get_issue(
            issue_id_or_key=issue_key,
            fields=[
                'summary',
                'status',
                'issuetype',
                'parent',
                'assignee',
                *fields_for_status_sections(self.app.config.status_sections),  # type:ignore[attr-defined]
            ],
        )
        if not response.success:
            self.notify(
                response.error or 'Unable to fetch the given issue',
                title='Work Items Search',
                severity='error',
            )
            return WorkItemSearchResult(total=0, start=0, end=0)
        if not response.result:
            self.notify(
                f'The selected work item {issue_key} was not found',
                title='Work Items Search',
                severity='error',
            )
            return WorkItemSearchResult(total=0, start=0, end=0)
        total = len(response.result.issues or [])
        return WorkItemSearchResult(response=response.result, total=total, start=total, end=total)

    async def search_issues(
        self,
        next_page_token: str | None = None,
        search_term: str | None = None,
        page: int | None = None,
    ) -> None:
        """Searches work items.

        If a specific issue is specified in the Issue Key input widget then the app searches the details of that issue
        only. If, on the other hand, no issue is specified then the app searches issues based on the given criteria.

        Once the results are retrieved this method will update a reactive attribute in the search results table to
        update the results.

        Args:
            next_page_token: a token that identifies the next page of results.
            search_term: a search term to search items using full-text search.
            page: the page number to fetch results; this is only supported for Jira Data Center (aka. on-premises)

        Returns:
            Nothing.
        """
        self.run_button.loading = True
        results: WorkItemSearchResult
        if (value := self.issue_key_input.value) and isinstance(value, str) and value.strip():
            # search single issue
            results = await self._search_single_issue(value.strip())
        else:
            results = await self._search_work_items(
                next_page_token=next_page_token, search_term=search_term, page=page
            )
        # set the data in the results table
        table = self.search_results_table
        # store the initial results set in the table to handle local searches
        table.set_initial_results_set(results.response)
        # update the result set in the table
        table.search_results = results.response
        table.focus()

        # update results count for the table's container
        self.search_results_container.pagination = {
            'total': results.total,
            'current_page_number': self.search_results_table.page,
        }
        self.run_button.loading = False
        self._start_pull_request_badges()

    @on(Button.Pressed, '#run-button')
    async def handle_run_button(self) -> None:
        self.run_worker(self.action_search())

    def action_show_recent_history(self) -> None:
        self.app.push_screen(
            HistoryScreen(self.app.recent_history_manager),  # type:ignore[attr-defined]
            callback=self._close_recent_history_screen,
        )

    # NEW - Actionable Logic

    def _search_result_fields(self) -> list[str]:
        """Returns the fields every work item search retrieves: those the columns show and those grouping needs."""

        return [
            *get_work_item_search_fields(self.app.config.search_results_columns),  # type:ignore[attr-defined]
            *fields_for_status_sections(self.app.config.status_sections),  # type:ignore[attr-defined]
        ]

    def _clear_work_item_panes(self) -> None:
        """Empties every pane that shows the selected work item, so the next selection fetches it afresh."""

        self.current_loaded_work_item_key = None
        self.issue_info_container.clear_information = True
        self.issue_details_widget.issue = None
        self.issue_comments_widget.comments = None
        self.related_issues_widget.issues = None
        self.issue_remote_links_widget.issue_key = None
        self.issue_attachments_widget.attachments = None
        self.issue_child_work_items_widget.issues = None
        if widget := self.pull_requests_widget:
            widget.clear()

    def _set_active_view(self, position: int | None) -> None:
        """Marks the view at `position` as the one the search results show, or no view when `position` is `None`."""

        self.active_view = position
        if views_bar := self.views_bar:
            views_bar.active_view = position
        title = self.work_item_tabs_titles.get('search_results_container') or 'Work Items'
        if position is not None:
            title = f'{title} · {self.app.config.views[position - 1].label}'  # type:ignore[attr-defined]
        self.search_results_container.border_title = title

    def _startup_view_position(self) -> int | None:
        """Returns the position of the configured startup view, or `None` when no configured view is set to load."""

        if (position := self.app.config.startup_view) is None:  # type:ignore[attr-defined]
            return None
        views = self.app.config.views or []  # type:ignore[attr-defined]
        if 1 <= position <= len(views):
            return position
        self.notify(
            f'startup_view is {position} but {len(views)} views are configured.',
            title='Views',
            severity='warning',
        )
        return None

    def action_load_view(self, position: int) -> None:
        """Loads the view at `position` (1-based) in `config.views`; positions past the last view do nothing."""

        if 1 <= position <= len(self.app.config.views or []):  # type:ignore[attr-defined]
            self.run_worker(self.load_view(position), exclusive=True, group=VIEWS_WORKER_GROUP)

    def action_reload_view(self) -> None:
        """Loads the active view again; with no view active, reads the pull requests on display again."""

        if self.active_view is not None:
            self.action_load_view(self.active_view)
        elif self.pull_request_service is not None:
            self._refresh_pull_requests()
        else:
            self.notify('No view is active. Load one with its key first.', title='Views')

    def _refresh_pull_requests(self) -> None:
        """Reads again the pull requests of the work items on display; the PRs tab follows the new read."""

        self._start_pull_request_badges()

    def _start_pull_request_badges(self) -> None:
        if self.pull_request_service is not None:
            self.run_worker(
                self.refresh_pull_request_badges(),
                exclusive=True,
                group=PULL_REQUESTS_WORKER_GROUP,
            )

    async def refresh_pull_request_badges(self) -> None:
        """Reads the pull requests of every work item in the search results and shows each one's PR badge."""

        if self.pull_request_service is None:
            return
        results = self.search_results_table.get_initial_results_set()
        if not results or not results.issues:
            return
        summaries = await self.pull_request_service.summaries(
            [(work_item.id, work_item.key) for work_item in results.issues]
        )
        self._pull_request_summaries.update(summaries)
        self.search_results_table.set_pull_request_badges(
            {key: pull_request_badge(work_item) for key, work_item in summaries.items()}
        )
        if (widget := self.pull_requests_widget) and (shown := widget.work_item_key) in summaries:
            self.run_worker(
                self._show_pull_request_details(summaries[shown]),
                exclusive=True,
                group=PULL_REQUEST_DETAILS_WORKER_GROUP,
            )

    async def load_pull_request_details(self, work_item_id: str, work_item_key: str) -> None:
        """Shows in the PRs tab the pull requests of a work item, with Qodo's review of each open one.

        The pull requests the last badge refresh read are used when there are any; otherwise they are read now.

        Args:
            work_item_id: the ID of the work item.
            work_item_key: the key of the work item.

        Returns:
            None
        """

        widget = self.pull_requests_widget
        if self.pull_request_service is None or widget is None:
            return
        widget.show_loading(work_item_key)
        summary = self._pull_request_summaries.get(work_item_key)
        if summary is None:
            summaries = await self.pull_request_service.summaries([(work_item_id, work_item_key)])
            summary = summaries[work_item_key]
        await self._show_pull_request_details(summary)

    async def _show_pull_request_details(self, summary: WorkItemPullRequests) -> None:
        """Shows `summary` in the PRs tab at once, keeping Qodo's last review there, then with Qodo's review read again."""

        widget = self.pull_requests_widget
        if self.pull_request_service is None or widget is None:
            return
        if widget.work_item_key != summary.work_item_key:
            return
        widget.show(widget.with_shown_qodo_reviews(summary))
        detailed = await self.pull_request_service.with_qodo_reviews(summary)
        if widget.work_item_key == summary.work_item_key:
            widget.show(detailed)

    async def action_focus_work_item_pull_requests_tab(self) -> None:
        if widget := self.pull_requests_widget:
            self.set_focus(widget)

    def _selected_work_item(self) -> JiraIssue | None:
        """Returns the work item highlighted in the search results; with none, says so and returns `None`."""

        table = self.search_results_table
        results = table.get_initial_results_set()
        work_item = next(
            (
                item
                for item in (results.issues if results else [])
                if item.key == table.current_work_item_key
            ),
            None,
        )
        if work_item is None:
            self.notify('Highlight a work item in the search results first.', title='Work Item')
        return work_item

    def _reload_results(self) -> None:
        """Shows the search results afresh after a work item changed: the active view, else the last search."""

        if self.active_view is not None:
            self.action_load_view(self.active_view)
        else:
            self.run_worker(self.action_search())

    def _run_work_item_action(self, action) -> None:
        self.run_worker(action, group=WORK_ITEM_ACTIONS_WORKER_GROUP)

    def action_pick_up(self) -> None:
        """Assigns the highlighted work item to the user and moves it to the pick-up status."""

        if work_item := self._selected_work_item():
            self._run_work_item_action(self.pick_up(work_item))

    def action_move_to_next_column(self) -> None:
        """Moves the highlighted work item to the next board column it can reach."""

        if work_item := self._selected_work_item():
            self._run_work_item_action(self.move_to_column(work_item, 1))

    def action_move_to_previous_column(self) -> None:
        """Moves the highlighted work item to the previous board column it can reach."""

        if work_item := self._selected_work_item():
            self._run_work_item_action(self.move_to_column(work_item, -1))

    def action_open_pull_request(self) -> None:
        """Opens the highlighted work item's open pull requests, else its last one, in the browser."""

        if work_item := self._selected_work_item():
            self._run_work_item_action(self.open_pull_requests(work_item))

    def action_hand_to_claude(self) -> None:
        """Opens a Windows Terminal tab where Claude Code starts on the highlighted work item."""

        if not (work_item := self._selected_work_item()):
            return
        settings = self.app.config.work_item_actions  # type:ignore[attr-defined]
        allowed = {status.casefold() for status in settings.claude_statuses}
        if work_item.status_name.casefold() not in allowed:
            self.notify(
                f'Claude Code takes {", ".join(settings.claude_statuses)} work items; '
                f'{work_item.key} is {work_item.status_name}.',
                title='Claude Code',
                severity='warning',
            )
            return
        self._run_work_item_action(self.hand_to_claude(work_item))

    async def _my_user(self) -> str | None:
        """Returns the user to assign work to: the configured account ID, else the API user's account or username."""

        if account_id := self.app.config.jira_account_id:  # type:ignore[attr-defined]
            return account_id
        response: APIControllerResponse = await self.api.myself()
        if not response.success or response.result is None:
            return None
        if self.app.config.cloud:  # type:ignore[attr-defined]
            return response.result.account_id
        return response.result.username

    async def pick_up(self, work_item: JiraIssue) -> None:
        """Assigns `work_item` to the user, then moves it to the pick-up status when the workflow allows it."""

        status = self.app.config.work_item_actions.pick_up_status  # type:ignore[attr-defined]
        user = await self._my_user()
        if not user:
            self.notify('Your Jira user could not be found.', title='Pick Up', severity='error')
            return
        assigned: APIControllerResponse = await self.api.assign_work_item(work_item.key, user)
        if not assigned.success:
            self.notify(
                assigned.error or 'The work item could not be assigned.',
                title='Pick Up',
                severity='error',
            )
            return
        if work_item.status_name.casefold() == status.casefold():
            self.notify(
                f'{work_item.key} is yours; it was already {work_item.status_name}.',
                title='Pick Up',
            )
            self._reload_results()
            return
        transitions: APIControllerResponse = await self.api.transitions(work_item.key)
        transition = (
            find_transition_to_status(transitions.result or [], status)
            if transitions.success
            else None
        )
        if transition is None:
            self.notify(
                f'{work_item.key} is yours, but the workflow does not let it move from '
                f'{work_item.status_name} to {status}.',
                title='Pick Up',
                severity='warning',
            )
        else:
            moved: APIControllerResponse = await self.api.transition_issue_status(
                work_item.key, transition.to_state.id
            )
            if moved.success:
                self.notify(
                    f'{work_item.key} is yours and {transition.to_state.name}.', title='Pick Up'
                )
            else:
                self.notify(
                    f'{work_item.key} is yours, but it did not move to {status}: {moved.error}',
                    title='Pick Up',
                    severity='warning',
                )
        self._reload_results()

    async def move_to_column(self, work_item: JiraIssue, step: int) -> None:
        """Moves `work_item` to the nearest reachable board column after (`step=1`) or before (`step=-1`) its own."""

        direction = 'next' if step > 0 else 'previous'
        sections = self.app.config.status_sections or []  # type:ignore[attr-defined]
        if not any(section.statuses and not section.labels for section in sections):
            self.notify(
                'Moving along the board needs status_sections that name statuses.',
                title='Move',
                severity='warning',
            )
            return
        transitions: APIControllerResponse = await self.api.transitions(work_item.key)
        if not transitions.success:
            self.notify(
                transitions.error or 'The transitions could not be read.',
                title='Move',
                severity='error',
            )
            return
        transition = find_column_transition(
            work_item.status_name, sections, transitions.result or [], step
        )
        if transition is None:
            self.notify(
                f'{work_item.key} cannot move to a {direction} column from {work_item.status_name}.',
                title='Move',
                severity='warning',
            )
            return
        moved: APIControllerResponse = await self.api.transition_issue_status(
            work_item.key, transition.to_state.id
        )
        if not moved.success:
            self.notify(
                moved.error or 'The work item did not move.', title='Move', severity='error'
            )
            return
        self.notify(f'{work_item.key} moved to {transition.to_state.name}.', title='Move')
        self._reload_results()

    async def _work_item_pull_requests(self, work_item: JiraIssue) -> WorkItemPullRequests | None:
        """Returns the work item's pull requests, read now when the last badge refresh did not cover it."""

        if self.pull_request_service is None:
            return None
        if (summary := self._pull_request_summaries.get(work_item.key)) is None:
            summaries = await self.pull_request_service.summaries([(work_item.id, work_item.key)])
            summary = self._pull_request_summaries[work_item.key] = summaries[work_item.key]
        return summary

    async def open_pull_requests(self, work_item: JiraIssue) -> None:
        """Opens `work_item`'s open pull requests, else its last one, in the browser."""

        if self.pull_request_service is None:
            self.notify(
                'Opening pull requests needs the pull_requests setting.',
                title='Open PR',
                severity='warning',
            )
            return
        urls = pull_request_urls(await self._work_item_pull_requests(work_item))
        if not urls:
            self.notify(f'No pull request is linked to {work_item.key}.', title='Open PR')
            return
        for url in urls:
            self.app.open_url(url)
        self.notify(f'Opening {len(urls)} pull request(s) of {work_item.key}.', title='Open PR')

    async def hand_to_claude(self, work_item: JiraIssue) -> None:
        """Starts Claude Code on `work_item` in its pull request's repository, or in a repository the user picks."""

        repositories: dict[str, str] = self.app.config.work_item_actions.repositories  # type:ignore[attr-defined]
        repository = pull_request_repository(await self._work_item_pull_requests(work_item))
        if repository and (folder := repositories.get(repository)):
            self._launch_claude(work_item.key, folder)
            return
        if not repositories:
            self.notify(
                'Handing work items to Claude Code needs work_item_actions.repositories.',
                title='Claude Code',
                severity='warning',
            )
            return

        def launch_in(folder: str | None) -> None:
            if folder:
                self._launch_claude(work_item.key, folder)

        await self.app.push_screen(
            RepositoryPickerScreen(work_item.key, repositories), callback=launch_in
        )

    def _launch_claude(self, work_item_key: str, folder: str) -> None:
        """Opens a Windows Terminal tab in `folder` running the configured Claude Code command for the work item."""

        terminal = shutil.which('wt')
        shell = shutil.which('pwsh') or shutil.which('powershell')
        if terminal is None or shell is None:
            self.notify(
                'Handing a work item to Claude Code needs Windows Terminal (wt) and PowerShell.',
                title='Claude Code',
                severity='error',
            )
            return
        if not Path(folder).is_dir():
            self.notify(
                f'The folder {folder} does not exist.', title='Claude Code', severity='error'
            )
            return
        command = self.app.config.work_item_actions.claude_command.replace('{key}', work_item_key)  # type:ignore[attr-defined]
        try:
            subprocess.Popen(
                claude_launch_arguments(terminal, shell, folder, work_item_key, command)
            )
        except OSError as error:
            self.notify(
                f'Windows Terminal did not start: {error}', title='Claude Code', severity='error'
            )
            return
        self.notify(f'Claude Code is starting on {work_item_key} in {folder}.', title='Claude Code')

    async def load_view(self, position: int) -> None:
        """Searches every work item of the view at `position` and shows them, unpaged, in the search results.

        Args:
            position: the 1-based position of the view in `config.views`.

        Returns:
            None
        """

        view = self.app.config.views[position - 1]  # type:ignore[attr-defined]
        self._clear_work_item_panes()
        self._set_active_view(position)
        self.run_button.loading = True
        try:
            response: APIControllerResponse = await self.api.search_view_work_items(
                view,
                fields=self._search_result_fields(),
                max_results=self.app.config.view_max_results,  # type:ignore[attr-defined]
            )
        finally:
            self.run_button.loading = False
        if self.active_view != position:
            return
        if not response.success or response.result is None:
            self.notify(
                response.error or 'The view could not be searched.',
                title=view.label,
                severity='error',
            )
            return
        results: JiraIssueSearchResponse = response.result
        table = self.search_results_table
        table.page = 1
        table.token_by_page = {}
        table.set_initial_results_set(results)
        table.search_results = results
        table.focus()
        self.search_results_container.pagination = {
            'total': len(results.issues),
            'current_page_number': 1,
            'paged': False,
        }
        if views_bar := self.views_bar:
            views_bar.set_count(position, len(results.issues))
        self._start_pull_request_badges()
        if results.is_last is False:
            self.notify(
                f'Showing the first {len(results.issues)} work items. Raise view_max_results to load more.',
                title=view.label,
                severity='warning',
            )

    async def action_search(self, search_term: str | None = None) -> None:
        """Handles the event  when the user presses the "search" button."""

        self.app.workers.cancel_group(self, VIEWS_WORKER_GROUP)
        self._set_active_view(None)
        self._clear_work_item_panes()
        # reset the current page
        self.search_results_table.page = 1
        # clear the token-based pagination control
        self.search_results_table.token_by_page = {}
        # Jira Cloud supports pagination based on a next page token; Jira Data Center (aka. on-premises) uses offset
        # to fetch pages.
        next_page_token: str | None = self.search_results_table.token_by_page.get(
            self.search_results_table.page
        )
        # search the work items that match the criteria
        self.run_worker(
            self.search_issues(
                next_page_token=next_page_token,
                search_term=search_term,
                page=self.search_results_table.page,
            ),
            exclusive=True,
        )

    async def action_focus_project_filter(self) -> None:
        self.set_focus(self.project_selector)

    async def action_focus_search_work_item_type_filter(self) -> None:
        self.set_focus(self.issue_type_selector)

    async def action_focus_search_work_item_status_filter(self) -> None:
        self.set_focus(self.issue_status_selector)

    async def action_focus_search_assignee_filter(self) -> None:
        self.set_focus(self.users_selector)

    async def action_focus_search_work_item_key_filter(self) -> None:
        self.set_focus(self.issue_key_input)

    async def action_focus_search_created_from_filter(self) -> None:
        self.set_focus(self.issue_date_from_input)

    async def action_focus_search_created_until_filter(self) -> None:
        self.set_focus(self.issue_date_until_input)

    async def action_focus_search_sort_filter(self) -> None:
        self.set_focus(self.order_by_widget)

    async def action_focus_search_sprint_filter(self) -> None:
        self.set_focus(self.active_sprint_checkbox)

    async def action_focus_search_jql(self) -> None:
        self.set_focus(self.jql_expression_input)

    async def action_focus_search_results(self) -> None:
        self.set_focus(self.search_results_table)

    async def action_focus_work_item_information_tab(self) -> None:
        self.set_focus(self.issue_info_container)

    async def action_focus_work_item_details_tab(self) -> None:
        self.set_focus(self.issue_details_widget)

    async def action_focus_work_item_comments_tab(self) -> None:
        self.set_focus(self.issue_comments_widget)

    async def action_focus_work_item_related_tab(self) -> None:
        self.set_focus(self.related_issues_widget)

    async def action_focus_work_item_attachments_tab(self) -> None:
        self.set_focus(self.issue_attachments_widget)

    async def action_focus_work_item_links_tab(self) -> None:
        self.set_focus(self.issue_remote_links_widget)

    async def action_focus_work_item_subtasks_tab(self) -> None:
        self.set_focus(self.issue_child_work_items_widget)

    def action_copy_issue_url(self) -> None:
        """Copy to the clipboard the URL of the item currently selected in the search results."""
        if (table := self.search_results_table) and table.current_work_item_key:
            if url := build_external_url_for_issue(table.current_work_item_key):
                self.app.copy_to_clipboard(url)
                self.notify('Work item URL copied!')

    def action_copy_issue_key(self) -> None:
        """Copy to the clipboard the key of the item currently selected in the search results."""
        if (table := self.search_results_table) and table.current_work_item_key:
            self.app.copy_to_clipboard(self.search_results_table.current_work_item_key)
            self.notify('Work item Key copied!')

    async def action_create_work_item(self) -> None:
        """Handles the event to create a new work item."""
        await self.app.push_screen(
            AddWorkItemScreen(
                project_key=self.project_selector.selection,
                reporter_account_id=self.initial_assignee_account_id,
            ),
            callback=self.create_work_item,
        )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Check if an action may run."""

        if table := self.query_one_optional(IssuesSearchResultsTable):
            if action == 'copy_issue_key' and table and not table.current_work_item_key:
                return False
            elif action == 'copy_issue_url' and table and not table.current_work_item_key:
                return False
        return True

    # END - Actionable Logic

    @on(SubtasksWidget.CreateSubtask)
    async def _create_work_item_subtask(self, message: SubtasksWidget.CreateSubtask) -> None:
        await self.app.push_screen(
            AddWorkItemScreen(
                project_key=message.project_key,
                reporter_account_id=self.initial_assignee_account_id,
                parent_work_item_key=message.parent_work_item_key,
            ),
            callback=self.create_work_item,
        )

    async def create_work_item(self, data: dict) -> None:
        """Handles the event to create a work item after the user clicks on the "save" button in the create-work-item
        screen.

        This method calls the API to create the new work item with the data returned by the create-work-item screen and,
        if the user set the status of the item then it calls the API to transition item to the desired status.

        If the item is created successfully this method will open the quick-view modal screen (if
        `config.view_work_item_after_creation = True`) and, if `config.enable_recent_history = True` it will also add
        the item to the recent history.

        Args:
            data: a dictionary with the details of the fields and values to create the item. This data is provided upon
            dismissal of the create-work-item modal screen.

        Returns:
            None
        """

        if data:
            self.loading_container.display = True

            # split data into base fields and dynamic fields (custom fields, components, etc.)
            # base fields are handled differently by the controller
            base_fields = {
                'project_key',
                'parent_key',
                'issue_type_id',
                'assignee_account_id',
                'reporter_account_id',
                'summary',
                'description',
                'duedate',
                'priority',
                'status',
            }
            # separate base data from dynamic fields (custom fields, components, etc.)
            base_data: dict = {k: v for k, v in data.items() if k in base_fields}
            dynamic_fields: dict = {k: v for k, v in data.items() if k not in base_fields}
            # request the API to create the work item
            response: APIControllerResponse = await self.api.create_work_item(
                base_data, **dynamic_fields
            )
            created_work_item: JiraBaseIssue | None
            if response.success and (created_work_item := response.result):
                new_status: str | None
                # check if the user wants to set the new item's status; i.e. transition the item to a different status
                if (new_status := data.get('status')) is not None:
                    response = await self.api.transition_issue_status(
                        created_work_item.key, new_status
                    )
                    self.loading_container.display = False
                    if not response.success:
                        self.notify(
                            f'Failed to transition the work item to a different status: {response.error}',
                            severity='error',
                            title='Status Transition',
                        )
                self.loading_container.display = False
                self.notify(
                    f'Work item {created_work_item.key} created successfully',
                    title='Create Work Item',
                )
                if self.app.config.view_work_item_after_creation:  # type:ignore[attr-defined]
                    self.call_next(self._open_quick_view_screen, created_work_item.key)
                if self.app.config.enable_recent_history:  # type:ignore[attr-defined]
                    self.app.add_to_recent_history(HistoryEntry(created_work_item.key, '', '', ''))  # type:ignore[attr-defined]
            else:
                self.loading_container.display = False
                self.notify(
                    f'Failed to create the work item: {response.error}',
                    severity='error',
                    title='Create Work Item',
                )

    async def _open_quick_view_screen(self, work_item_key: str) -> None:
        # opens a modal screen to view the details of a recently-created work item
        await self.app.push_screen(
            WorkItemQuickViewScreen(work_item_key),
            self._load_work_item,
        )

    async def retrieve_issue_subtasks(self, work_item: JiraIssue) -> None:
        work_item_subtasks = WorkItemSubtasks(
            work_item_key=work_item.key, project_key=work_item.project.key
        )
        response: APIControllerResponse = await self.api.search_issues(
            jql_query=f'parent={work_item.key}',
            fields=['id', 'key', 'status', 'summary', 'issuetype', 'assignee'],
        )
        if response.success and response.result:
            work_item_subtasks.issues = response.result.issues
        elif not response.success:
            self.logger.error(
                'Unable to retrieve the sub tasks of the work item',
                extra={'error': response.error, 'issue_key': work_item.key},
            )
            self.notify(
                'Unable to retrieve the sub tasks of the work item',
                severity='warning',
                title='Work Item Search',
            )
        self.issue_child_work_items_widget.issues = work_item_subtasks

    async def fetch_issue(self, selected_work_item_key: str) -> None:
        """Retrieves the details of a work item selected by the user in the search results.

        This is triggered from the datatable that holds the search results:
        [IssuesSearchResultsTable](#jiratui.widgets.search.IssuesSearchResultsTable)

        Every time a user selects a work item in the search results the application will do the following:

        - retrieve the details of the work item by sending a request to the
        API's [get_issue](#jiratui.api_controller.controller.APIController.get_issue) method.
        - update the information on the description tab (summary and description)
        - retrieve the subtasks associated to the selected work item
        - update the data in all the other tabs

        Args:
            selected_work_item_key: the key of the work item selected by the user from the search results datatable.

        Returns:
            None
        """

        if not selected_work_item_key:
            self.notify(
                'You need to select a work item before fetching its details.',
                title='Find Work Item',
                severity='error',
            )
            return

        # show loading indicator
        self.issue_info_container.show_loading()

        # step 1: fetch issue
        response: APIControllerResponse = await self.api.get_issue(
            issue_id_or_key=selected_work_item_key,
        )
        if not response.success or not response.result:
            self.issue_info_container.hide_loading()
            self.notify(
                'Unable to find the selected work item', title='Not Found', severity='error'
            )
            return

        result: JiraIssueSearchResponse | None = response.result
        if not result or not result.issues:
            self.issue_info_container.hide_loading()
            self.notify(
                'Unable to find the selected work item', title='Not Found', severity='error'
            )
            return

        work_item: JiraIssue = result.issues[0]
        # track the currently loaded work item
        self.current_loaded_work_item_key = work_item.key

        # step 2: populate information tab
        self.issue_info_container.issue = work_item
        self.issue_info_container.hide_loading()

        # step 3: populate the details tab
        self.issue_details_widget.issue = work_item

        # step 4: populate the related-issues tab
        self.related_issues_widget.issues = WorkItemRelatedItems(
            work_item_key=work_item.key, related_items=work_item.related_issues
        )

        # step 5: populate comments tab
        self.issue_comments_widget.comments = WorkItemComments(
            work_item_key=work_item.key, comments=work_item.comments
        )

        # step 6: populate attachments tab
        self.issue_attachments_widget.attachments = WorkItemAttachments(
            work_item_key=work_item.key, attachments=work_item.attachments
        )

        # step 7: populate links tab
        if self.app.config.show_issue_web_links:  # type:ignore[attr-defined]
            self.issue_remote_links_widget.issue_key = work_item.key

        # step 8: populate subtasks tab
        self.run_worker(self.retrieve_issue_subtasks(work_item))

        # step 9: populate the pull requests tab
        if self.pull_request_service is not None:
            self.run_worker(
                self.load_pull_request_details(work_item.id, work_item.key),
                exclusive=True,
                group=PULL_REQUEST_DETAILS_WORKER_GROUP,
            )

        if self.app.config.enable_recent_history:  # type:ignore[attr-defined]
            self.app.add_to_recent_history(  # type:ignore[attr-defined]
                HistoryEntry(
                    key=work_item.key,
                    item_type=work_item.issue_type.name if work_item.issue_type else '',
                    status=work_item.status.name if work_item.status else '',
                    summary=work_item.summary,
                )
            )

    async def request_text_search(self, value: str):
        value = value or ''
        if (value := value.strip()) and len(value) >= max(
            FULL_TEXT_SEARCH_DEFAULT_MINIMUM_TERM_LENGTH,
            int(self.app.config.full_text_search_minimum_term_length),  # type:ignore[attr-defined]
        ):
            self.run_worker(self.action_search(search_term=value))
        else:
            self.notify('Nothing to search')

    async def action_find_by_text(self) -> None:
        """Opens a screen to allow the user to enter a term to search items by text."""
        await self.app.push_screen(TextSearchScreen(), self.request_text_search)

    async def _focus_item_after_startup(self, position: int) -> None:
        """Focuses and opens a work item at the specified position after startup search completes.

        Args:
            position: the 1-based position of the work item in the search results to focus and open.

        Returns:
            None
        """

        import asyncio

        # Get the search results table
        table = self.search_results_table

        # Wait for the table to be populated with results (with timeout)
        max_attempts = 50  # 5 seconds total
        attempt = 0
        row_count = 0

        while attempt < max_attempts:
            await self.app.animator.wait_for_idle()
            await asyncio.sleep(0.1)
            row_count = table.work_item_count
            if row_count > 0:
                break
            attempt += 1

        # Check if we timed out waiting for results
        if row_count == 0:
            self.notify(
                'Search results are empty or still loading.',
                severity='warning',
                title='Focus Work Item',
            )
            return

        # Check if the position is valid
        if position < 1 or position > row_count:
            self.notify(
                f'Position {position} is out of range. Search results contain {row_count} item(s).',
                severity='warning',
                title='Focus Work Item',
            )
            return

        if (row_index := table.row_index_for_position(position)) is None:
            self.notify(
                f'Work item {position} is in a collapsed section. Expand its section to open it.',
                severity='warning',
                title='Focus Work Item',
            )
            return

        # Move cursor to this row
        table.move_cursor(row=row_index)

        # Give a moment for the cursor to move
        await asyncio.sleep(0.1)
        await self.app.animator.wait_for_idle()

        # Trigger the row selection action (simulates pressing Enter)
        # This will fire the on_data_table_row_selected event which calls fetch_issue
        table.action_select_cursor()

        # Wait for the issue to be fetched and rendered
        await asyncio.sleep(0.3)
        await self.app.animator.wait_for_idle()

        # Shift focus to the issue info container so user can interact with the issue
        self.issue_info_container.focus()

    @on(IssuesSearchResultsTable.WorkItemDeleted)
    def _clear_work_item_data_after_deletion(
        self, message: IssuesSearchResultsTable.WorkItemDeleted
    ) -> None:
        """Clears the data of a selected item when the item is deleted.

        When a work item is deleted and the item is the one currently being displayed in the information tabs on the
        right-hand panel let's make sure we clean the forms and widgets. This way we avoid displaying the details of
        the item that was deleted. This is only done when the item deleted is the one currently being displayed.

        Args:
            message: the message sent by a widget, e.g. the DataTable that holds the search results, when a work item is
            deleted. The message contains the key of the work item that was deleted.

        Returns:
            None
        """

        if self.current_loaded_work_item_key == message.work_item_key:
            # clean up the forms and tabs
            self.related_issues_widget.issues = None
            self.issue_comments_widget.comments = None
            self.issue_attachments_widget.attachments = None
            self.issue_remote_links_widget.issue_key = None
            self.issue_child_work_items_widget.issues = None
            self.issue_info_container.issue = None
            self.issue_details_widget.issue = None
            self.current_loaded_work_item_key = None

        if self.issue_key_input.value == message.work_item_key:
            self.issue_key_input.value = ''

    @on(WorkItemInfoContainer.WorkItemUpdated)
    def _refresh_work_item_after_update(
        self, message: WorkItemInfoContainer.WorkItemUpdated
    ) -> None:
        """Fetches the work item's details after some of its details have been updated in the Info tab

        Args:
            message: the message sent by the `WorkItemInfoContainer` widget after some of the details of a work item
            have been updated.

        Returns:
            None
        """

        self.run_worker(self.fetch_issue(message.work_item_key), exclusive=True)

    def _load_work_item(self, work_item_key: str | None = None) -> None:
        """Searches a work item.

        Args:
            work_item_key: the key of the item to search and display.

        Returns:
            None
        """

        if work_item_key and work_item_key.strip():
            self.issue_key_input.value = work_item_key
            self.run_worker(self.action_search(), exclusive=True)
            self.call_next(self.fetch_issue, work_item_key)

    def _close_recent_history_screen(self, work_item_key: str | None = None) -> None:
        # when the modal screen that shows the recent history is dismissed with a work item's key then it means that
        # the user has requested to search for the work item with that key
        self._load_work_item(work_item_key)

    @on(IssueDetailsWidget.UpdateRecentHistory)
    def _update_recent_history(self, message: IssueDetailsWidget.UpdateRecentHistory) -> None:
        message.stop()
        if self.app.config.enable_recent_history:  # type:ignore[attr-defined]
            self.app.add_to_recent_history(  # type:ignore[attr-defined]
                HistoryEntry(
                    key=message.work_item_key,
                    item_type=message.item_type or '',
                    status=message.status or '',
                    summary=message.summary or '',
                )
            )

    @on(SearchWorkItem)
    def _on_search_work_item(self, message: SearchWorkItem) -> None:
        # this handles the message sent by another widget requesting to search a work item by its key
        if message.work_item_key:
            self._load_work_item(message.work_item_key)
