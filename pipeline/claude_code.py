"""The script step through headless Claude Code (user 2026-11-17: "SCRIPT_PROVIDER=claude-code, which runs the script
step (core answer, outline, whole-script writing, judge) through headless Claude Code (`claude -p`) on the Mac, using
my existing Claude subscription login. No API key.").

    ask(system, user, json_schema=None)   -> (text, usage)    one `claude -p` call
    preflight()                           -> '' or why not    logged in with a subscription?

Every call runs `claude -p` with the subscription's login: the environment it gets has no ANTHROPIC_API_KEY (the
repo's .env sets one; with it the CLI would bill the API), no tools, no MCP servers, no settings, hooks or plugins,
no saved session, and an empty working folder (no CLAUDE.md). Structured output is the CLI's --json-schema.

A missing login or a usage limit raises Unavailable: the script step then falls back to the local 9B pipeline and
says so (pipeline/script.py). Any other failure is an ordinary LLMError for the caller, as a failed Ollama call is.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config

TIMEOUT_S = 300
# the variables the CLI must not see: an API key or token would bill the API instead of the subscription, and the
# variables of a Claude Code session this one runs inside would make it a child of that session
STRIP = re.compile(r'^(ANTHROPIC_|CLAUDE_CODE_|CLAUDECODE$|CLAUDE_PID$|CLAUDE_EFFORT$|CLAUDE_PLUGIN|CLAUDE_AGENT_SDK)')
# what the CLI says when the subscription cannot be used (login, limit, plan): the 9B pipeline takes over
UNAVAILABLE = re.compile(r'(?i)usage limit|limit reached|hit your limit|rate.?limit|not logged in|please run /login|'
                         r'/login|invalid api key|authentication|unauthori[sz]ed|oauth|credit balance|quota')


class Unavailable(RuntimeError):
    """Claude Code cannot be used now (not logged in, a usage limit): fall back to the local pipeline."""


def binary() -> str:
    return (os.getenv('CLAUDE_CODE_BIN', '').strip() or shutil.which('claude')
            or str(Path.home() / '.local' / 'bin' / 'claude'))


def model() -> str:
    return config.claude_code_model()


def env(environ=None) -> dict:
    """The CLI's environment: the caller's, without any Anthropic key or Claude Code session variable."""
    src = os.environ if environ is None else environ
    out = {k: v for k, v in src.items() if not STRIP.match(k)}
    out.setdefault('HOME', str(Path.home()))
    path = out.get('PATH', '/usr/bin:/bin')
    bin_dir = str(Path(binary()).parent)
    if bin_dir not in path.split(':'):
        out['PATH'] = f'{bin_dir}:{path}'
    return out


def workdir() -> Path:
    d = Path(os.getenv('CLAUDE_CODE_WORKDIR', '').strip() or config.shared_cache_dir() / 'claude-code-cwd')
    d.mkdir(parents=True, exist_ok=True)
    return d


def command(system: str, json_schema: dict | None, mdl: str | None = None) -> list:
    cmd = [binary(), '-p', '--output-format', 'json', '--model', mdl or model(), '--system-prompt', system,
           '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--no-session-persistence',
           '--setting-sources', '', '--disable-slash-commands']
    if json_schema is not None:
        cmd += ['--json-schema', json.dumps(json_schema)]
    return cmd


def run(cmd, user, timeout):
    return subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=timeout, env=env(), cwd=workdir())


def ask(system: str, user: str, json_schema: dict | None = None, *, timeout: float = TIMEOUT_S, runner=None,
        mdl: str | None = None) -> tuple[str, dict]:
    """(answer text — JSON text when json_schema is given, usage {seconds, input, output, api_equivalent_usd, model}).
    Raises Unavailable (no login, a limit) or LLMError (anything else)."""
    from .llm import LLMError
    runner = runner or run
    t0 = time.time()
    try:
        p = runner(command(system, json_schema, mdl), user, timeout)
    except FileNotFoundError:
        raise Unavailable(f'the Claude Code CLI is not installed ({binary()})') from None
    except subprocess.TimeoutExpired:
        raise LLMError(f'Claude Code did not answer in {timeout:.0f} s') from None
    seconds = round(time.time() - t0, 2)
    out = (p.stdout or '').strip()
    try:
        data = json.loads(out.splitlines()[-1] if out else '')
    except ValueError:
        data = None
    if not isinstance(data, dict):
        msg = ' '.join(((p.stderr or '') + ' ' + out).split())[:300] or f'exit {p.returncode}'
        if UNAVAILABLE.search(msg):
            raise Unavailable(msg)
        raise LLMError(f'Claude Code gave no answer: {msg}')
    text = data.get('result') if isinstance(data.get('result'), str) else ''
    if data.get('is_error') or data.get('subtype') not in (None, 'success'):
        msg = ' '.join(f'{text} {data.get("api_error_status") or ""} {data.get("subtype") or ""}'.split())[:300]
        if UNAVAILABLE.search(msg) or data.get('api_error_status') in (401, 403, 429):
            raise Unavailable(msg or 'Claude Code refused the call')
        raise LLMError(f'Claude Code failed: {msg}')
    usage_in = data.get('usage') or {}
    used = list((data.get('modelUsage') or {}).keys())
    usage = dict(seconds=seconds, api_ms=data.get('duration_api_ms'),
                 input=int(usage_in.get('input_tokens') or 0) + int(usage_in.get('cache_read_input_tokens') or 0)
                 + int(usage_in.get('cache_creation_input_tokens') or 0),
                 output=int(usage_in.get('output_tokens') or 0),
                 api_equivalent_usd=data.get('total_cost_usd'), models=used)
    if json_schema is not None:
        so = data.get('structured_output')
        if so is None:                               # an answer in the text: the JSON in it, if any
            m = re.search(r'\{.*\}', text or '', re.S)
            if not m:
                raise LLMError('Claude Code gave no structured answer')
            try:
                so = json.loads(m.group(0))
            except ValueError:
                raise LLMError('Claude Code gave no structured answer') from None
        return json.dumps(so, ensure_ascii=False), usage
    return text or '', usage


def preflight(runner=None) -> str:
    """'' when the CLI is installed and logged in with a claude.ai subscription, else why not (no model call)."""
    try:
        p = (runner or (lambda: subprocess.run([binary(), 'auth', 'status'], capture_output=True, text=True,
                                                timeout=30, env=env(), cwd=workdir())))()
    except FileNotFoundError:
        return f'the Claude Code CLI is not installed ({binary()})'
    except subprocess.TimeoutExpired:
        return 'the Claude Code CLI did not answer `claude auth status`'
    try:
        data = json.loads(p.stdout or '')
    except ValueError:
        return 'Claude Code is not logged in (`claude auth status` gave no answer)'
    if not data.get('loggedIn'):
        return 'Claude Code is not logged in (run `claude` once and /login with the subscription)'
    if data.get('authMethod') not in ('claude.ai', 'oauth', 'subscription'):
        return f'Claude Code is logged in with {data.get("authMethod")}, not a claude.ai subscription'
    return ''
