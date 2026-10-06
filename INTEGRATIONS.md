# Connect Quorum to Jira and Google Calendar (both free)

By default Quorum uses a built-in demo database, so it works with nothing connected. To showcase the real thing,
point it at **Jira** (tasks) and/or **Google Calendar** (events). You can connect either one, or both.

The important idea for interviews: the agent and the MCP *tools* don't change. Only `app/backends.py` knows whether a
task goes to the demo database or to Jira. That is what MCP buys you.

```
planner agent ──MCP tools──> app/mcp_server.py ──> app/backends.py ──┬─> Jira Cloud REST API
(unchanged)                  (unchanged tools)                       ├─> Google Calendar API
                                                                     └─> local SQLite demo
```

## 0. Put your real team in `team.json`

The agent matches names spoken in a meeting against this list, and uses the emails for calendar invites and Jira lookups.
Copy `team.example.json` to `team.json` and edit it (include **yourself** with the Google/Jira email you will sign in with):

```json
[
  {"name": "Vaibhav Gupta", "email": "you@gmail.com", "role": "Engineer"},
  {"name": "Teammate One", "email": "friend1@gmail.com", "role": "Designer"}
]
```
Then run `python -m app.cli seed` (or press "Reset demo data" in the UI) to load it. Keep two people sharing a first name
if you want to show off the "which Priya?" question. The sample transcripts mention Rahul, Priya, Ananya and Karan; for demos
with your own team, use your teammates' names in a transcript of your own (copy a file in `samples/` and edit it).

## 1. Jira (free plan, about 5 minutes)

1. Create a free Jira site at **atlassian.com/software/jira/free** (sign up; choose "Software development" and a **team-managed** project; name it e.g. "Demo", key e.g. `DEMO`).
2. Create an API token: **id.atlassian.com/manage-profile/security/api-tokens** -> *Create API token* -> copy it.
3. Put these in `boardbot/.env` (copy `.env.example` first):
   ```
   BOARDBOT_TASKS=jira
   BOARDBOT_JIRA_BASE_URL=https://YOURSITE.atlassian.net
   BOARDBOT_JIRA_EMAIL=you@gmail.com
   BOARDBOT_JIRA_TOKEN=the-token-you-copied
   BOARDBOT_JIRA_PROJECT=DEMO
   ```
4. Check it: `python -m app.cli doctor` should print `Jira OK: signed in as ..., project DEMO (...)`.

What happens: "search_tasks" reads open issues in that project (so duplicates are caught against your real board);
`create_task` creates a real issue with the due date, priority, label `boardbot`, and a label like `owner-Rahul_Mehta`.
It assigns the issue if it can find that person's Jira account by email (or `jira_account_id` in `team.json`), otherwise it
creates it unassigned and says so. If your project doesn't allow a field (for example priority), Quorum drops that field and retries.

Other free boards (Trello, GitHub Issues, Linear) work the same way: write one class like `JiraBoard` in `backends.py`
with `list_open`, `create`, `check`, and select it with a setting. Nothing else changes.

## 2. Google Calendar (free, about 10 minutes, one time)

1. Go to **console.cloud.google.com**, create a project (any name).
2. *APIs & Services -> Library* -> search **Google Calendar API** -> **Enable**.
3. *APIs & Services -> OAuth consent screen* -> choose **External**, fill the app name and your email, and **add your own Google account under "Test users"**. (Leave it in "Testing" mode: no review needed.)
4. *APIs & Services -> Credentials -> Create credentials -> OAuth client ID -> Application type: **Desktop app*** -> Download the JSON.
5. Save that file as `boardbot/google_credentials.json`.
6. Add to `.env`:
   ```
   BOARDBOT_CALENDAR=google
   BOARDBOT_TIMEZONE=Asia/Kolkata
   ```
7. Sign in once: `python -m app.cli connect google` (a browser opens; allow access; Google may warn "unverified app", which is expected for your own test app: *Advanced -> continue*). This saves `google_token.json`.
8. Check: `python -m app.cli doctor` should print `Google Calendar OK: you@gmail.com ...`.

How it behaves:
- **Invites are never emailed** (`sendUpdates="none"`), so you can demo with real teammate addresses safely. Events appear on the signed-in calendar with attendees listed.
- **Conflicts**: Google only lets Quorum see the signed-in user's own calendar. An event counts as busy time for (a) every team member who is an attendee (matched by email in `team.json`) and (b) you, the calendar owner. So for the demo, create a couple of meetings on your calendar with your teammates as attendees, then mention a clashing meeting time in the transcript.
- All-day events and cancelled/declined events don't block meetings. Meetings are only scheduled Mon-Fri 09:00-18:00 in `BOARDBOT_TIMEZONE`.

## 3. Run the demo

Use real dates: set `BOARDBOT_TODAY=real` in `.env` (the default is a fixed demo date so the sample transcripts stay valid), load a
real transcript (or one of the samples), and run `start.bat`. The header chips show `tasks: Jira | calendar: Google Calendar`.
After you press **Approve & create**, the results panel shows links straight to the new Jira issues and calendar events.

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| doctor: "Jira rejected the login" | Use your Atlassian **email** and an **API token** (not your password); base URL has no trailing path. |
| doctor: "Jira returned 404" | Wrong site URL or project key (the key is the capital letters on issue numbers, e.g. DEMO-12). |
| Issue created but unassigned | The person has no Jira account matching the email in `team.json`. Add `jira_account_id`, or invite them to the Jira site. |
| "Google Calendar is not connected yet" | Run `python -m app.cli connect google`. |
| Google says "access blocked" | Add your account as a **Test user** on the OAuth consent screen (step 3). |
| Token expired after ~7 days | Apps in "Testing" mode expire refresh tokens weekly: run `connect google` again. |
| Want the demo database back | Remove the `BOARDBOT_TASKS` / `BOARDBOT_CALENDAR` lines from `.env`. |

Security: `.env`, `team.json`, `google_credentials.json` and `google_token.json` hold secrets or personal data and are git-ignored. Never commit them.
