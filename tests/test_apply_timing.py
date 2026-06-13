import json

from applypilot.apply.launcher import (
    PLAYWRIGHT_MCP_VERSION,
    _make_mcp_config,
    aggregate_timing_reports,
    load_timing_reports,
    summarize_stream_json_events,
)


def test_stream_json_timing_parser_pairs_tools_and_model_turns():
    records = [
        (
            0.2,
            json.dumps({
                "type": "assistant",
                "message": {
                    "content": [{
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "mcp__playwright__browser_navigate",
                        "input": {"url": "https://example.com/job"},
                    }]
                },
            }),
        ),
        (
            1.7,
            json.dumps({
                "type": "user",
                "message": {
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "ok",
                    }]
                },
            }),
        ),
        (
            2.0,
            json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "RESULT:APPLIED"}]},
            }),
        ),
        (
            2.1,
            json.dumps({
                "type": "result",
                "num_turns": 2,
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "result": "RESULT:APPLIED",
            }),
        ),
    ]

    summary = summarize_stream_json_events(
        records,
        spawn_ts=0.0,
        finished_ts=3.0,
        job_url="https://example.com/job",
        result="RESULT:APPLIED",
    )

    assert summary["job_url"] == "https://example.com/job"
    assert summary["total_s"] == 3.0
    assert summary["startup_s"] == 0.2
    assert summary["model_turns"]["count"] == 2
    assert summary["model_turns"]["total_s"] == 0.5
    assert summary["model_turns"]["tokens"]["input_tokens"] == 100
    assert summary["model_turns"]["tokens"]["output_tokens"] == 20
    assert summary["tool_calls"]["browser_navigate"] == {"count": 1, "total_s": 1.5}
    assert summary["top_slowest"][0]["name"] == "browser_navigate"
    assert summary["result"] == "RESULT:APPLIED"


def test_timing_report_aggregation_loads_fixture_files(tmp_path):
    first = {
        "job_url": "https://example.com/a",
        "total_s": 100,
        "startup_s": 5,
        "model_turns": {"count": 3, "total_s": 30},
        "tool_calls": {"browser_fill_form": {"count": 2, "total_s": 4}},
        "top_slowest": [],
        "result": "RESULT:APPLIED",
    }
    second = {
        "job_url": "https://example.com/b",
        "total_s": 200,
        "startup_s": 10,
        "model_turns": {"count": 2, "total_s": 20},
        "tool_calls": {"browser_fill_form": {"count": 1, "total_s": 5}},
        "top_slowest": [],
        "result": "RESULT:FAILED:stuck",
    }
    (tmp_path / "timing_20260101_000000_000000_w0.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "timing_20260101_000001_000000_w1.json").write_text(json.dumps(second), encoding="utf-8")

    reports = load_timing_reports(tmp_path)
    aggregate = aggregate_timing_reports(reports)

    assert aggregate["runs"] == 2
    assert aggregate["avg_total_s"] == 150
    assert aggregate["avg_startup_s"] == 7.5
    assert aggregate["avg_model_turn_s"] == 10
    assert aggregate["tool_calls"]["browser_fill_form"]["count"] == 3
    assert aggregate["tool_calls"]["browser_fill_form"]["total_s"] == 9
    assert aggregate["slowest_runs"][0]["job_url"] == "https://example.com/b"


def test_mcp_config_uses_pinned_playwright_mcp(monkeypatch):
    monkeypatch.delenv("APPLYPILOT_MCP_VERSION", raising=False)

    args = _make_mcp_config(9222)["mcpServers"]["playwright"]["args"]

    assert args[0] == "-y"
    assert args[1] == f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}"
    assert "@playwright/mcp@latest" not in args


def test_mcp_config_allows_version_override(monkeypatch):
    monkeypatch.setenv("APPLYPILOT_MCP_VERSION", "1.2.3")

    args = _make_mcp_config(9223)["mcpServers"]["playwright"]["args"]

    assert args[1] == "@playwright/mcp@1.2.3"
