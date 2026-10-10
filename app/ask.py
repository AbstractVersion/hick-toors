import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from app.config import BASE_DIR, Settings
from app.models import Hike, Stay

UNAVAILABLE = "Ask Claude unavailable – run `claude` once to log in"
BUSY = "Claude is still answering the previous question – please wait"
FENCED_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
FENCED_BLOCK_RE = re.compile(r"```json.*?```", re.S)
LOGIN_RE = re.compile(r"log ?in|auth|credential|api key", re.I)

SYSTEM_PROMPT = """You are the trip planner inside hick-toors, a local site for finding hikes in Greece
plus a place to stay, usually travelling with a dog. Home is {home}.
Use the hicktoors tools (search_hikes, search_stays, plan_trip) to find real options; never invent
hikes, stays, prices or coordinates. Dates are YYYY-MM-DD.
For questions about the user's saved trips, use list_saved_trips and get_saved_trip (read-only; you
cannot change trips).
When the user gives an Airbnb room link, use lookup_airbnb (with the trip dates) to get that listing.
Finish with a short friendly plan, then a final fenced JSON block exactly like:
```json
{{"summary": "<2-5 sentence plan>", "hike_ids": ["wikiloc:..."], "stay_ids": ["airbnb:..."],
 "combos": [{{"hike_id": "wikiloc:...", "stay_id": "airbnb:...", "note": "<why>"}}],
 "warnings": ["<any warnings returned by the tools, verbatim>"]}}
```
Only use ids returned by the tools. Copy any warnings the tools returned into the "warnings" list."""

Run = Callable[[list[str]], Awaitable[tuple[int, str, str]]]


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    context: dict = {}


class AskCombo(BaseModel):
    hike: Hike
    stay: Stay
    note: str = ""


class AskResult(BaseModel):
    summary: str = ""
    hikes: list[Hike] = []
    stays: list[Stay] = []
    combos: list[AskCombo] = []
    warnings: list[str] = []


def extract_json(text: str) -> dict | None:
    candidates = FENCED_JSON_RE.findall(text)[::-1]
    if "{" in text:
        candidates.append(text[text.find("{"): text.rfind("}") + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


class AskRunner:
    def __init__(self, settings: Settings, lookup: Callable[[str], Hike | Stay | None], run: Run | None = None):
        self.settings = settings
        self._lookup = lookup
        self._run = run or self._run_subprocess
        self._lock = asyncio.Lock()

    def available(self) -> bool:
        return shutil.which(self.settings.claude_bin) is not None

    def write_mcp_config(self) -> Path:
        path = Path(self.settings.cache_dir) / "mcp.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        config = {"mcpServers": {"hicktoors": {
            "command": shutil.which("uv") or "uv",
            "args": ["run", "--directory", str(BASE_DIR), "python", "-m", "app.mcp_tools"],
        }}}
        path.write_text(json.dumps(config, indent=2))
        return path

    def build_prompt(self, question: str, context: dict) -> str:
        return f"{question}\n\nContext from the search form: {json.dumps(context, ensure_ascii=False)}"

    def build_command(self, prompt: str, mcp_config: Path) -> list[str]:
        return [
            shutil.which(self.settings.claude_bin) or self.settings.claude_bin,
            "-p",
            "--mcp-config", str(mcp_config),
            "--strict-mcp-config",
            "--allowedTools", "mcp__hicktoors",
            "--tools", "",
            "--append-system-prompt", SYSTEM_PROMPT.format(home=self.settings.home_name),
            "--output-format", "json",
            "--", prompt,
        ]

    async def _run_subprocess(self, cmd: list[str]) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(BASE_DIR))
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.settings.ask_timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")

    def parse_output(self, code: int, out: str, err: str) -> AskResult:
        try:
            envelope = json.loads(out)
        except ValueError:
            envelope = None
        if code != 0 or not isinstance(envelope, dict) or envelope.get("is_error"):
            text = ((envelope or {}).get("result") if isinstance(envelope, dict) else None) or err or out
            if LOGIN_RE.search(text or ""):
                return AskResult(warnings=[UNAVAILABLE])
            return AskResult(warnings=[f"Claude couldn't answer: {(text or 'no output').strip()[:200]}"])
        text = envelope.get("result") or ""
        payload = extract_json(text)
        if payload is None:
            return AskResult(summary=text.strip(), warnings=["Claude's answer had no map data"])
        missing = 0

        def resolve(obj_id, kind):
            nonlocal missing
            obj = self._lookup(str(obj_id))
            if isinstance(obj, kind):
                return obj
            missing += 1
            return None

        hikes = [h for h in (resolve(i, Hike) for i in payload.get("hike_ids") or []) if h]
        stays = [s for s in (resolve(i, Stay) for i in payload.get("stay_ids") or []) if s]
        combos = []
        for c in payload.get("combos") or []:
            if not isinstance(c, dict):
                continue
            hike, stay = resolve(c.get("hike_id"), Hike), resolve(c.get("stay_id"), Stay)
            if hike and stay:
                combos.append(AskCombo(hike=hike, stay=stay, note=str(c.get("note") or "")))
        warnings = []
        for w in [str(w)[:200] for w in payload.get("warnings") or []][:5]:
            if w not in warnings:
                warnings.append(w)
        if missing:
            warnings.append(f"{missing} result(s) from Claude could not be found and were skipped")
        summary = payload.get("summary") or FENCED_BLOCK_RE.sub("", text)
        return AskResult(summary=str(summary).strip(), hikes=hikes, stays=stays,
                         combos=combos, warnings=warnings)

    async def ask(self, req: AskRequest) -> AskResult:
        if not self.available():
            return AskResult(warnings=[UNAVAILABLE])
        if self._lock.locked():
            return AskResult(warnings=[BUSY])
        async with self._lock:
            cmd = self.build_command(self.build_prompt(req.question, req.context), self.write_mcp_config())
            try:
                code, out, err = await self._run(cmd)
            except asyncio.TimeoutError:
                return AskResult(warnings=[
                    f"Claude took longer than {self.settings.ask_timeout_seconds} s – try a simpler question"])
            except FileNotFoundError:
                return AskResult(warnings=[UNAVAILABLE])
            return self.parse_output(code, out, err)
