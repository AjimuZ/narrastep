# Ralph runner

Autonomous development loop for the roadmap: each iteration is a new Claude Code process with a
clean context, which handles one task and ends. It is adapted from
[snarktank/ralph](https://github.com/snarktank/ralph) (MIT, commit
`6c53cb0b831ebe8739c6a003e22af14902d8b0b5`, license in [LICENSE.upstream](LICENSE.upstream)). The
decision and its limits are in [ADR 0019](../../docs/decisions/0019-autonomous-development-runner.md).

This is a development tool. It is not part of Narrastep, no image contains it, and nobody needs it
to build, run or contribute to the project.

## Commands

Run these from the repository root in Git Bash, on `main`, from a terminal outside Claude Code.
The runner commits and **pushes `main` by itself** after each verified task, as the owner decided.

| Goal | Command |
| --- | --- |
| Check the setup without starting anything | `./scripts/ralph/ralph.sh --dry-run` |
| Start, at most 30 iterations by default | `./scripts/ralph/ralph.sh` |
| Start in a window of its own, which survives closing VS Code | `./scripts/ralph/open-run.sh` (the same arguments) |
| Start with another limit | `./scripts/ralph/ralph.sh 10` |
| Continue after an interruption, a limit or a failed attempt | `./scripts/ralph/ralph.sh --resume` |
| See passed, open, blocked and owner tasks | `./scripts/ralph/status.sh` |
| The same, refreshed every 30 seconds | `./scripts/ralph/status.sh --watch` |
| Follow the current agent | `tail -f "$(ls -t scripts/ralph/.state/logs/*.txt \| head -n 1)"` |
| Stop after the current iteration | `./scripts/ralph/stop.sh` |
| Stop now | `./scripts/ralph/stop.sh --now` |
| Try a blocked task again | `node scripts/ralph/prd.cjs unblock P08.3` |
| Do the guided check only a person can do, after P07.4b | `node scripts/ralph/owner-check.cjs` |
| Record another owner check that passed | `node scripts/ralph/prd.cjs confirm <id>` |

Do not commit or edit files in this repository while a run is active: the runner works on `main`
in this working tree, and it stops when `HEAD` moves under it.

Optional settings, as environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RALPH_MODEL`, `RALPH_EFFORT` | Claude Code's defaults | Model and effort of each agent |
| `RALPH_ITERATION_TIMEOUT_SECONDS` | `5400` | A longer session counts as a failed attempt |
| `RALPH_VERIFY_TIMEOUT_SECONDS` | `3600` | A longer verification counts as a failed attempt |
| `RALPH_LIMIT_WAIT_SECONDS` | `1800` | Wait after a usage limit; `0` stops the run instead |
| `RALPH_LIMIT_MAX_WAITS` | `336` | Waits before the run stops (7 days by default) |
| `RALPH_SESSION_WAIT_SECONDS` | `600` | Wait after a failed session; the sixth failure in a row stops the run |
| `RALPH_MIN_FREE_DISK_GB` | `20` | Less free disk refuses the start |
| `RALPH_PUSH` | `1` | `0` keeps verified commits local |
| `RALPH_CLAUDE_BIN` | see below | Claude Code executable |

Claude Code is found in `RALPH_CLAUDE_BIN`, then on `PATH`, then as the newest binary of the VS Code
extension, so nothing has to be installed. `jq`, which upstream needs, is not used.

## One iteration

1. The runner pushes waiting commits and stops if `origin` has work this branch lacks. `prd.cjs` then
   picks the task with the lowest `priority` whose `dependsOn` tasks passed and that is neither
   blocked nor an owner task. A task interrupted with work in the tree continues first.
2. It removes containers left by an interrupted iteration and, for a task with a `job`, runs that
   long command first, with no agent session; the output reaches the brief. A job that passed is
   not repeated. Then the runner starts Claude Code with [CLAUDE.md](CLAUDE.md) and a brief of that task: its criteria,
   last failure, the patterns in `progress.txt` and the last three log entries.
3. The agent changes files and writes `.state/result.json` with an outcome, a summary and a commit
   message. It cannot commit, and it cannot change `prd.json`, `progress.txt` or the runner.
4. On `done`, the runner runs [verify.sh](verify.sh): the changed paths, secrets, weakened tests,
   required documents and roadmap ticks, then the [checks](check.sh) of the base areas (`docs backend
   web e2e desktop ralph compose`, about four minutes), the task's areas, the real `stack` at every
   fifth task, and the task's extra commands.
5. Only when that passes does the runner set `passes`, append to `progress.txt` and create one commit
   with a `Task: <id>` trailer, including the documentation changed with the code. Find a task's
   commit with `git log --grep='Task: P08.6'`.
6. Before each push it checks every waiting commit again for forbidden paths, secrets and authorship
   words, because the repository is public. It never uses force and pushes no tags.

## What happens when something goes wrong

| Event | Result |
| --- | --- |
| Verification fails, or the agent reports `failed` or no report | Attempt counted, no commit, work kept for the next attempt |
| Second failed attempt, or the agent reports `blocked` | Task blocked with its reason, work moved to the Git stash (`ralph blocked <id>`), independent tasks continue |
| The agent changes `prd.json`, `progress.txt` or the runner | Change undone, attempt counted |
| Usage limit | Waits up to 7 days, then repeats the iteration; no attempt counted |
| The session itself fails, such as a network outage or a lost login | Waits and repeats, with no attempt or iteration counted; the sixth failure in a row stops the run with status 3 |
| The verification hangs | Stopped at its time limit; attempt counted |
| A check runs out of memory or its test workers cannot start | That step repeats up to twice after a pause; no attempt counted. A failing test is never repeated |
| The runner is killed, or Windows restarts | `--resume` continues the same task with its work |
| The push fails for network or login | Work continues, and the next iteration tries again |
| `origin` has work this branch lacks | Run stops with status 7; never forces or rebases by itself |
| A waiting commit looks unsafe to publish | Nothing is pushed; run stops with status 8 |
| `HEAD` or the branch moves during an iteration | Run stops with status 4 |
| Only blocked or owner tasks remain | Run stops with status 5 |
| All tasks an agent can do passed | Run ends with status 0, from the verified state only; the agent's words play no part |

The other statuses: 1 iteration limit reached, 2 not started, 6 stopped on request.

A local tag `ralph-start-<date>` marks where each new run began. It is not pushed.

## Files

| File | Tracked | Purpose |
| --- | --- | --- |
| `ralph.sh`, `prd.cjs` | Yes | The loop, and the task state and rules |
| `verify.sh`, `check.sh` | Yes | What must pass before a commit |
| `sandbox.sh`, `sandbox-entry.sh`, `checks.Dockerfile` | Yes | Isolated container for project commands |
| `stack.sh`, `stack.env`, `stack.override.yml` | Yes | The real stack, under another project, ports and image tags; refuses a Compose file that reaches the host |
| `desktop-smoke.sh`, `podman-smoke.sh` | Yes | Jobs of P07.2 and P08.12: the real window and real Podman |
| `owner-check.cjs` | Yes | The guided session for P05.6 and P07-CHECK; reads numbers, never transcript text |
| `open-run.sh` | Yes | Opens a run in its own window |
| `settings.json` | Yes | What the agent may do |
| `CLAUDE.md` | Yes | Instructions of each iteration |
| `prd.json` | Yes | Tasks and their state |
| `progress.txt`, `archive/` | Yes | Recent log and reusable patterns, and the older log |
| `.state/` | No | Logs of the agents and of verification, the current task and the stop request |

## Changing the task list

`docs/ROADMAP.md` remains the source of the requirements. When an item is added, add a task to
`prd.json`; `node scripts/ralph/prd.cjs validate` fails while an open roadmap item has no task that
closes it, a dependency comes later than its task, or an identifier repeats.

A task has the upstream fields `id`, `title`, `description`, `acceptanceCriteria`, `priority`,
`passes` and `notes`, and these additions: `dependsOn`, `areas` (checks to run), `roadmapItems`,
`closes` (items it must tick), `owner` (never given to an agent), `userVisible` (needs a changelog
entry), `verify` and `verifyHost` (extra commands), `job` (`command` and `timeoutSeconds`, run before
the agent), `allowTestChanges` (for a task whose purpose is to move tests), `attempts`, `blocked`,
`blockedReason`, `lastFailure` and `verifiedAt`.

## Tests

```bash
node --test 'scripts/ralph/test/*.test.cjs'
```

`test/runner.test.cjs` runs the real `ralph.sh` in temporary repositories with a stand-in for Claude
Code, so it needs no Docker, login or usage.

## Differences from upstream

| Upstream | Here | Why |
| --- | --- | --- |
| `--dangerously-skip-permissions` | `--permission-mode dontAsk` with an allow and a deny list | The agent gets only what it needs |
| The agent commits and sets `passes` | The runner does both after its own verification | Completion comes from verified state |
| Stops on `<promise>COMPLETE</promise>` in the output | Stops when `prd.json` has no open task | The same |
| The agent picks a story by priority | The runner picks by priority and dependencies | Dependencies and blocks are enforced |
| No limit on failures | Two attempts, then a block; independent tasks continue | No endless loops |
| The agent pushes, if it wants to | Only the runner pushes, after verification and without force | The owner wants the push automatic, not unchecked |
| Needs `jq` and `claude` on `PATH` | Node.js, and the extension's binary | Nothing is installed on the computer |
| The agent reads all of `progress.txt` | A brief, and old entries move to `archive/` | The context stays small |
| Branch per feature, archived on change | Always `main`, as the owner decided | The owner's rule; the upstream archive step is removed |
| Commit subject `feat: [ID] - [Title]` | The project's imperative subject and a `Task:` trailer | [CLAUDE.md](../../CLAUDE.md) rule 18 |
