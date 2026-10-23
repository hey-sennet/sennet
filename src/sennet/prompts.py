"""Setup's terminal UI: select lists, spinners and download bars.

rich draws the output and questionary asks the questions. Without a terminal, or
with `--yes`, nothing is asked: each question takes its default and says what it
chose, so a log of the run still reads well.
"""
from __future__ import annotations

import contextlib
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import questionary
from prompt_toolkit.styles import Style
from questionary.prompts import common
from rich.console import Console
from rich.progress import (BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn,
                           TransferSpeedColumn)

from .speech.models import Model, ModelFile

STYLE = Style([
    ("qmark", "fg:#5f87ff bold"),
    ("question", "bold"),
    ("answer", "fg:#5fafff bold"),
    ("pointer", "fg:#5fafff bold"),
    ("highlighted", "fg:#5fafff bold"),
    ("selected", "fg:#5fafff"),
    ("disabled", "fg:#6c6c6c italic"),
    ("instruction", "fg:#6c6c6c"),
    ("text", ""),
    ("note", "fg:#8a8a8a italic"),
])

_choice_tokens = common.InquirerControl._get_choice_tokens


def _tokens_without_label(self):
    """questionary writes a highlighted option's description as "Description: …"; show just the note, dimmed."""
    tokens = []
    for style, text, *rest in _choice_tokens(self):
        if text.startswith("  Description: "):
            style, text = "class:note", "  " + text[len("  Description: "):]
        tokens.append((style, text, *rest))
    return tokens


common.InquirerControl._get_choice_tokens = _tokens_without_label

class Abort(Exception):
    """The user left a question with Ctrl-C."""


@dataclass
class Option:
    value: Any
    label: str
    detail: str = ""              # beside the label
    note: str = ""                # under the list while this option is highlighted
    disabled: str | None = None   # why it can't be chosen
    tags: tuple[str, ...] = ()    # recommended, current


class Prompter:
    def __init__(self, interactive: bool, console: Console | None = None):
        self.interactive = interactive
        self.console = console or Console(highlight=False)

    # Questions

    def select(self, message: str, options: list[Option], default: Any = None) -> Any:
        """The chosen option's value. `default` is pre-selected and is the answer without a terminal."""
        enabled = [o for o in options if not o.disabled]
        chosen = next((o for o in enabled if o.value == default), enabled[0])
        if not self.interactive:
            self._answered(message, chosen.label)
            return chosen.value
        width = max(len(o.label) for o in options) + 2
        detail_width = max((len(o.detail) for o in options), default=0) + 2
        choices = [questionary.Choice(title=self._title(o, width, detail_width), value=o.value,
                                      disabled=o.disabled, description=o.note or None)
                   for o in options]
        default_choice = next(c for c in choices if c.value == chosen.value)
        value = self._ask(questionary.select(message, choices, default=default_choice, style=STYLE, qmark="?",
                                             pointer="❯", instruction="(↑↓, enter)"))
        answer = next(o for o in options if o.value == value)
        # questionary echoes the whole row, details and tags too; keep just the label.
        self._replace_echo(f"? {message} {self._title(answer, width, detail_width)}", message, answer.label)
        return value

    def confirm(self, message: str, default: bool = True) -> bool:
        if not self.interactive:
            self._answered(message, "yes" if default else "no")
            return default
        return self._ask(questionary.confirm(message, default=default, style=STYLE, qmark="?"))

    def text(self, message: str, default: str = "", validate: Callable[[str], bool | str] | None = None) -> str:
        if not self.interactive:
            self._answered(message, default)
            return default
        return self._ask(questionary.text(message, default=default, validate=validate, style=STYLE, qmark="?"))

    # Output

    def heading(self, title: str):
        self.console.print()
        self.console.rule(f"[bold]{title}[/]", align="left", style="grey37")

    def info(self, message: str):
        self.console.print(f"  {message}")

    def note(self, message: str):
        self.console.print(f"  [grey62]{message}[/]")

    def ok(self, message: str):
        self.console.print(f"  [green]✓[/] {message}")

    def warn(self, message: str):
        self.console.print(f"  [yellow]![/] {message}")

    def fail(self, message: str):
        self.console.print(f"  [red]✗[/] {message}")

    @contextlib.contextmanager
    def working(self, message: str) -> Iterator[None]:
        """A spinner while something slow runs."""
        with self.console.status(f"[grey62]{message}[/]", spinner="dots"):
            yield

    @contextlib.contextmanager
    def downloads(self) -> Iterator[Downloads]:
        progress = Progress(TextColumn("  {task.description}"), BarColumn(bar_width=28), DownloadColumn(),
                            TransferSpeedColumn(), TimeRemainingColumn(), console=self.console, transient=False)
        with progress:
            yield Downloads(progress)

    # Helpers

    @staticmethod
    def _title(option: Option, width: int, detail_width: int) -> str:
        tags = "  ".join(option.tags)
        return f"{option.label:<{width}}{option.detail:<{detail_width}}{tags}".rstrip()

    def _replace_echo(self, echoed: str, message: str, answer: str):
        if not sys.stdout.isatty():
            return
        rows = -(-len(echoed) // shutil.get_terminal_size().columns)
        sys.stdout.write("\x1b[1A\r\x1b[2K" * rows)
        sys.stdout.flush()
        self._answered(message, answer)

    def _answered(self, message: str, answer: str):
        self.console.print(f"[bold #5f87ff]?[/] [bold]{message}[/] [#5fafff]{answer}[/]")

    @staticmethod
    def _ask(question: questionary.Question) -> Any:
        answer = question.ask()  # None on Ctrl-C
        if answer is None:
            raise Abort()
        return answer


class Downloads:
    """One bar per model, fed by `models.fetch`'s per-file progress."""

    def __init__(self, progress: Progress):
        self.progress = progress

    def bar(self, label: str, model: Model) -> Callable[[ModelFile, int], None]:
        task = self.progress.add_task(label, total=model.size)
        finished: dict[str, int] = {}
        shown = {"at": 0.0}

        def update(file: ModelFile, done: int):
            if done >= file.size:
                finished[file.name] = file.size
            now = time.monotonic()
            if done < file.size and now - shown["at"] < 0.1:
                return
            shown["at"] = now
            current = 0 if file.name in finished else done
            self.progress.update(task, completed=sum(finished.values()) + current)

        return update


def tagged(value: Any, recommended: Any, current: Any) -> tuple[str, ...]:
    return tuple(tag for tag, match in (("recommended", recommended), ("current", current)) if value == match)


def size(n_bytes: int) -> str:
    return f"{n_bytes / 1e9:.1f} GB" if n_bytes >= 1e9 else f"{n_bytes / 1e6:.0f} MB"


def gigabytes(mb: int) -> str:
    return f"{mb / 1024:.1f} GB"
