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
    start = text.index(gss.BEGIN_MARKER) + len(gss.BEGIN_MARKER)
    end = text.index(gss.END_MARKER, start)
    return text[start:end].strip("\n") + "\n"


def test_find_enabled_flags_is_a_pure_recursive_walk():
    cfg = {
        "a": {"enabled": True, "nested": {"enabled": False}},
        "b": {"foo_enabled": True},
        "c": "not a dict",
        "d": [1, 2, 3],  # lists are not walked into -- no flags live there today
    }
    assert gss.find_enabled_flags(cfg) == [
        ("a.enabled", True),
        ("a.nested.enabled", False),
        ("b.foo_enabled", True),
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
