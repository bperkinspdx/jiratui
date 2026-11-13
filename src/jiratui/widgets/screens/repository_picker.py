"""The modal screen that asks which repository Claude Code starts in."""

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets.option_list import Option

from jiratui.widgets.screens.context_menu import ContextMenuOptionList


class RepositoryPickerScreen(ModalScreen[str]):
    """Lists the configured repositories, the first highlighted, and returns the chosen one's folder.

    Escape closes it without a choice.
    """

    BINDINGS = [Binding('escape', 'app.pop_screen', 'Close')]

    def __init__(self, work_item_key: str, repositories: dict[str, str]):
        """Initializes the screen.

        Args:
            work_item_key: the key of the work item Claude Code is given.
            repositories: the local folder of each repository, by name, in the order to offer them.
        """

        super().__init__()
        self._work_item_key = work_item_key
        self._repositories = repositories

    @property
    def options_list(self) -> ContextMenuOptionList:
        return self.query_one(ContextMenuOptionList)

    def compose(self) -> ComposeResult:
        yield ContextMenuOptionList()

    def on_mount(self) -> None:
        options = self.options_list
        options.border_title = f'Hand {self._work_item_key} to Claude Code in'
        for name, folder in self._repositories.items():
            options.add_option(Option(f'{name}  {folder}', id=folder))
        options.highlighted = 0
        options.focus()

    @on(ContextMenuOptionList.OptionSelected)
    def handle_option_selection(self, message: ContextMenuOptionList.OptionSelected) -> None:  # type:ignore[name-defined]
        message.stop()
        if message.option_id:
            self.dismiss(message.option_id)
