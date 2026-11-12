"""Builds the JQL behind configured work-item views and groups work items into status sections."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
import re

from jiratui.config import StatusSectionConfiguration
from jiratui.models import JiraIssue

DEFAULT_VIEW_ORDER_BY = 'ORDER BY Rank ASC'
"""Views without their own ORDER BY clause follow board rank, so each status section reads like a board column."""

_ORDER_BY = re.compile(r'\border\s+by\b', re.IGNORECASE)


@dataclass
class WorkItemSection:
    """The work items that fall in one status section, in the order the search returned them."""

    label: str
    work_items: list[JiraIssue] = field(default_factory=list)
    collapsed: bool = False
    """Whether the section starts collapsed."""


def split_order_by(jql: str) -> tuple[str, str]:
    """Separates a JQL expression's filter from its trailing ORDER BY clause.

    Text inside quoted strings never counts as a clause, so `summary ~ "order by"` stays part of the filter.

    Args:
        jql: a JQL expression, with or without an ORDER BY clause.

    Returns:
        The filter and the ORDER BY clause, both stripped; the clause is an empty string when the expression has none.
    """

    matches = list(_ORDER_BY.finditer(_mask_quoted_text(jql)))
    if not matches:
        return jql.strip(), ''
    start = matches[-1].start()
    return jql[:start].strip(), jql[start:].strip()


def build_view_jql(jql: str) -> str:
    """Returns a view's JQL ordered by board rank unless the view orders its results itself."""

    filter_jql, order_by = split_order_by(jql)
    return f'{filter_jql} {order_by or DEFAULT_VIEW_ORDER_BY}'.strip()


def build_related_view_jql(
    jql: str, parent_keys: Sequence[str], linked_keys: Iterable[str]
) -> str | None:
    """Builds the JQL of a view that selects work items related to a base set of work items.

    A work item is related when it is a sub-task of a base work item or is linked to one. The view's own JQL then
    narrows the related work items, e.g. to bugs only.

    Args:
        jql: the view's JQL, applied to the related work items.
        parent_keys: the keys of the base work items.
        linked_keys: the keys of the work items linked to the base work items.

    Returns:
        The JQL to search, or `None` when there are no base work items and so nothing can be related.
    """

    if not parent_keys:
        return None
    candidates = [f'parent in ({", ".join(parent_keys)})']
    if linked := sorted(set(linked_keys) - set(parent_keys)):
        candidates.append(f'key in ({", ".join(linked)})')
    related = ' OR '.join(candidates)
    filter_jql, order_by = split_order_by(jql)
    narrowed = f'({related}) AND ({filter_jql})' if filter_jql else f'({related})'
    return f'{narrowed} {order_by or DEFAULT_VIEW_ORDER_BY}'


def group_work_items_by_status(
    work_items: Iterable[JiraIssue], sections: Sequence[StatusSectionConfiguration]
) -> list[WorkItemSection]:
    """Groups work items into the configured status sections.

    Sections keep their configured order and are left out when empty. A status that no section names gets a section of
    its own, labelled with the status name, after the configured ones. Status names match regardless of case.

    Args:
        work_items: the work items to group.
        sections: the configured status sections.

    Returns:
        The non-empty sections, configured ones first.
    """

    grouped = [
        WorkItemSection(label=section.label, collapsed=section.collapsed) for section in sections
    ]
    position_by_status = {
        status.casefold(): position
        for position, section in enumerate(sections)
        for status in section.statuses
    }
    unconfigured: dict[str, WorkItemSection] = {}
    for work_item in work_items:
        status_name = work_item.status_name
        if (position := position_by_status.get(status_name.casefold())) is not None:
            grouped[position].work_items.append(work_item)
        else:
            unconfigured.setdefault(
                status_name.casefold(), WorkItemSection(label=status_name or 'No status')
            ).work_items.append(work_item)
    return [section for section in grouped if section.work_items] + list(unconfigured.values())


def _mask_quoted_text(jql: str) -> str:
    """Replaces every character inside a quoted string with a space, keeping positions aligned with the input."""

    masked: list[str] = []
    quote: str | None = None
    escaped = False
    for character in jql:
        if quote:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == quote:
                quote = None
                masked.append(character)
                continue
            masked.append(' ')
        else:
            if character in ('"', "'"):
                quote = character
            masked.append(character)
    return ''.join(masked)
