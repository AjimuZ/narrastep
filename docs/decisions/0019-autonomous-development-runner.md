# ADR 0019: Autonomous development runner

Status: accepted (2026-10-30, requested by the owner), amended the same day (automatic push, jobs
for the former owner checks). Supersedes: none.

## Context

The owner wants the roadmap completed incrementally by Claude Code without one long session that
fills its context: every iteration starts clean, reads the persistent state, does one task, verifies
it, commits and ends. The owner named [snarktank/ralph](https://github.com/snarktank/ralph) as the
implementation, and asked that completion follow verified state and not the agent's statement, that
Claude Code's safety controls stay on, and that all work happens on `main`. The first setup did not
push; after seeing it, the owner asked for the push to be automatic.

Upstream's `ralph.sh` does not meet those conditions as it is: it runs with
`--dangerously-skip-permissions`, ends when the output says `<promise>COMPLETE</promise>`, lets the
agent set `passes`, has no dependencies or failure limit, and needs `jq` and `claude` on `PATH`. On
this computer neither is on `PATH`, nothing may be installed, the host has Python 3.15 and Node.js 24
while the project uses 3.14.10 and 22.23.1, and `docker compose` in the repository would reach the
owner's project volume.

## Decision

- Vendor upstream's runner, prompt and task format into `scripts/ralph/`, pinned to commit
  `6c53cb0b831ebe8739c6a003e22af14902d8b0b5`, and adapt them. The differences are listed in
  [the runner's README](../../scripts/ralph/README.md#differences-from-upstream).
- **The runner decides, the agent reports.** The agent has no permission to commit or edit the task
  state. It writes a report, and the runner verifies the working tree, sets `passes` and commits. A
  commit therefore exists only after its checks passed, and the run ends only when `prd.json` has no
  open task that an agent can do.
- **Limited permissions.** Each agent runs with `--permission-mode dontAsk`, an allow list in
  `scripts/ralph/settings.json`, only the project's setting sources, no MCP servers and no web tools.
- **Isolated commands.** Project commands run in a container (`sandbox.sh`) with the right versions.
  There `.git` is read-only and `.env`, `data/` and the rest of `.claude/` are hidden. The real stack
  runs as a separate Compose project with its own ports and image tags (`stack.sh`).
- **Two attempts.** A second failure blocks the task with its reason, sets its work aside in the Git
  stash and moves on to independent tasks. Usage limits and failed sessions cost no attempt.
- **Owner checks stay with the owner.** Tasks that need a real call, a display or Podman are marked
  `owner`, are never given to an agent and do not prevent completion.
- **The runner works on `main`** in the owner's working tree (owner's decision, 2026-10-30), and
  stops when `main` is behind `origin`, because the roadmap would be out of date.
- **The runner pushes `main`** after each verified commit (owner's decision, 2026-10-30). The agent
  still cannot. There is no force and there are no tags. Every waiting commit is checked again first
  for forbidden paths, secrets and authorship words. A network failure only delays the push; a
  refused push or unsafe content stops the run.
- **Long checks need no session.** A task's `job` runs before its agent, so a benchmark that takes
  hours uses no usage quota. The window (P07.2), Podman (P08.12) and the benchmark (P06.5) became
  such tasks with the owner's approval, because only a real call needs a person. Podman runs inside
  a privileged container since the owner cannot install it, so `podman machine` on Windows stays
  unverified. The call and the single-window check are one guided session,
  `scripts/ralph/owner-check.cjs`, which reads numbers from the local API and never transcript text.
- **Regressions are looked for on every task:** every task runs all the suites, the real stack runs
  at every fifth, and a change may not delete tests or add `.only`, `.skip`, `fixme` or `xfail`.
- **Other programs share the computer.** The suites run with 4 workers, and a check repeats only when
  its output shows a lack of resources, so the load of another project costs no attempt.
- **Waiting beats failing.** A usage limit waits up to 7 days. A failed session waits and repeats,
  and only six in a row stop the run, so the runner does not depend on recognising the exact
  message of a limit it has never seen.

## Alternatives

- **Upstream unchanged.** Rejected for the reasons in the context.
- **A dedicated branch or worktree.** Safer for the owner's working tree, and what the first request
  asked for; the owner decided on `main` instead.
- **Claude Code in a container.** A stronger boundary, but it would need the owner's login inside the
  container, and it could not use Docker for the real stack without giving it the Docker socket.
- **A loop inside one session.** The context fills up, which is what the owner wants to avoid.

## Consequences

- Development can continue without the owner, one verified commit per task, and stops by itself.
- **Limits of the isolation.** This is a tool policy, not a security boundary. The allowed scripts
  run containers with network access; the sandbox can read and write the whole repository except
  `.git`, `.env`, `data/` and `.claude/`. `stack.sh` refuses a Compose file with host folders,
  privileged containers or the Docker socket, but `desktop-smoke.sh` runs the repository's `start.sh`
  and Electron on the host, so code the agent wrote does run there, and `podman-smoke.sh` uses a
  privileged container inside Docker's virtual machine. The sandbox's network access is accepted: the
  repository is public, and the sandbox holds no credentials. Pattern rules for shell commands are
  best-effort. Risk K18 records this.
- **Nothing stands between a commit and the public.** Each verified commit is published at once, with
  no person reading it first, so a task that passes its checks but is wrong needs a later fix or a
  revert, and stays visible in the history. The owner should not edit this working tree during a
  run, and no other session should push to `main`, because either one stops it.
- **Rule 17 of `CLAUDE.md`** (no paid services) applies to what the project depends on. Claude Code
  needs the owner's own plan and is not a dependency: the product, its build, its tests and its CI
  do not use it, and contributors do not need it. Rule 11 is unchanged for the same reason. The agent
  sends the repository's code to the service it runs on, and the permissions and the sandbox keep
  `data/` out, so recordings and transcripts are not sent.
- Claude Code's own output can change between versions; `prd.cjs` reads only `result`, `is_error` and
  `subtype` from its stream, and a change there shows up as failed sessions, which stop the run.
