"""Tests for aicode.agent — the tool-use loop with a scripted client."""

from aicode.agent import Agent, AgentSession, assistant_message
from aicode.tools import ToolRunner
from conftest import FakeClient, tool_response


def make_agent(tmp_path, script, **kwargs):
    client = FakeClient(script)
    runner = ToolRunner(workdir=str(tmp_path))
    agent = Agent(client, runner, workdir=str(tmp_path), **kwargs)
    return agent, client


def test_assistant_message_roundtrip_shape():
    resp = {
        "content": "let me check",
        "tool_calls": [{"id": "c1", "name": "list_dir", "arguments": {"path": "."}}],
    }
    msg = assistant_message(resp)
    assert msg["role"] == "assistant"
    assert msg["tool_calls"][0]["type"] == "function"
    assert msg["tool_calls"][0]["function"]["name"] == "list_dir"
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"path": "."}'


def test_task_that_writes_file_end_to_end(tmp_path, capsys):
    script = [
        tool_response(
            "write_file",
            {"path": "hello.py", "content": 'print("hi from agent")\n'},
            call_id="c1",
        ),
        {"content": "Done. Created hello.py.", "finish_reason": "stop"},
    ]
    agent, client = make_agent(tmp_path, script)
    final = agent.run_task("create hello.py")

    assert final == "Done. Created hello.py."
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == 'print("hi from agent")\n'

    # The second model call must carry the assistant tool_calls + tool result.
    second_call = client.calls[1]["messages"]
    roles = [m["role"] for m in second_call]
    assert roles == ["system", "user", "assistant", "tool"]
    tool_msg = second_call[3]
    assert tool_msg["tool_call_id"] == "c1"
    assert "Wrote" in tool_msg["content"]
    # Progress line was printed.
    assert "write_file" in capsys.readouterr().out


def test_run_command_tool_roundtrip(tmp_path):
    script = [
        tool_response("run_command", {"command": "echo ok"}, call_id="c2"),
        {"content": "The command succeeded.", "finish_reason": "stop"},
    ]
    agent, client = make_agent(tmp_path, script)
    agent.run_task("run echo ok")
    tool_msg = client.calls[1]["messages"][3]
    assert "exit_code=0" in tool_msg["content"]
    assert "ok" in tool_msg["content"]


def test_dry_run_never_executes(tmp_path):
    script = [
        tool_response("write_file", {"path": "x.txt", "content": "X"}, call_id="c1"),
        {"content": "done", "finish_reason": "stop"},
    ]
    client = FakeClient(script)
    runner = ToolRunner(workdir=str(tmp_path), dry_run=True)
    agent = Agent(client, runner, workdir=str(tmp_path))
    agent.run_task("write x.txt")
    assert not (tmp_path / "x.txt").exists()
    assert client.calls[1]["messages"][3]["content"].startswith("Dry-run:")


def test_iteration_budget_forces_tool_free_final_call(tmp_path):
    script = [
        tool_response("write_file", {"path": "a.txt", "content": "a"}, call_id="c1"),
        tool_response("write_file", {"path": "b.txt", "content": "b"}, call_id="c2"),
        {"content": "Summary: wrote a.txt and b.txt.", "finish_reason": "stop"},
    ]
    agent, client = make_agent(tmp_path, script, max_iters=2)
    final = agent.run_task("write two files")
    assert final == "Summary: wrote a.txt and b.txt."
    assert client.calls[2]["tools"] is None
    assert "budget exhausted" in client.calls[2]["messages"][-1]["content"]


def test_unknown_tool_call_becomes_error_result(tmp_path):
    script = [
        tool_response("teleport", {"where": "mars"}, call_id="c1"),
        {"content": "ok", "finish_reason": "stop"},
    ]
    agent, client = make_agent(tmp_path, script)
    agent.run_task("teleport")
    assert "unknown tool" in client.calls[1]["messages"][3]["content"]


def test_reasoning_display_flag(tmp_path, capsys):
    script = [{"content": "done", "reasoning": "I should check first", "finish_reason": "stop"}]
    agent, _ = make_agent(tmp_path, script, show_reasoning=True)
    agent.run_task("x")
    out = capsys.readouterr().out
    assert "I should check first" in out


def test_reasoning_hidden_by_default(tmp_path, capsys):
    script = [{"content": "done", "reasoning": "secret thought", "finish_reason": "stop"}]
    agent, _ = make_agent(tmp_path, script)
    agent.run_task("x")
    assert "secret thought" not in capsys.readouterr().out


def test_agent_forwards_on_content_callback(tmp_path):
    """Streaming display hook must reach the client and receive incremental pieces."""

    class StreamingFake(FakeClient):
        def chat(self, messages, tools=None, **kwargs):
            on_content = kwargs.get("on_content")
            if on_content:
                on_content("streamed-")
                on_content("answer")
            return super().chat(messages, tools=tools, **kwargs)

    script = [{"content": "streamed-answer", "finish_reason": "stop"}]
    agent = Agent(StreamingFake(script), ToolRunner(workdir=str(tmp_path)), workdir=str(tmp_path))
    pieces = []
    agent.on_content = pieces.append
    final = agent.run_task("x")
    assert final == "streamed-answer"
    assert pieces == ["streamed-", "answer"]


def test_session_keeps_history_across_turns(tmp_path):
    script = [
        {"content": "first answer", "finish_reason": "stop"},
        {"content": "second answer", "finish_reason": "stop"},
    ]
    agent, client = make_agent(tmp_path, script)
    session = AgentSession(agent)
    assert session.turn("q1") == "first answer"
    assert session.turn("q2") == "second answer"

    second_call = client.calls[1]["messages"]
    assert [m["role"] for m in second_call] == ["system", "user", "assistant", "user"]
    assert second_call[1]["content"] == "q1"
    assert second_call[3]["content"] == "q2"
