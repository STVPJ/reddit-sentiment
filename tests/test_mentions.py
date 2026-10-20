import pytest


@pytest.mark.parametrize("text, want", [
    ("Copilot keeps suggesting deprecated APIs", ["GitHub Copilot"]),
    ("cursor was fine for refactoring", ["Cursor"]),
    ("we use Claude Code and Codex CLI", ["Claude Code", "OpenAI Codex"]),
    ("Codeium rebranded as windsurf", ["Windsurf"]),
    ("cursors, codexes and copilots are words", []),          # word boundaries
    ("I asked Claude in the browser", []),                   # general chat AI, not a product
])
def test_find_mentions(cfg, text, want):
    assert sorted(cfg.find_mentions(text)) == sorted(want)
