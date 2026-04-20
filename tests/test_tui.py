from textual.widgets import Log

from showdown_copilot.tui import CopilotApp
from showdown_copilot.engine_client import EngineUpdate


async def test_copilot_app_mounts_expected_widgets():
    app = CopilotApp(bot_username="KBotSpec")
    async with app.run_test() as pilot:
        # Verify key widgets are mounted by id
        assert app.query_one("#header") is not None
        assert app.query_one("#best-move") is not None
        assert app.query_one("#pv") is not None
        assert app.query_one("#alternatives") is not None
        assert app.query_one("#assumed-sets") is not None
        assert app.query_one("#turn-log") is not None


async def test_copilot_app_updates_best_move_panel_on_new_update():
    app = CopilotApp(bot_username="KBotSpec")
    async with app.run_test() as pilot:
        u = EngineUpdate(best_move="kowtowcleave", confidence=0.73, sims=142000, depth=8, pv=["kowtowcleave"])
        app.push_engine_update(u)
        await pilot.pause()
        panel = app.query_one("#best-move")
        # Textual renderable — we just confirm the move name ended up in the rendered text
        rendered = str(panel.renderable) if hasattr(panel, 'renderable') else str(panel.render())
        assert "kowtowcleave" in rendered.lower()


async def test_push_assumed_sets_renders_dash_when_empty():
    app = CopilotApp(bot_username="KBotSpec")
    async with app.run_test() as pilot:
        app.push_assumed_sets({})
        await pilot.pause()
        panel = app.query_one("#assumed-sets")
        rendered = str(panel.renderable) if hasattr(panel, "renderable") else str(panel.render())
        assert "—" in rendered


async def test_push_turn_event_writes_to_log():
    app = CopilotApp(bot_username="KBotSpec")
    async with app.run_test() as pilot:
        app.push_turn_event("turn 5: Kowtow Cleave hits")
        await pilot.pause()
        log = app.query_one("#turn-log", Log)
        # Textual's Log widget exposes .lines — a list of rendered strings
        lines = getattr(log, "lines", None) or []
        assert any("Kowtow Cleave" in str(line) for line in lines)
