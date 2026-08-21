"""Tests for aicode.tools — the file/shell tool implementations."""

from aicode import tools
from aicode.tools import TOOL_SPECS, ToolRunner


def make_runner(tmp_path, **kwargs):
    return ToolRunner(workdir=str(tmp_path), **kwargs)


def test_write_then_read_roundtrip(tmp_path):
    runner = make_runner(tmp_path)
    result = runner.tool_write_file("sub/dir/hello.py", 'print("hi")\n')
    assert "Wrote" in result
    assert (tmp_path / "sub" / "dir" / "hello.py").exists()
    assert runner.tool_read_file("sub/dir/hello.py") == 'print("hi")\n'


def test_read_missing_file(tmp_path):
    runner = make_runner(tmp_path)
    assert runner.tool_read_file("nope.txt").startswith("Error: cannot read")


def test_paths_resolve_relative_to_workdir(tmp_path):
    runner = make_runner(tmp_path)
    runner.tool_write_file("a.txt", "A")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("OUT", encoding="utf-8")
    assert runner.tool_read_file(str(outside)) == "OUT"  # absolute paths allowed
    assert runner.tool_read_file("a.txt") == "A"


def test_list_dir_marks_directories(tmp_path):
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    runner = make_runner(tmp_path)
    out = runner.tool_list_dir(".")
    lines = out.splitlines()
    assert "file.txt" in lines
    assert "folder/" in lines
    assert lines[0] < lines[1]  # sorted


def test_list_dir_missing(tmp_path):
    runner = make_runner(tmp_path)
    assert runner.tool_list_dir("missing").startswith("Error:")


def test_run_command_captures_output(tmp_path):
    runner = make_runner(tmp_path)
    out = runner.tool_run_command("echo hello && ls")
    assert "exit_code=0" in out
    assert "hello" in out


def test_run_command_reports_failure(tmp_path):
    runner = make_runner(tmp_path)
    out = runner.tool_run_command("false")
    assert "exit_code=1" in out


def test_run_command_timeout(tmp_path):
    runner = make_runner(tmp_path, cmd_timeout=1)
    out = runner.tool_run_command("sleep 5")
    assert "timed out" in out


def test_write_refuses_oversized_content(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "WRITE_MAX_BYTES", 10)
    runner = make_runner(tmp_path)
    out = runner.tool_write_file("big.txt", "x" * 20)
    assert "refused" in out
    assert not (tmp_path / "big.txt").exists()


def test_read_truncates_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "READ_MAX_BYTES", 8)
    (tmp_path / "big.txt").write_text("abcdefghijklmnop", encoding="utf-8")
    runner = make_runner(tmp_path)
    out = runner.tool_read_file("big.txt")
    assert "truncated" in out
    assert "total 16" in out


def test_dispatch_unknown_tool(tmp_path):
    runner = make_runner(tmp_path)
    assert "unknown tool" in runner.dispatch("nope", {})


def test_dispatch_invalid_arguments(tmp_path):
    runner = make_runner(tmp_path)
    assert "invalid arguments" in runner.dispatch("read_file", {"path": 1})


def test_dispatch_dry_run_does_nothing(tmp_path):
    runner = make_runner(tmp_path, dry_run=True)
    out = runner.dispatch("write_file", {"path": "x.txt", "content": "X"})
    assert out.startswith("Dry-run:")
    assert not (tmp_path / "x.txt").exists()


def test_tool_specs_cover_all_handlers(tmp_path):
    runner = make_runner(tmp_path)
    names = {spec["function"]["name"] for spec in TOOL_SPECS}
    assert names == {"read_file", "write_file", "list_dir", "run_command"}
    for spec in TOOL_SPECS:
        assert spec["type"] == "function"
        assert "parameters" in spec["function"]
    for name in names:
        assert hasattr(runner, "tool_" + name)


def test_dispatch_salvages_malformed_json_arguments(tmp_path):
    """Literal newlines inside a long content value break json.loads;
    dispatch must recover path/content via tolerant extraction."""
    raw = '{\n"path": "game.py",\n"content": "print(\'hi\')\\nprint(\'你好，世界\')\\n"\n}'
    runner = make_runner(tmp_path)
    result = runner.dispatch("write_file", {"_raw": raw})
    assert "Wrote" in result
    written = (tmp_path / "game.py").read_text(encoding="utf-8")
    assert written == "print('hi')\nprint('你好，世界')\n"


def test_salvage_ignores_non_string_values(tmp_path):
    """Numbers/bools outside quotes are skipped; strings are recovered."""
    raw = '{"max_len": 500, "path": "a.txt", "content": "ok"}'
    runner = make_runner(tmp_path)
    runner.dispatch("write_file", {"_raw": raw})
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "ok"


def test_dispatch_raw_unparseable_returns_error(tmp_path):
    runner = make_runner(tmp_path)
    result = runner.dispatch("write_file", {"_raw": "no key-value pairs at all"})
    assert "could not parse tool arguments" in result


def test_salvage_unescape_handles_cjk_and_escapes():
    raw = '{"content": "line1\\nline2 \\"quoted\\" \\u4e2d\\u6587"}'
    out = tools.salvage_arguments(raw)
    assert out["content"] == 'line1\nline2 "quoted" 中文'
