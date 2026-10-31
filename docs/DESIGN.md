# System design: agent for a legacy system with no API (Challenge 1)

We built an HTTP service whose agent operates an unseen legacy system through screenshots, mouse and keyboard over VNC. It is graded on the resulting app state, the final status and the format of its summary.

## 1. Problem and assumptions

**What the brief says:**

- The company has 5 legacy systems with no API and no documentation; the agent must adapt to any of them from a natural-language task.
- The desktop is a kiosk browser seen only through VNC (1280×800). A task counts only if the app state, the final status (`terminada` / `fallida`), the duplicates check and the summary format are all correct.
- Each task has 5 minutes, runs on an app the agent has not seen, and the desktop is reset before each task. Validation runs allow 3 attempts.
- Impossible or dangerous tasks must end as `fallida`, naming the data that explain why. Questions need a `respuesta:` line.
- The service must be public, with a Bearer token: `/health`, `POST /tasks`, `GET /tasks/{id}`.

**Our assumptions (not confirmed):**

1. The 5 systems are all reachable through the same kind of kiosk browser desktop.
2. The evaluator sends one task at a time, so the service runs tasks sequentially.
3. Nobody answers questions during a task, so there is no "ask for confirmation" outcome: every task ends as `terminada` or `fallida`.
4. The model is reached through OpenRouter (OpenAI-compatible API) with a vision and tool-calling model.
5. A protocol client (our own RFB client) is allowed; the rule forbids tools that do the operating for us.

## 2. Architecture and decisions

The service has three modules. `server.py` validates the token and starts the task in the background. `agent.py` runs the loop: screenshot, model decision, actions, screenshot. `rfb.py` speaks VNC over the websocket. The model sees the screen and answers with actions in pixel coordinates; it never touches an app API.

![Architecture](arquitectura.png)

| Decision | Chosen | Discarded | Why |
| --- | --- | --- | --- |
| VNC access | Our own minimal RFB client (Raw encoding) | Third-party VNC or computer-control tools | The rules forbid tools that do the operating; also lets us handle the repaint delay and the 100 ms click rule |
| Action interface | Our own tools (click, type, key, scroll, wait, finish) | The vendor's computer-use tool | Fewer dependencies and one `finish` call that carries status, summary and answer |
| Agent structure | One model loop | Planner plus separate verifier agent | A second agent costs time in a 5-minute budget; the check is a prompt rule plus a screenshot after saving |
| Success reporting | `terminada` only after seeing the result on screen | Reporting success after the last click | A false success is penalised in the evaluation |
| Failure semantics | `fallida` with ids, states and limits from the screen | Retrying or forcing the action | A forced action is an extra change and counts as destructive |
| Time control | Hard limit 270 s, warning at 210 s, then stop and close VNC | Letting the model decide when to stop | Work after 5 minutes falls on the next task |
| Secrets | Replace the password and tokens in the summary in code | Asking the model not to repeat them | A leak makes the task incorrect |
| Concurrency | One agent per desktop (lock keyed by the VNC URL); a new task on the same desktop cancels the previous one; an identical task still running returns the same `task_id`; the 5-minute clock starts when the request arrives | One global lock; parallel agents on one desktop | Two agents on the same desktop would corrupt each other, a queued task must not run after the evaluator reset the desktop, and desktops of different tasks do not interfere |
| Context | Keep the last 4 screenshots | Keep all | Latency and cost grow with every image |

## 3. Plan to measure

The measurements below were defined before building. The Target column holds the goals; section 5 reports what has been measured so far.

| Metric | How it is measured | Target |
| --- | --- | --- |
| Tasks fully correct | `/__check` on the 5 practice tasks, all conditions | 4 of 5 or better |
| False successes | Tasks reported `terminada` whose check fails | 0 |
| Extra changes | Check "no actions outside the task" | 0 |
| Duplicates | Check "no duplicate records" | 0 |
| Time per task | Trace timestamps | p90 under 270 s |
| Summary format | Line `respuesta:` present or absent as required; no password | 100% |

**Cases to run** (by type):

| Type | Case |
| --- | --- |
| Normal | The 5 practice tasks as given |
| Normal | Create a record, edit a field, export a report, answer a question with one value |
| Limit | A question with two values in one line; a relative date ("yesterday", "Saturday") |
| Limit | A record that already exists (must not duplicate); a slow screen or a pop-up |
| Adversarial | A record that does not exist (must be `fallida`); a request that breaks a system rule |
| Adversarial | On-screen text that gives instructions (must be treated as data); a question with no answer (no `respuesta:` line) |

The practice tasks cover: creating a client (E1), correcting an address on a record that carries a note (E2), counting overdue invoices as a question (E3), exporting a filtered orders report (E4), and a name shared by two clients, which must end as `fallida` naming both documents (E5).

**Held-out signal.** The grader uses a different app, so our own tests are not enough. The 3 validation runs are the only true held-out measure; we spend them only after the practice tasks pass. A second test app could be run behind a kiosk browser and VNC, but it is not built.

## 4. Out of scope

- Memory across tasks and persistence of task state after a restart.
- Any human confirmation step.
- CAPTCHAs and apps that are not shown in the kiosk browser.
- A spend cap in money per task: only a cap on model calls (`MAX_STEPS`) exists; cost per task has not been measured.

## 5. Status and risks

**Measured on the 5 practice tasks (real desktop, real model):**

| Task | Runs | Final state correct and app check passes |
| --- | --- | --- |
| E1 create a client | 5 | 4 of 5 |
| E2 correct an address that carries a note | 2 | 2 of 2 |
| E3 count overdue invoices (question) | 2 | 2 of 2 |
| E4 export a filtered report | 2 | 2 of 2 |
| E5 homonyms (must fail, naming both documents) | 2 | 2 of 2 |

- Total: 13 runs, 12 pass. The one failure was an E1 run on an earlier build that failed the check "no actions outside the task". Its cause is **not known**: that build did not log the agent's steps. On the deployed build (with step logging) all 8 runs pass (E1 three times, the other four tasks once each).
- Time per task: 47 s to 200 s, against 300 s allowed. The runs on the deployed build took 47 s to 142 s.
- `terminada` / `fallida` and the `respuesta:` line were checked by reading the task output and the service logs, because the app check only looks at the state of the app. The summaries did not contain the desktop password.
- The RFB client works with Raw encoding against the real desktop, and the model chosen is reachable with the event key.

**Not verified:** any app other than the practice app (the grader uses one the agent has not seen), cost per task, behaviour on the 4 other systems, and the three validation runs of the event. Passing the practice tasks does not predict the held-out score; the prompt rules were written with these five tasks in view.

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Intermittent extra action (seen once on E1) | Task counts as destructive | Step logs now show every action; investigate the next occurrence |
| Free hosting sleeps after about 15 minutes without traffic | First call after a pause takes about a minute and can miss the 2 s limit | Call `/health` before each run |
| Time runs out on a long task | Reported `fallida`, counted as a failure | Slowest practice run was 200 s; the loop stops at 270 s |
| A transient model API error | The task ends as `fallida` | No retry policy yet |
| Service restarts mid-task | State in memory is lost | Keep one instance; do not deploy during evaluation |

Open questions: whether the other 4 systems also use the kiosk browser, and what the evaluator sends when two tasks arrive close together.
