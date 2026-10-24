"""scripts/gen_stream_status.py's output must match the table committed in
CLAUDE.md between the stream-status markers -- see tests/test_sitemap.py for
the identical "generated artifact pinned against its source" pattern."""
import importlib.util
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "gen_stream_status", os.path.join(_REPO, "scripts", "gen_stream_status.py"))
gss = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gss)


def _committed_table() -> str:
    with open(os.path.join(_REPO, "CLAUDE.md"), encoding="utf-8") as f:
        text = f.read()
    assert text.count(gss.BEGIN_MARKER) == 1, "stream-status begin marker must appear exactly once"
    assert text.count(gss.END_MARKER) == 1, "stream-status end marker must appear exactly once"
    start = text.index(gss.BEGIN_MARKER) + len(gss.BEGIN_MARKER)
    end = text.index(gss.END_MARKER, start)
    assert end > start, "stream-status end marker must come after the begin marker"
    return text[start:end].strip("\n") + "\n"


def test_find_enabled_flags_is_a_pure_recursive_walk():
    cfg = {
        "a": {"enabled": True, "nested": {"enabled": False}},
        "b": {"foo_enabled": True},
        "c": "not a dict",
        "d": [1, 2, 3],  # non-dict list items contribute no flags
        # a future per-item flag inside a list -- must not be silently skipped
        "e": [{"enabled": True}, {"other": 1}, {"enabled": False}],
    }
    assert gss.find_enabled_flags(cfg) == [
        ("a.enabled", True),
        ("a.nested.enabled", False),
        ("b.foo_enabled", True),
        ("e[0].enabled", True),
        ("e[2].enabled", False),
    ]


def test_build_table_shape():
    table = gss.build_table({"x": {"enabled": True}, "y": {"z_enabled": False}})
    assert table == (
        "| Config flag | Live |\n"
        "| --- | --- |\n"
        "| `x.enabled` | yes |\n"
        "| `y.z_enabled` | no |\n"
    )


def test_claude_md_stream_status_table_matches_generator():
    """Adding/flipping/removing an `enabled` flag in config.yml without
    regenerating this table fails here -- pytest does not print this
    docstring on failure, so the assert message below repeats the fix."""
    generated = gss.build_table(gss.load_config()).strip("\n") + "\n"
    assert _committed_table() == generated, (
        "CLAUDE.md's stream-status table is stale -- run: "
        "python3 scripts/gen_stream_status.py and paste the output between "
        "CLAUDE.md's <!-- stream-status:begin/end --> markers")
