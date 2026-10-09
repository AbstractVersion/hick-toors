import asyncio
import json
import sys

import pytest

from app.ask import UNAVAILABLE, AskRequest, AskRunner, extract_json
from app.config import Settings
from app.models import Hike, Stay

HIKE = Hike(id="wikiloc:1", name="Portaria loop", url="u", lat=39.39, lon=22.99)
STAY = Stay(id="airbnb:7", source="airbnb", name="Stone house", url="u", lat=39.40, lon=23.0)
OBJECTS = {HIKE.id: HIKE, STAY.id: STAY}


def envelope(text, is_error=False):
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": text})


ANSWER = """Here is a plan.
```json
{"summary": "Do the Portaria loop, stay at the stone house.", "hike_ids": ["wikiloc:1"],
 "stay_ids": ["airbnb:7", "airbnb:999"], "combos": [{"hike_id": "wikiloc:1", "stay_id": "airbnb:7", "note": "5 min walk"}]}
```"""


@pytest.fixture
def settings(tmp_path):
    # sys.executable exists on PATH lookups, so available() is True
    return Settings(_env_file=None, cache_dir=tmp_path, claude_bin=sys.executable)


def runner(settings, run=None):
    return AskRunner(settings, lookup=OBJECTS.get, run=run)


def test_build_command_flags(settings):
    r = runner(settings)
    path = r.write_mcp_config()
    cmd = r.build_command("hello", path)
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-p", "hello"]
    assert cmd[cmd.index("--mcp-config") + 1] == str(path)
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__hicktoors"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--append-system-prompt" in cmd
    config = json.loads(path.read_text())
    server = config["mcpServers"]["hicktoors"]
    assert server["args"][-2:] == ["-m", "app.mcp_tools"]


def test_build_prompt_includes_context(settings):
    prompt = runner(settings).build_prompt("river hike", {"start": "2026-10-23", "dog": True})
    assert "river hike" in prompt and '"start": "2026-10-23"' in prompt


def test_extract_json_prefers_last_fenced_block():
    assert extract_json(ANSWER)["hike_ids"] == ["wikiloc:1"]
    assert extract_json('text {"summary": "x"} more') == {"summary": "x"}
    assert extract_json("no json here") is None


def test_parse_output_resolves_ids_and_warns_on_unknown(settings):
    result = runner(settings).parse_output(0, envelope(ANSWER), "")
    assert result.summary.startswith("Do the Portaria loop")
    assert [h.id for h in result.hikes] == ["wikiloc:1"]
    assert [s.id for s in result.stays] == ["airbnb:7"]
    assert result.combos[0].note == "5 min walk" and result.combos[0].stay.id == "airbnb:7"
    assert result.warnings == ["1 result(s) from Claude could not be found and were skipped"]


def test_parse_output_without_json_keeps_text(settings):
    result = runner(settings).parse_output(0, envelope("Try Pelion this weekend."), "")
    assert result.summary == "Try Pelion this weekend."
    assert result.warnings == ["Claude's answer had no map data"]


def test_parse_output_login_error(settings):
    result = runner(settings).parse_output(1, envelope("Invalid API key · Please run /login", True), "")
    assert result.warnings == [UNAVAILABLE]


def test_parse_output_other_error(settings):
    result = runner(settings).parse_output(1, "", "boom")
    assert result.warnings == ["Claude couldn't answer: boom"]


async def test_ask_happy_path(settings):
    seen = []

    async def run(cmd):
        seen.append(cmd)
        return 0, envelope(ANSWER), ""

    result = await runner(settings, run).ask(AskRequest(question="weekend hike", context={}))
    assert result.hikes[0].id == "wikiloc:1"
    assert seen[0][2].startswith("weekend hike")


async def test_ask_timeout(settings):
    async def run(cmd):
        raise asyncio.TimeoutError

    result = await runner(settings, run).ask(AskRequest(question="weekend hike"))
    assert result.warnings == ["Claude took longer than 120 s – try a simpler question"]


async def test_ask_when_cli_missing(tmp_path):
    settings = Settings(_env_file=None, cache_dir=tmp_path, claude_bin="definitely-not-installed-xyz")
    r = AskRunner(settings, lookup=OBJECTS.get)
    assert r.available() is False
    result = await r.ask(AskRequest(question="weekend hike"))
    assert result.warnings == [UNAVAILABLE]


async def test_ask_rejects_concurrent_runs(settings):
    gate = asyncio.Event()

    async def run(cmd):
        await gate.wait()
        return 0, envelope(ANSWER), ""

    r = runner(settings, run)
    first = asyncio.create_task(r.ask(AskRequest(question="weekend hike")))
    await asyncio.sleep(0)
    second = await r.ask(AskRequest(question="another one"))
    gate.set()
    await first
    assert second.warnings == ["Claude is still answering the previous question – please wait"]
