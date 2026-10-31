# Agent for a legacy system with no API (Frontier Challenge 1)

An HTTP service whose agent operates an unseen management system the way a new employee would:
it looks at the screen and uses mouse and keyboard over VNC. It receives a task in natural
language, works on it in the background and reports a final status and a summary.

- Design document: [`docs/DESIGN.md`](docs/DESIGN.md)
- Architecture diagram: [`docs/arquitectura.png`](docs/arquitectura.png) (editable source: `docs/arquitectura.svg`)

![Architecture](docs/arquitectura.png)

## How it works

1. `POST /tasks` receives the task and the desktop (`wss://.../websockify` URL and password) and
   returns a `task_id` immediately.
2. `agent.py` connects to the desktop through `rfb.py` (a minimal RFB/VNC client written for this
   project) and loops: screenshot, model decision, actions, new screenshot.
3. The model finishes with a `finish` call carrying the status (`terminada` / `fallida`), a summary
   and, for questions, the answer. The summary gets a `respuesta: <value>` line when it is a question.
4. The desktop password and the service secrets are replaced in the summary before it is published.
5. `GET /tasks/{task_id}` returns `{"estado": "en_curso" | "terminada" | "fallida", "resumen": "..."}`.

A task is stopped at 270 s counted from the moment the request arrives (the evaluator cuts at 300 s),
and the VNC connection is closed as soon as the final status is reported. Only one agent runs per
desktop: a new task for the same desktop cancels the previous one, and an identical task that is
still running is not started twice.

## Entrypoint

Docker:

```bash
docker build -t desktop-agent .
docker run -p 8080:8080 \
  -e TEAM_TOKEN=<team token> \
  -e OPENROUTER_API_KEY=<model key> \
  desktop-agent
```

Without Docker (Python 3.12):

```bash
pip install -r requirements.txt
TEAM_TOKEN=<team token> OPENROUTER_API_KEY=<model key> uvicorn server:app --host 0.0.0.0 --port 8080
```

Every call needs `Authorization: Bearer <TEAM_TOKEN>`.

| Endpoint | What it does |
| --- | --- |
| `GET /health` | `{"ok": true}` when the service is ready |
| `POST /tasks` | Body: `{"tarea": "...", "escritorio": {"url": "wss://...", "password": "..."}}`. Returns `{"task_id": "..."}` |
| `GET /tasks/{task_id}` | `{"estado": "...", "resumen": "..."}` |

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `TEAM_TOKEN` | Bearer token; without it every call returns 401 | required |
| `OPENROUTER_API_KEY` | Model access through OpenRouter | required |
| `AGENT_MODEL` | OpenRouter model id that chooses the actions (must accept images and tools) | `anthropic/claude-sonnet-4.5` |
| `LLM_BASE_URL` | OpenAI-compatible endpoint | `https://openrouter.ai/api/v1` |
| `MAX_STEPS` | Cap of model calls per task | `80` |
| `TASK_HARD_LIMIT_S` | Hard limit per task, in seconds | `270` |
| `TASK_WARN_S` | When the model is told to wrap up | `210` |
| `PORT` | Port inside the container | `8080` |

## Tests

```bash
python test_rfb.py     # VNC client against a simulated server
python test_server.py  # per-desktop locking, cancellation, duplicates, late start (needs httpx)
```

It checks the RFB client (handshake, VNC authentication, framebuffer, mouse and keyboard) against a
simulated VNC server. It does not exercise the model or a real desktop.

## Status

- Unit tests (no network): the RFB client against a simulated server, the agent loop with a fake model
  and desktop, and the service's per-desktop locking.
- Run against a real desktop with the real model on the 5 practice tasks: 13 runs, 12 pass. All 8 runs on
  the current build pass. The one failure was on an earlier build, with an unknown cause (no step logs
  then). Times: 47 s to 200 s per task.
- Not verified: any app other than the practice app, cost per task, and the event's validation runs.
  Details and risks are in the design document.

## Use of AI

Most of the code and documentation in this repository was drafted by Claude (Anthropic) in a
conversation directed by Ramón Sesma. The agent itself calls a Claude model at run time to decide
its actions.

No external tool that operates desktops is used; the VNC client is implemented in `rfb.py`.
