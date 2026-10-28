# JARVIS V4 — implementation plan

Branch: `claude/v4-agent-system`, cut from `claude/web-research` (`1da88dc`).
`main` and `jarvis-v3-backup` are not touched by any of this.

## What the inspection found

Two of the ten requested sections are already written, tested and pushed —
they landed on `claude/web-research` in the two commits before this branch:

| Requested | State |
|---|---|
| §3 Repair MCP tool calling | **Done** (`ab15624`). Discovery, definitions to the model, execution through `armory`, results back, tool cards with real errors, approval gate on writes. Tested with JARVIS Files → `list_directory`; 27 checks. |
| §4 Internet research | **Done** (`1da88dc`). `web_search` / `wikipedia` / `fetch_url` free and keyless, `download_file` gated, SSRF guard, sources in the answer; 88 checks. No live search was possible from the build machine — see LIMITATIONS.md. |

So "the Ollama chat system does not appear to execute MCP tools" was true of
V3 and is fixed on the branch this one is cut from. Nothing in §3 is rebuilt.
Two gaps remain inside those sections and are carried into the phases below:

- **Only Ollama is handed tools.** `llm.supports_tools()` returns true for
  Ollama alone, so an OpenAI or Claude turn silently degrades to a
  tool-less conversation. Orchestration (§2, §10) cannot work until the paid
  providers can call tools too.
- **No multi-source comparison.** §4 asks JARVIS to compare sources; today it
  can read several pages but is not asked to reconcile them.

Three parts of §1 also already exist: OpenAI and Claude are real integrations
with keys held server-side in `~/.jarvis/secrets.json` (never sent to the
browser), billed providers are off until configured, and there is a cost
guard. Missing from §1: the **Cloud / Local / Automatic** mode switch, and
Gemini.

Genuinely new work: §2 Control Center, §5 Workspace, §6 Memory, §10 Command
Center, and §7 speed.

## Why local answers feel slow — three causes, found in the code

Not guesses; these are specific and each has a specific fix.

1. **With any MCP server connected, the answer is not streamed at all.**
   `toolchat.run()` calls `llm.chat_once_full()`, which is non-streaming,
   because the tool loop needs `tool_calls` intact. So the final answer is
   withheld until generation finishes. Connecting a tool server made chat
   feel dramatically slower, which is exactly when the user noticed it.
   *Fix: stream the final round. Buffer only while a tool call might still
   appear, then release tokens as they arrive.*

2. **Ollama is never told to keep the model loaded.** No `keep_alive` is
   sent, so Ollama unloads after ~5 minutes idle and the next message pays a
   multi-gigabyte reload before the first token. On a phone that is tens of
   seconds.
   *Fix: send `keep_alive`, configurable, default 30 minutes.*

3. **No request tuning and a fixed context window.** No `options` are sent
   (`num_ctx`, `num_predict`), and all 20 past messages are resent whatever
   the model. A 3B model on a phone re-reads that window every turn.
   *Fix: expose `num_ctx` / `num_predict` / `keep_alive` in settings with
   phone-sensible defaults, and trim the window by characters, not messages.*

Voice is not a cause: speech already starts at `onDone`, after the text is
complete, which is correct for a single TTS request.

**Measurement honesty:** there is no Ollama on the build machine (no binary,
no service, and the egress proxy blocks model downloads). So the *model's*
generation speed cannot be measured here. What can be measured, and will be,
is JARVIS's own overhead: time from the HTTP request to the first token
reaching the browser, against a stub that emits tokens on a fixed schedule.
That isolates the harness from the model and is the number these fixes
actually change. Real end-to-end timings have to come from the phone, and the
plan ships a timing readout so they can be read off there.

## Phases

Each phase ends with tests run and a commit. Nothing moves to the next phase
until the current one passes.

### Phase 1 — speed (and streaming through the tool loop)
- Stream the final round of the tool loop.
- `keep_alive`, `num_ctx`, `num_predict` in config and settings.
- Trim history by character budget.
- A timing readout: time-to-first-token and total, per turn, in the chat card.
- Harness-overhead measurements before and after, against a token stub.

### Phase 2 — the AI brain
- [x] `Cloud / Local / Automatic` routing mode (plus `Manual`, the default, so
  nothing changes until it is chosen). Automatic prefers local, falls to
  cloud only when a cloud provider is both configured and authorised.
- [x] Tools for OpenAI and Claude, so `supports_tools()` stops being
  Ollama-only. Switchable, because each tool round is a billed request.
- [ ] Gemini provider, same shape as the existing two, off until configured.
- [ ] Per-provider cost limits where the API reports usage.

### Phase 3 — AI Control Center / Command Center (§2 + §10 are one screen)
- A hierarchical dashboard: JARVIS as commander, providers and MCP servers
  beneath it, each with ON/OFF, real status, and an autonomy setting
  (autonomous / ask first).
- Honest labelling: a **model**, an **agent** and a **tool** are shown
  differently. An MCP connection is not called an autonomous agent.
- Delegation: JARVIS routes a task to an enabled agent, collects the result,
  answers once. Manual pick or automatic.
- Live "who is working on what", plus recent results and errors.

### Phase 4 — Workspace and long-term memory
- `~/.jarvis/workspace/` with Projects, Research, Documents, Notes, Tasks,
  Results, Work Journal. Real files; browsable, editable and deletable from
  the UI.
- SQLite memory at `~/.jarvis/memory.db`: project state, decisions,
  preferences, open and finished tasks, research notes, and what JARVIS was
  doing when the app closed.
- Relevant memories retrieved when answering.
- Workspace writes are autonomous; personal memories need approval before
  being stored permanently. Nothing local leaves the device unless the user
  sends it to a cloud provider.
- Export and backup of both.

### Phase 5 — research polish and integration tests
- Multi-source comparison with per-claim sourcing.
- End-to-end tests across brain, agents, workspace and memory.
- A final pass on the numbers from Phase 1.

## Standing constraints

- The V3 interface is preserved: console band, rings, face, particles,
  border, shortcuts, clock, voices. Every phase re-runs the layout checks
  (dial 275px, RINGS/FACE bar matching, no page scroll).
- Stdlib only. SQLite is in Python's standard library, so memory adds no
  dependency. No pip installs — Termux has no compiler for most wheels.
- Code stays separate from user data: everything the user owns is under
  `~/.jarvis`, never in the source tree.
- No feature is reported as working until it has been tested, and anything
  that could not be tested here is named in LIMITATIONS.md.

## Costs and credentials

Nothing in V4 requires payment to run. Ollama, the MCP servers, the web
research tools, SQLite and the workspace are all free and local. Paid
providers stay off until the user enters a key: OpenAI and Gemini need an API
key, Claude needs an Anthropic key, and each is billed per token by its
provider. Claude Code is CLI-gated and cannot run on Termux — already
documented and already offers no switch when unusable.
