"""Read-only table windows (ttk.Treeview with a scroll bar).

TableWindow is used for submissions (RF17) and grades (RF18); a double
click can open a detail. AnnouncementsWindow adds a text pane that shows
the message of the selected announcement (RF16).
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from aulari.texts import text

PADDING = 8
VISIBLE_ROWS = 15
MESSAGE_LINES = 8
CHAR_PIXELS = 8  # approximate width of one character, for column widths


class TableWindow(tk.Toplevel):
    """A window with a table; columns are (heading, width in characters)."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        columns: list[tuple[str, int]],
        rows: list[tuple[str, ...]],
        on_open: Callable[[int], None] | None = None,
    ) -> None:
        """Build the table and fill it with rows (all values already text)."""
        super().__init__(parent)
        self.title(title)
        self._on_open = on_open
        frame = ttk.Frame(self, padding=PADDING)
        frame.pack(fill="both", expand=True)
        names = [f"c{index}" for index in range(len(columns))]
        self.tree = ttk.Treeview(
            frame, columns=names, show="headings", height=min(max(len(rows), 1), VISIBLE_ROWS)
        )
        for name, (heading, width) in zip(names, columns, strict=True):
            self.tree.heading(name, text=heading, anchor="w")
            self.tree.column(name, width=width * CHAR_PIXELS, anchor="w")
        for row in rows:
            self.tree.insert("", "end", values=row)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        if on_open is not None:
            self.tree.bind("<Double-1>", self._double_click)
            ttk.Label(self, text=text("table_double_click_hint")).pack(anchor="w", padx=PADDING)
        ttk.Button(self, text=text("close_button"), command=self.destroy).pack(pady=PADDING)

    def selected_index(self) -> int | None:
        """Return the index of the selected row, or None."""
        result = None
        selection = self.tree.selection()
        if selection:
            result = self.tree.index(selection[0])
        return result

    def _double_click(self, _event: tk.Event) -> None:
        """Open the detail of the double-clicked row."""
        index = self.selected_index()
        if index is not None and self._on_open is not None:
            self._on_open(index)


class AnnouncementsWindow(TableWindow):
    """Table of announcements plus the plain-text message of the selected one."""

    def __init__(self, parent: tk.Misc, rows: list[tuple[str, ...]], messages: list[str]) -> None:
        """Build the table; selecting a row shows its message below."""
        columns = [
            (text("column_date"), 16),
            (text("column_course"), 10),
            (text("column_subject"), 50),
        ]
        super().__init__(parent, text("announcements_title"), columns, rows)
        self._messages = messages
        self.message = tk.Text(self, wrap="word", height=MESSAGE_LINES, width=80)
        self.message.pack(fill="both", expand=True, padx=PADDING)
        self.message.configure(state="disabled")
        self.tree.bind("<<TreeviewSelect>>", self._show_message)
        if rows:
            self.tree.selection_set(self.tree.get_children()[0])

    def _show_message(self, _event: tk.Event | None = None) -> None:
        """Show the message of the selected announcement."""
        index = self.selected_index()
        content = self._messages[index] if index is not None else ""
        self.message.configure(state="normal")
        self.message.delete("1.0", "end")
        self.message.insert("1.0", content)
        self.message.configure(state="disabled")
