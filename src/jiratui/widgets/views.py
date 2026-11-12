"""The bar that lists the work-item views defined in the configuration."""

from textual.markup import escape
from textual.reactive import Reactive, reactive
from textual.widgets import Static


class ViewsBar(Static):
    """Lists the configured views, each with the key that loads it and the number of work items it last loaded.

    The view whose results the search results table shows is highlighted. Clicking a view loads it.
    """

    active_view: Reactive[int | None] = reactive(None)
    """The 1-based position of the view the search results table shows; `None` once any other search replaces it."""

    def __init__(self, labels: list[str], keys: list[str], show_keys: bool = True) -> None:
        """Initializes the bar.

        Args:
            labels: the labels of the configured views, in configuration order.
            keys: the keys that load the views, in the same order; views past the last key have no key.
            show_keys: whether each view is preceded by its key.
        """

        super().__init__(id='views-bar')
        self._labels = labels
        self._keys = keys
        self._show_keys = show_keys
        self._counts: dict[int, int] = {}

    def on_mount(self) -> None:
        self._render_views()

    def watch_active_view(self) -> None:
        self._render_views()

    def set_count(self, position: int, count: int) -> None:
        """Records the number of work items the view at `position` loaded and shows it next to its label."""

        self._counts[position] = count
        self._render_views()

    def describe_view(self, position: int) -> str:
        """Returns the text that labels the view at `position` in the bar, e.g. `alt+1 My work (26)`."""

        text = escape(self._labels[position - 1])
        if self._show_keys and position <= len(self._keys):
            text = f'{escape(self._keys[position - 1])} {text}'
        if (count := self._counts.get(position)) is not None:
            text = f'{text} ({count})'
        return text

    def _render_views(self) -> None:
        entries: list[str] = []
        for position in range(1, len(self._labels) + 1):
            text = f' {self.describe_view(position)} '
            if position == self.active_view:
                text = f'[reverse bold]{text}[/]'
            entries.append(f'[@click=screen.load_view({position})]{text}[/]')
        self.update(' '.join(entries))
