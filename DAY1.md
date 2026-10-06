# Day 1: from zero to understanding how an AI agent works

**Time:** about 5 hours with breaks. **You need:** nothing except this folder open in VS Code. No theory first: you will run
things, then read the code that did it.

**By tonight you will be able to explain, in your own words:**
1. what Quorum does and how data flows through it
2. what an AI model really is (and is not)
3. what a "tool" is and why the model never runs code
4. the agent loop, line by line (the single most important idea in agentic AI)

---

## Part A. The project in plain words

After a team meeting you have a **transcript**: who said what. It is messy, because people change their minds:

```
[00:01:10] Priya Nair: Next, the API documentation. Karan, can you write those?
[00:01:20] Karan: I'm swamped with testing this week.
[00:01:35] Priya Nair: Okay then, Priya S, can you take the API docs instead?
[00:01:41] Priya Sharma: Yes, I'll write them. Monday works for me.
...
[00:05:00] Priya Nair: Demo prep sync, how about Thursday at 3pm with Rahul and Ananya?
[00:05:20] Priya Nair: Fine, let's move it to Friday at 11am.
```

Quorum turns that into **a plan**: the API docs belong to **Priya Sharma, due Monday** (not Karan: the later statement
wins), and there is **ONE demo sync, Friday 11am** (not two meetings). It also skips anything cancelled, asks you when a
name is ambiguous, and drafts a recap email. **You check the plan, and only then does it create anything.**

Why it counts as *agentic AI* and not just "summarise the text": it has to **look things up** (who is "Priya"? two people
match; does this task already exist? is Rahul free at 11am?) and **act carefully** (ask you when unsure, never guess, never
act without approval).

---

## Part B. Words you need (plain English)

| Word | What it means here |
|---|---|
| **Program / Python** | A program is a list of instructions. Python is the language this project is written in. |
| **File types** | `.py` Python code. `.md` notes you read (like this). `.env` your secret settings (your API key). `.json` data in a tidy text format. `.bat` a double-click script for Windows. |
| **Terminal** | A text window where you type commands instead of clicking. |
| **Library / package** | Code other people wrote that we reuse (e.g. FastAPI, which runs the web page). Installed with `pip`. |
| **API** | A way for one program to ask *another program* (usually over the internet) to do something. We ask Groq's servers to run an AI model. |
| **HTTP request** | The actual message sent to an API: "here is my question, here is my key". The reply is the **response**. |
| **JSON** | A text format for data: `{"name": "Rahul", "days": 45}`. Curly braces = a thing with named fields. Square brackets = a list. |
| **API key** | A secret password for an API. Yours is in the `.env` file. Never share it or commit it. |
| **AI model / LLM** | A giant pattern-predicting program. You give it text, it writes the most likely helpful reply. **It has no memory between calls and it cannot run code or open files.** It only produces text. |
| **Prompt** | The text you send the model. |
| **Token** | A chunk of text (about 3/4 of a word). Models read and write tokens, and free APIs limit how many per minute/day. |
| **Message roles** | A conversation is a list of messages, each with a role: `system` (the rules you set), `user` (what the person says), `assistant` (what the model says), `tool` (the result of a tool you ran). |
| **Tool** | An ordinary Python function that the model is *allowed to ask for*, e.g. `add_days(start, days)`. |
| **Agent** | A model **in a loop with tools**: it asks for a tool, your code runs it and shows the result, it decides what to do next, until it is done. |
| **Transcript / utterance** | A transcript is the text of a meeting. One *utterance* is one person's turn, e.g. line #6: `Priya Nair: Okay then, Priya S ...`. We number them so every fact can point to its proof. |
| **MCP** | A standard way to offer tools to AI programs (Day 2). Think of it as "USB for AI tools". |

**The most important sentence of the whole project:**
> The model never does anything itself. It only *writes text asking for something*. **Our code** does the work and tells it what happened.

---

## Part C. The whole project in one picture

What happens when you click "sprint sync" (the files are what you will study over the 5 days):

```
1. READ      split the text into numbered lines: "#6 Priya Nair: Okay then, Priya S ..."      app/transcript.py
2. CLEAN     lines that look like "ignore your rules" attacks are removed                   app/safety.py
3. EXTRACT   ONE call to the AI model: "list WHAT WAS SAID, step by step, citing line #"      app/llm.py, app/planner.py
4. REPLAY    plain code applies those steps in order, so the LATEST statement wins           app/reduce.py
5. REPAIR    plain code: exact names, real dates, duplicates, calendar clashes                app/normalize.py
6. CHECK     more code re-checks the result against the workspace                            app/planner.py
7. REVIEW    YOU edit and approve the plan in the web page                                   app/static/index.html
8. DO        plain code creates the tasks/events/email (no AI)                               app/executor.py
```
Where do the *tools* live? In a small separate program, the **MCP server** (`app/mcp_server.py`), which knows the team,
the calendar and the task list. Steps 5, 6 and 8 call it. An optional **agent mode** adds the tool-calling loop in the
middle (`app/agent.py`): the model itself decides which tools to call. **The loop is today's lesson**, because it is how all
agents work, even though fast mode (the default) skips it for speed.

You don't need to understand steps 1-8 deeply today. Today is the **foundation**: reading, brain, loop.

---

## Part D. The Python you need to read this code

You do not need to be a Python expert. These 10 things cover almost everything you'll see. Each example is real code from the project.

1. **Strings and f-strings**: text in quotes. `f"hello {name}"` puts the variable's value inside.
2. **Lists and dicts** (the most important):
   ```python
   messages = [                                   # a LIST: ordered items in [ ]
       {"role": "user", "content": "Hi"},         # a DICT: named fields in { }
   ]
   messages[0]["role"]   # -> "user"
   ```
3. **Functions** with type hints: `def add_days(start: str, days: int) -> str:` means "takes text and a whole number, returns text". Hints are labels for humans.
4. **`if` / `for`**: `for round_number in range(1, 8):` repeats up to 7 times. `if not reply.tool_calls:` runs only when there are no tool calls.
5. **List comprehension**: a one-line loop that builds a list.
   `[c.name for c in reply.tool_calls]` means "the name of every tool call".
6. **Classes and `@dataclass`**: a class bundles data (and functions) under one name. `reply.text` means "the `text` field of the object `reply`".
7. **`try` / `except`**: "try this; if it breaks, do that instead of crashing".
8. **`import`**: bring in code from another file or a library: `from app.llm import make_llm`.
9. **`**` unpacking**: `RUNNERS[c.name](**c.arguments)` turns `{"days": 45, "start": "..."}` into `add_days(days=45, start="...")`.
10. **`async` / `await`**: some actions take time (waiting for the internet). `await` means "wait for this without freezing everything else". Any function using `await` must be `async def`, and you start the whole thing with `asyncio.run(main())`. **Just read `await x` as "wait for x".**

---

## Part E. Your plan for today (do these in order)

**How to run things.** Open VS Code, `File > Open Folder` and choose `...\Agentic Project\boardbot`. Open a terminal (`` Ctrl+` ``)
and type commands exactly as shown. Always run them from the `boardbot` folder:
```powershell
cd "C:\Users\vaibh\OneDrive\Desktop\Agentic Project\boardbot"
..\.venv\Scripts\python examples\01_read_and_replay.py
```
(`..\.venv\Scripts\python` is Python with all the project's libraries. No activation needed.)

### Step 1 (15 min). Use it before reading it
Double-click `start.bat`. Click **sprint sync**. Watch the live feed while it runs (about 5-10 seconds). When the plan appears,
find: the API-docs task (owner **Priya Sharma**, with a history line showing it was Karan first), the **single** demo sync
(Friday 11:00, with `#` chips showing which lines prove it), the **"Which Priya?"** question, and the notes about the
dropped terms-of-service task and the settings-page idea. Click a `#` chip: the transcript jumps to that line. Approve it and open the
Workspace panel. **Write one sentence about each thing you noticed.**

### Step 2 (30 min). Reading, and the big trick: replay
Run `..\.venv\Scripts\python examples_read_and_replay.py` (no key, no internet). Real output:
```
#1 Priya Nair     Karan, can you write the API docs?
#3 Priya Nair     Okay, Priya S, take the API docs instead. Monday works?
...
FINAL STATE (after replaying the steps):
  task    Write API docs: owner=Priya S, due=2026-10-12
            history: #1: assigned to Karan
            history: #3: owner Karan -> Priya S
            history: #4: due 2026-10-12
  meeting Demo sync: 2026-10-09T11:00
            history: #5: scheduled 2026-10-08 15:00
            history: #6: moved 2026-10-08 15:00 -> 2026-10-09 11:00
```
What to understand:
- `parse_transcript` (in [app/transcript.py](app/transcript.py)) turns raw text into numbered `Utterance(index, speaker, time, text)` objects.
- In the example, the **log of steps was written by hand**. In the real app the **AI model writes it**, and it is the model's only job: *list what was said*, not decide the final answer. Why? Tracking "owner changed, then changed again, meeting moved twice" in one's head is where small models slip.
- [app/reduce.py](app/reduce.py) `reduce_log` then replays the steps **in order with plain code**: the latest statement wins, and every change is recorded as history. Read only its docstring today.
- Try it (edit the example, run, undo): (a) delete the `Step` at utterance 3: the owner stays Karan. (b) add `Step(utterance=8, item="T1", action="cancel")`: the task disappears into a note. (c) change the meeting step to `"next Tuesday at 2pm"`.

### Step 3 (30 min). What "calling the AI" really is
It is just a web request. Open [app/llm.py](app/llm.py), class `OpenAICompatLLM`, method `chat` (line ~127). In simplified form it sends this to Groq:
```
POST https://api.groq.com/openai/v1/chat/completions
Authorization: Bearer <your key>

{ "model": "openai/gpt-oss-120b",
  "temperature": 0.2,
  "messages": [ {"role": "system", "content": "..."}, {"role": "user", "content": "..."} ],
  "tools": [ {"type": "function", "function": {"name": "add_days", "description": "...", "parameters": {...}}} ] }
```
and gets back either text, or **a request to use a tool**:
```
{ "choices": [ { "message": { "content": null, "tool_calls": [
      { "id": "call_ab12", "type": "function",
        "function": { "name": "add_days", "arguments": "{\"start\": \"2026-10-06\", \"days\": 45}" } } ] } } ],
  "usage": { "prompt_tokens": 312, "completion_tokens": 28 } }
```
Notice: the model did **not** compute anything. It wrote "please call add_days with these arguments". Also notice `arguments`
arrives as a *string* of JSON. That is why `llm.py` has a tiny function `_as_dict` to turn it back into a dict.
Lines 153-181 are the **error handling**: wrong key, too many requests (wait and retry), server hiccup. Read them as
"what can go wrong when you depend on someone else's computer".

### Step 4 (60 min). The smallest possible agent
Run `..\.venv\Scripts\python examples\02_agent_loop.py`. Real output:
```
round 1: model asked for add_days({"days": 45, "start": "2026-10-06"}) -> 2026-11-20
round 2: model asked for weekday_of({"day": "2026-11-20"}) -> Friday

FINAL ANSWER: 45 days from today, 2026-10-06, falls on Friday, November 20 2026.
```
The model chose **by itself** to use two tools in order. Now read [examples/02_agent_loop.py](examples/02_agent_loop.py) with this table:

| Lines | Code | Plain English |
|---|---|---|
| 22-27 | `def add_days(...)`, `def weekday_of(...)` | Two **ordinary Python functions**. These are the tools. |
| 31-42 | `TOOLS = [ {...}, {...} ]` | The **menu** the model sees: a name, a description and the arguments each tool needs. The model never sees the function bodies. Good descriptions = good tool use. |
| 43 | `RUNNERS = {"add_days": add_days, ...}` | A lookup so that when the model says "add_days", we can find the real function. |
| 47-48 | `Settings.from_env()`, `make_llm(s)` | Read your settings (and key from `.env`) and create the object that talks to the model. |
| 51-54 | `messages = [system, user]` | The conversation so far: the rules and the question. |
| 55 | `for round_number in range(1, 8)` | The loop, with a **hard limit** of 7 rounds so it can never run forever. |
| 56 | `reply = await llm.chat(messages, tools=TOOLS)` | **Ask the model.** Give it the whole conversation and the tool menu. |
| 57-60 | `messages.append(assistant)` | Save what the model said, because the model forgets everything between calls. |
| 62-64 | `if not reply.tool_calls: ... return` | **The stop condition.** If the model asked for no tools, its text is the final answer. |
| 66-67 | `for c in reply.tool_calls: output = RUNNERS[c.name](**c.arguments)` | The model asked for a tool, so **we run it**. |
| 69 | `messages.append({"role": "tool", ..., "content": output})` | Show the model the result, linked to its request by `tool_call_id`. Then the loop goes round again. |

Here is exactly what the model saw by the final round (the conversation after both tools ran):
```
[system]     You are a careful assistant. Use the tools for date maths; never calculate dates yourself.
[user]       Today is 2026-10-06. What weekday is it 45 days from now?
[assistant]  (asks) add_days {"start": "2026-10-06", "days": 45}
[tool]       2026-11-20
[assistant]  (asks) weekday_of {"day": "2026-11-20"}
[tool]       Friday
[assistant]  "45 days from today ... falls on Friday, November 20 2026."   <- no tool asked: done
```
**That is an agent.** Everything else in this project is this loop made safer, faster or more useful.

### Step 5 (60 min). The real loop, with safety rails
Read [app/agent.py](app/agent.py) top to bottom. It is the same loop plus protections. Match each to the problem it solves:

| In `agent.py` | Problem it solves |
|---|---|
| `max_turns` (line 53), `token_budget` | The agent gets stuck or runs up cost. |
| `seen` + "You already made this exact call" (line 111) | The model calls the same tool again and again. |
| "Unknown tool ..." (line 107) | The model invents a tool name. We reply with the real list. |
| "Missing required argument(s)" (line 109) | The model forgets an argument. We tell it what is needed. |
| `try / except` around `tool.call` (114-117) | A tool crashes. The error becomes a **message the model reads**, not a crash. |
| `_fix_argument_names` (line 130) | The model wrote `text=` when the tool wanted `query=`. If it's obvious, we fix it silently. |
| `MAX_RESULT_CHARS` truncation (line 122) | A huge tool result would flood the model. |
| `asyncio.gather(...)` (line 87) | Several tools requested at once run **in parallel**. |

Principle to remember: **errors become data.** Instead of crashing, tell the model what went wrong and let it try again.

### Step 6 (30 min). Break things on purpose (this is how you learn)
Edit `examples/02_agent_loop.py`, run it, and write down what happens. Undo each change afterwards.
1. Delete the `weekday_of` entry from `TOOLS` (lines 37-41). Does the model still answer? How?
2. In `add_days`, add `+ 1` to the days (a deliberate bug). Does the model notice the wrong answer?
3. Change the question to something that needs **no** tool ("What is 2+2?"). How many rounds now? Why?
4. Change `range(1, 8)` to `range(1, 2)`. What happens, and what does that show about the limit?

### Step 7 (30 min). Notes and self-test
In a new file `notes.md`, write **in your own words**: what an agent is; what a tool is; why the model needs the history resent;
why we limit rounds. Then answer the self-test below *without* looking.

---

## Part F. Self-test (answers at the bottom: no peeking)

1. Can the AI model run Python code or read your files? What can it actually produce?
2. What is a "tool", and what does the model see about one?
3. What does the model send when it wants to use a tool, and who runs the tool?
4. Why do we resend the whole `messages` list on every round?
5. What are the two ways the loop can end?
6. Why does a crashing tool return an error message instead of stopping the program?
7. Why do we make the model list *what was said* and let code replay it, instead of asking the model for the final plan?
8. Why is `arguments` converted from a string back into a dict?
9. Name three protections in `agent.py` and the problem each solves.
10. In one sentence: what is an agent?

<details><summary>Answers</summary>

1. No. It only produces text. (Asking for a tool is also just text.)
2. A normal Python function the model is allowed to request. It sees only the name, a description and the argument schema.
3. A tool call: a name plus arguments (as JSON). Our code runs the function and sends back the result.
4. The model has no memory between calls. The history is how it "remembers" the conversation and the tool results.
5. The model asks for no tools (final answer), or a safety limit stops it (`max_turns` / token budget).
6. The model can read the error and try something else, so one failure doesn't kill the whole task.
7. Tracking changes of mind is where models slip, but "apply steps in order, latest wins" is trivial and 100% reliable in code. The model does the reading it is good at; code does the exact part. Bonus: every fact points to a line number you can check.
8. The API returns arguments as JSON text, but our code needs a real dict to call the function.
9. Any three of: turn limit (can't loop forever), repeat guard (same call twice), unknown-tool message (invented tool), missing-argument message, try/except (crashing tool), truncation (huge result), argument-name fix.
10. A model in a loop with tools: it asks, your code acts, and it decides what to do next until it's done.
</details>

---

## If you get stuck
- **A command fails:** copy the *whole* error text to me; the last 3 lines usually say what's wrong.
- **"No API key found":** open `boardbot\.env`; it must contain one line `GROQ_API_KEY=gsk_...` with no spaces or quotes.
- **A word you don't know:** check Part B, then ask me.
- **Too much code on screen:** that's normal. Only read the lines I point to; the rest comes on Days 2-5.

## Tomorrow (Day 2): tools as a service (MCP)
You'll meet the **MCP server**: a separate program that offers tools (people, calendar, tasks), and how our code discovers and
calls them. Same idea as today's tools, but they live in their own program so any AI app can use them. That is also how the
app can create real Jira issues and Google Calendar events.
