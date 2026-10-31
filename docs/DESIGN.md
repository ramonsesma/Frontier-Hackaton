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
4. The model is reached with an Anthropic API key.
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
| Concurrency | One task at a time | Parallel tasks | Two tasks on the same desktop would corrupt each other |
| Context | Keep the last 4 screenshots | Keep all | Latency and cost grow with every image |

## 3. Plan to measure

The measurements below are defined before more building. The numbers are targets, not results: nothing has run against a real desktop yet.

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
- Parallel tasks and any human confirmation step.
- CAPTCHAs and apps that are not shown in the kiosk browser.
- A spend cap per task (open, see risks).

## 5. Status and risks

**Verified:** the RFB client passes a test against a simulated VNC server (handshake, authentication, framebuffer, input), and the service imports. **Not verified:** any run against a real desktop, against the model API, or on the practice tasks.

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Real server uses a different encoding than Raw | Client fails with a clear error | Add the encoding found |
| Screen repaints slower than 1 s | The model acts on a stale capture | Increase the wait after each action |
| Time runs out on a finished task | Reported `fallida`, counted as a failure | Tune the loop with the practice tasks |
| No spend cap per task | A loop could spend more than planned | Add a cost or step cap before deploying |
| Service restarts mid-task | State in memory is lost | Keep one instance during evaluation |

Open questions: whether the other 4 systems also use the kiosk browser, and how the evaluator handles two tasks sent at once.
