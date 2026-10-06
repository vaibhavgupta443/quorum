# Setup (free, about 5 minutes, nothing heavy runs on your PC)

Quorum's pieces and where they run:

| Part | What it is | Runs on | Cost |
|---|---|---|---|
| **Brain** | a hosted language model (default: Groq, GPT-OSS 120B) | their servers, over the internet | free tier |
| **Tools / MCP / app** | the code in this repo | your PC (light) | free |

**Privacy:** only the transcript text (plus your tool results) is sent to the model API. Do not paste confidential meetings
into a free hosted API unless you are comfortable with that provider's terms.

## 1. Get a free API key (1 minute, no credit card)

1. Go to **https://console.groq.com** and sign in with Google or GitHub.
2. Open **API Keys**, click **Create API Key**, name it anything, and copy the key (it starts with `gsk_`).

Free limits (at the time of writing): about 30 requests per minute, and a per-minute and daily token allowance per model.
One transcript uses a few thousand tokens in fast mode. If you hit a cap, set `BOARDBOT_MODEL=openai/gpt-oss-20b` (a smaller
model with a bigger allowance) or wait for the reset.

## 2. Install and run

Double-click **`setup.bat`**: it creates the Python environment, installs packages, and asks you to paste the key (it saves
it to a `.env` file that is never committed). Then double-click **`start.bat`**: the browser opens at http://127.0.0.1:8000.

Manual equivalent:
```powershell
cd boardbot
..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # then edit .env: GROQ_API_KEY=gsk_...
python -m app.cli doctor        # tells you what's missing
python -m app.cli serve
```

A run takes about 5-10 seconds (fast mode, the default). Tick "agent mode" in the UI (or `--agent`) for the slower tool-calling version.

## 3. Check everything

```powershell
python -m app.cli doctor                              # key valid? Jira / Calendar reachable?
python examples\01_read_and_replay.py                 # the replay idea (no key needed)
python examples\03_mcp_client.py                      # MCP demo (no key needed)
python examples\02_agent_loop.py                      # the smallest agent (uses your key)
python -m app.cli plan samples\sprint_sync.txt -v     # the full pipeline in the terminal
pytest                                                # 111 tests, no key needed
```

## Other free providers (same code, one setting)

Anything that speaks the "OpenAI-style" chat API works, because the app only talks to the model through `app/llm.py`.
Put these in `.env` (keys: Google AI Studio, Cerebras Cloud, OpenRouter):

```
BOARDBOT_PROVIDER=gemini        # or cerebras, openrouter
GEMINI_API_KEY=...              # CEREBRAS_API_KEY / OPENROUTER_API_KEY for the others
# BOARDBOT_MODEL=...            # defaults are suggestions; check the provider's model list if you get "model not available"
```
Any other OpenAI-style provider: `BOARDBOT_PROVIDER=mycloud`, `BOARDBOT_BASE_URL=https://.../v1`, `BOARDBOT_API_KEY=...`, `BOARDBOT_MODEL=...`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Banner: "No API key found" | Put `GROQ_API_KEY=gsk_...` in `boardbot\.env`, then restart `start.bat`. |
| "The groq API key was rejected" | Re-copy the key (no spaces or quotes) or create a new one. |
| "free groq limit was reached" | Wait a minute (per-minute limit) or a day (daily limit), or use `BOARDBOT_MODEL=openai/gpt-oss-20b`. |
| "model ... is not available to your key" | Use a model name from the list the message prints. |
| A name in the transcript is not matched | Names come from the team list (`app/db.py`, or your `team.json`, see INTEGRATIONS.md). |
| `python` not found in the .bat files | Install Python 3.11+ from python.org and tick "Add to PATH". |

## Deploying to AWS later

- The model is behind one interface (`app/llm.py`). To use Amazon Bedrock, write one more class with the same `chat` method and
  add it to `make_llm`. The agent, planner, tools, MCP server, executor and UI do not change.
- Run the web app in a container (ECS / App Runner / EC2). Keep the API key in AWS Secrets Manager, not in `.env`.
- Swap SQLite for RDS, or point the backends at real Jira and Google Calendar (see `INTEGRATIONS.md`).
- Before exposing it publicly: authentication, HTTPS, request size limits (transcripts are already capped at 200,000 characters), per-user workspaces, and cost/rate limits.
