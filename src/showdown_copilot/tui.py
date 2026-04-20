"""Textual TUI for Showdown Copilot."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Log

from showdown_copilot.engine_client import EngineUpdate


class CopilotApp(App):
    CSS = """
    #best-move { height: 5; border: heavy $accent; padding: 1; }
    #pv { height: 3; border: solid $panel; padding: 0 1; }
    #alternatives { height: 3; border: solid $panel; padding: 0 1; }
    #assumed-sets { border: solid $panel; padding: 0 1; }
    #turn-log { border: solid $panel; }
    """

    def __init__(self, bot_username: str, **kwargs):
        super().__init__(**kwargs)
        self._bot_username = bot_username

    def compose(self) -> ComposeResult:
        yield Header(id="header")
        with Horizontal():
            yield Static("(team preview not received)", id="teams")
        yield Static("best move: —", id="best-move")
        yield Static("PV: —", id="pv")
        yield Static("alternatives: —", id="alternatives")
        yield Static("assumed sets: —", id="assumed-sets")
        yield Log(id="turn-log")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Showdown Copilot"
        self.sub_title = f"spectator: {self._bot_username}"

    def push_engine_update(self, u: EngineUpdate) -> None:
        """Called by the main loop whenever a new NDJSON line arrives."""
        arrow = "▲" if u.is_final else "•"
        self.query_one("#best-move", Static).update(
            f"[bold]{u.best_move}[/bold]  conf {u.confidence:.0%} {arrow}   sims {u.sims:,}  depth {u.depth}"
        )
        if u.pv:
            self.query_one("#pv", Static).update(f"PV: {' → '.join(u.pv)}")
        if u.alternatives:
            alts = " | ".join(f"{a.get('move')} {float(a.get('score',0)):.0%}" for a in u.alternatives)
            self.query_one("#alternatives", Static).update(f"ALTS: {alts}")

    def push_assumed_sets(self, specs_by_species: dict[str, dict]) -> None:
        lines = []
        for sp, info in specs_by_species.items():
            moves = "/".join(info.get("moves", [])[:4])
            star = "*" if info.get("assumed") else " "
            lines.append(f"{sp:16s} @ {info.get('item','?'):16s} {moves} {star}")
        self.query_one("#assumed-sets", Static).update("\n".join(lines) or "—")

    def push_turn_event(self, text: str) -> None:
        self.query_one("#turn-log", Log).write_line(text)
