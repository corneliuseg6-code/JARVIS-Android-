# V4 setup: the multi-AI brain, the Claude Code agent and OpenArt

Everything here is **off until you switch it on**. With nothing configured,
JARVIS answers with the free local model (Ollama), exactly as before.

All of it is configured in the app: ☰ → **AI Connections**.

---

## 0. Update the phone

```bash
cd ~/jarvis-v3 && ./stop.sh
git fetch origin ccr-0204aa78-wihp2o
git checkout ccr-0204aa78-wihp2o && git pull
./start.sh
```

Then **close the installed PWA completely** (swipe it away, not just
background it), or it keeps serving the old cached interface. The cache name
for this release is `jarvis-v4-8-brain`.

No new packages are needed. The server is still Python standard library only.

---

## 1. Subscriptions are not API access

A subscription to a chat app does not give an app like JARVIS API access.
Each API is a separate account with its own key and its own bill.

| You may have | It does **not** include | What JARVIS needs |
|---|---|---|
| ChatGPT Plus / Pro | the OpenAI API | an API key from platform.openai.com, with credit on the API account |
| Claude Pro / Max | the Anthropic API | an API key from console.anthropic.com, with credit |
| Gemini app (Google AI Pro / Ultra) | the Gemini API | a key from aistudio.google.com/apikey (free tier with limits, or billing enabled) |
| OpenArt plan | — | your OpenArt account, signed in through OpenArt's own page; generations spend its credits |
| Claude Pro / Max | — | **Claude Code** *can* use a subscription login (`claude auth login`). That is Claude Code's own sign-in, separate from the Anthropic API key above. |

The interface repeats this on every paid card.

---

## 2. The brains

For each of **OpenAI**, **Claude** and **Gemini**:

1. **Enable this connection.**
2. Read the cost notice and tick **I understand this connection costs money**.
3. Paste the **API key** and press **Save key**. It is written to
   `~/.jarvis/secrets.json` (mode 600) and is never sent back to the browser.
4. **Test.** This lists the provider's models and costs nothing. It shows
   the real state: connected, key rejected, out of credit, network down.
5. Optionally change the **Model**. The Test result lists what your key can use.

A brain becomes usable only when all four are true: switched on, notice
acknowledged, key stored, and under its spending limit.

### Routing — who answers

| Mode | Behaviour |
|---|---|
| **Manual** (default) | The brain you picked with **Use this brain**. |
| **Local only** | Ollama only. Nothing leaves the phone. |
| **Cloud only** | The first usable paid brain in your **Cloud preference** order. |
| **Automatic** | Short everyday turns stay on Ollama. Coding, long (>600 chars), analysis/research or image turns go to a paid brain (Claude first for code). If Ollama is offline, cloud is used. |

With **fallback** on (the default), a brain that fails *before* it says
anything is replaced by the next usable one: no key, out of credit, offline,
or over budget. The chat shows the switch and the reason. Nothing is retried
silently once an answer has started.

Every answer is signed with who answered, the model, and (for a paid brain)
the estimated cost of that turn.

### MCP tools on every brain

Tools from the **Tool Armory** (JARVIS Files, JARVIS Web, OpenArt, any MCP
server you add) are now offered to all four brains, not only Ollama. The
approval rules are unchanged: a tool that writes waits for your approval.

### Costs and limits

**Costs and limits** shows today's and this month's estimated spend per
provider and lets you set:

- a daily limit for all providers (default **$2**),
- a monthly limit for all providers (default **$20**),
- a daily limit per provider (default: none),
- the longest paid answer, in tokens (default 4096).

When a limit is reached, that brain is stopped **before** the next call is
made. Automatic and fallback routing then use the free local model. The
figures come from each provider's own token counts and list prices. They are
estimates; your provider's invoice is authoritative. A model with no known
price is charged at the most expensive known rate for its provider, so an
unknown model can only make the estimate too high.

---

## 3. Claude Code: develop, edit and repair, with approval

Claude Code is the official coding agent CLI. JARVIS runs it; it does not
reimplement it.

**On Android, Claude Code is not an officially supported platform.** The
published builds are glibc/musl Linux and Termux uses Bionic. It works when
`claude` runs inside a Linux userland, for example:

```bash
pkg install proot-distro
proot-distro install ubuntu
proot-distro login ubuntu
# inside Ubuntu:
apt update && apt install -y curl git
curl -fsSL https://claude.ai/install.sh | bash
claude auth login       # subscription sign-in, or set ANTHROPIC_API_KEY
```

JARVIS must be able to run `claude` from the shell that starts it. In
practice, run JARVIS inside the same Ubuntu userland, or put a wrapper named
`claude` on Termux's PATH that calls into it. The card reports exactly what
it finds:

- the version,
- whether it is signed in and how (from `claude auth status`),
- or why it cannot run.

Then, in **AI Connections → Claude Code**:

1. **Enable this connection.**
2. Set the **Working directory**, e.g. `~/projects`. Claude Code cannot reach
   outside it.
3. Optionally **Allow file edits**. Without it, every run is plan-only:
   read-only, with no shell.
4. Set the **Spending cap per run** (default $1, passed as
   `--max-budget-usd`) and the **Turn limit**.
5. Optionally grant **Extra permissions**, e.g. `Bash(npm test)`, so an edit
   run may run your tests. Empty means edits only, no shell. Anything else
   that would need permission is refused, never bypassed.

How it is used:

- **From the panel:** describe the task, choose *Plan only* or *May edit
  files*, optionally a project subfolder, then **Prepare request**. The exact
  command is shown and nothing runs until you press **Approve and run**.
- **From the chat:** ask JARVIS for real work on your code. It can only
  *queue* the task. A card appears with **Review and approve**, which opens
  the queue.
- **While it runs:** the steps it takes appear live, and **Stop** ends it.
- **After an edit run:** you see the files it changed and the full diff.
  Files you had already modified are listed separately and never claimed. In
  a git repository, **Revert this run** restores exactly the run's own
  changes and deletes files it created. Your other edits are left alone.
- **Continue this session…** sends a follow-up in the same Claude Code
  session (`--resume`), and it waits for approval again.

Billing belongs to Claude Code: a subscription login uses your plan's limits,
and an API key is billed per token. A minimal run here cost $0.01–0.13.

---

## 4. OpenArt: images and video, with credit approval

JARVIS uses **OpenArt's official hosted MCP server**
(`https://mcp.openart.ai/mcp`), with the sign-in method the MCP specification
defines (OAuth 2.1 with PKCE). There is no API key to paste.

1. **AI Connections → OpenArt → Set up OpenArt.**
2. **Sign in with OpenArt.** A new tab opens on OpenArt's own sign-in page.
   After you sign in, OpenArt sends the tab back to
   `http://127.0.0.1:8787/api/oauth/callback`. JARVIS finishes the sign-in
   and connects, and you can close the tab. Start this from the phone itself:
   sign-in is refused from any address other than 127.0.0.1.
3. The card shows **Connected** and the tools OpenArt offers.

Then ask JARVIS in the chat, e.g. *"make an image of a red fox in snow"*.

- **Every OpenArt call is held** unless its name is plainly a read, such as
  `list_…` or `get_…`. This holds even if the server labels the tool
  read-only, and even if *Allow writes* is on for other tools.
- The card in the chat says the call would spend credits.
  **Spend credits and run** asks once more, then runs exactly that call, once.
- Images, video and audio that come back are saved to
  `~/.jarvis/workspace/Results/Media/` and shown in the chat.
- **Sign out** forgets the tokens. They never reach the browser.

Whether your OpenArt plan includes MCP access, and what each generation
costs, is OpenArt's decision. If sign-in or a call is refused, the card shows
OpenArt's own error.

---

## 5. Where things are stored

| What | Where |
|---|---|
| API keys, OpenArt sign-in tokens | `~/.jarvis/secrets.json` (mode 600) |
| Settings (no secrets) | `~/.jarvis/config.json` |
| Spend ledger | `~/.jarvis/usage.json` |
| Generated media | `~/.jarvis/workspace/Results/Media/` |

---

## 6. Checking it yourself

```bash
python3 tools/test_brain.py        # 104 checks, offline, spends nothing
```

It uses `tools/mock_cloud.py`, which speaks the OpenAI, Anthropic, Gemini,
Ollama and MCP-with-OAuth wire formats, and a throwaway `JARVIS_HOME`, so
your real settings are never touched.
