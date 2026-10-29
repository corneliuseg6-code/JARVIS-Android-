# JARVIS V4 — instructions for Claude Code

A local-first JARVIS assistant for Android: Python backend in Termux, PWA in
Chrome. Read this first. Detail lives in `JARVIS_PROJECT_STATUS.md` (state),
`LIMITATIONS.md` (what is and is not verified), `CHANGES.md` (history) and
`SETUP-V4-BRAIN.md` (AI providers, Claude Code, OpenArt).

## Protect stable versions

- **Never modify `main` or `jarvis-v3-backup`.** Work on the assigned branch.
  Commit and push there. Open a PR only when asked.
- Never rewrite pushed history or force-push someone else's branch.
- Preserve the interface unless the task names the change: console band,
  RINGS and FACE, particles, edge pulse, shortcuts, amber clock, chat panel,
  folder network, voices, Workspace.

## Architecture

```
server/   Python 3 standard library ONLY (Termux has no compiler for wheels)
  app.py        tiny HTTP router + SSE          api.py      every route; chat stream
  llm.py        provider router + routing      usage.py    spend ledger + limits
  ollama.py / openai_client.py / anthropic_client.py / gemini_client.py
  toolchat.py   MCP tool loop, approvals       armory.py   MCP connections
  mcp_client.py stdio + streamable HTTP        oauth.py    MCP sign-in (OAuth 2.1 + PKCE)
  claude_code.py Claude Code CLI bridge        media.py    tool media → workspace
  workspace.py, voices.py, config.py, paths.py, conversations.py, vision.py, store.py
web/      plain ES modules, no bundler. js/app.js wires everything.
  js/ui/connections.js  AI Connections panel   js/ui/toolsmenu.js  AI TOOLS menu
  js/i18n/  six languages; every key in all six
tools/    MCP servers, mocks (mock_cloud.py, mock_ollama.py) and test suites
```

- User data lives under `~/.jarvis` (`JARVIS_HOME`), never in the repo:
  `config.json`, `secrets.json` (mode 600), `usage.json`, `workspace/`.
- All four brains (Ollama, OpenAI, Claude, Gemini) share one shape: they
  yield `{"type":"token"}` events, then one `{"type":"message", usage}`.
  Tool calls are in the OpenAI/Ollama format. `llm.route()` picks the
  candidates; `api.post_chat` runs them with fallback.

## Decisions that stand

- **Ollama is the free default.** A paid brain needs four things: switched on,
  cost notice acknowledged, its own API key, and spend under budget.
  `config._clamp_v21` enforces the first two even against a hand-edited
  `config.json`.
- **A subscription is not API access** (ChatGPT, Claude, Gemini apps). Never
  assume otherwise in code, UI or docs. Claude Code is the exception: it
  may use its own subscription sign-in.
- **Keys and tokens never reach the browser.** There is no GET for them.
  Tests check every public response.
- **Never claim something ran unless it ran.** Tool results carry what
  actually happened. A queued Claude Code task says "NOT RUN".
- **Approval gates are per call:**
  - writing tools, and anything on a `billed` connection (OpenArt), wait
    for approval; spending also needs `confirm_spend`;
  - Claude Code runs only through `approve()`;
  - `--dangerously-skip-permissions` is never passed.
- Official integrations only: each provider's documented REST API, the
  official `claude` CLI, OpenArt's hosted MCP server. No scraping, no
  unofficial wrappers.
- **Status is real, and checking it is free.** Do not probe a paid brain
  that is not in use; list models (unbilled) only when the user presses Test.

## Coding rules

- Standard library only on the server. No pip installs in instructions
  unless optional, and they must be marked optional.
- Match the surrounding style and comment density. Comments explain *why*.
- Every user-visible string is an i18n key, added to **all six**
  `web/js/i18n/*.js`. Key counts must match.
- Bump `CACHE` in `web/sw.js` whenever `web/` changes, and add new JS files
  to `SHELL`.
- New CSS uses new classes. Do not override existing rules unless the task
  names them (see the corrections log for why).
- Keep diffs minimal and in scope.

## Minimise API costs

- Test against the mocks (`tools/mock_cloud.py` speaks the OpenAI,
  Anthropic, Gemini, Ollama and MCP+OAuth formats). Never against paid APIs
  by default.
- A real paid call (e.g. a Claude Code run) needs the cheapest model, a
  `--max-budget-usd` cap, the smallest prompt, and must be justified. Report
  what it cost.
- Free checks first: a fake key or a non-existent model returns an error at
  $0 and still proves the endpoint and the flags.

## Test procedure (run before every commit)

```bash
for t in tools/test_*.py; do python3 $t | tail -1; done   # all must pass
python3 tools/bench_chat.py        # TTFT should stay ≈0.35 s against the stub
python3 tools/selftest.py          # with a server running on :8787
```

Browser checks use Playwright (pre-installed in the cloud container), at
**412×915** with a mobile UA, against `python3 -m server.main --port N` plus
`tools/mock_cloud.py`. Assert these invariants:

- dial `#stage` is 275×275 at (54,103);
- `#avatar-switch` is 275 px wide;
- four shortcuts;
- `scrollHeight ≤ 915` and `scrollWidth ≤ 412`;
- the status pill is not truncated;
- no JS errors;
- no raw i18n keys in any of the six languages.

Also check 360×800. Compare geometry against the previous commit with a
`git worktree`.

## Definition of done

1. The results are verified by running them; untested means not done.
2. Limitations are recorded in `LIMITATIONS.md`, saying what was *not*
   verified and why.
3. `JARVIS_PROJECT_STATUS.md` (and `CHANGES.md`) are updated.
4. All suites pass, then commit and push to the working branch.
5. Report honestly: what works, what was not tested, any money spent.

## Android setup (short)

```bash
pkg install python git
git clone https://github.com/corneliuseg6-code/JARVIS-Android-.git ~/jarvis-v3
cd ~/jarvis-v3 && git checkout <branch> && ./start.sh   # open http://127.0.0.1:8787
# optional: pkg install ollama && ollama serve & && ollama pull llama3.2:3b
# optional voice: pip install sherpa-onnx==1.13.8 && pkg install espeak-ng
```

- After an update, close the installed PWA completely, or it serves the old
  cache.
- Claude Code needs a Linux userland on Android (`proot-distro`); see
  `SETUP-V4-BRAIN.md`.
- Target device: Galaxy S25 Ultra, 412×915 CSS px.

## Corrections log — confirmed mistakes, do not repeat

Add a line whenever a correction is confirmed (by the user or by a test).

- `desynchronized: true` on a 2D canvas paints an opaque black box on Android
  Chrome. Never use it.
- CSS custom properties resolve where they are declared: `--dial` and
  `--rail-h` live on `.app`, not `:root`.
- `transition` is not additive; a later rule replaces the whole list.
- A canvas in a hidden panel measures 0×0: use a `ResizeObserver`.
- Never silently fall back to the device voice. The user must choose it.
- Never assume a subscription includes API access.
- A shared `stop()` must not cancel unrelated pollers (an OpenArt sign-in
  poll was killed by the Claude Code request poller).
- Wait for the *final* state, not an intermediate one: after OAuth sign-in,
  wait for the connection to settle.
- Test stubs for CLIs must answer capability probes (`--help`); otherwise
  they run as if given a task. Run probes from a temp dir.
- `pkill -f <pattern>` can match your own shell. Use
  `pgrep -f "^python3 …" | xargs kill` instead.
- Config clamps USD limits to cents, so tests must use values ≥ $0.01.
- `http.server` header names keep the sender's capitalisation; compare them
  case-insensitively.
