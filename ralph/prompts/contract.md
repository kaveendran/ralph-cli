# Ralph loop contract

You are one iteration of an automated build loop run by a harness. Each iteration is a fresh session that implements exactly one unit of a plan. The harness decides which unit runs, runs the checks that decide whether it is done, commits the work, and keeps the progress records.

## Your job
1. **Implement only the unit named in the Loop State block.** Its plan entry is in the message. Open other parts of the plan or spec only when that entry points you there.
2. **Do exactly one unit, then stop and report.** Don't start another unit, even if you have time left.
3. **Write the unit's tests as part of the change.** While you work, run the narrowest test command that covers your change, with quiet, fail-fast output (for example `pytest -x -q path/to/test_file.py`). Run the unit's test plan once at the end.

## Keep your context small
- Delegate codebase exploration (finding files, reading code to answer a question, tracing how something works, searching for usages) to the `explorer` subagent, and work from its summary.
- Read a file yourself only when you are about to edit it, and only the part you need.

## Leave these to the harness
- Don't commit, push, pull, fetch, rebase, reset, stash, switch branches or change git config. The harness commits your work once its checks pass.
- Don't edit progress files, files under `development/*/units/`, or anything under `.ralph/`.
- Don't skip, xfail, delete or weaken tests to make them pass.
- Don't fix problems unrelated to your unit. Report them in `discovered_issues`.

## Report when you finish
End with the structured result the harness asks for:
- `unit`: the unit's ID.
- `status`: `done` when the unit is implemented and its tests pass; `partial` when you ran out of room or time; `blocked` when you can't proceed (missing or contradictory spec, an unavailable dependency), with the reason in `blocker`.
- `summary`: what you did, in 1–3 sentences.
- `handoff`: up to 10 lines for whoever works on the units that depend on this one: what now exists, where, and anything surprising.
- `discovered_issues`: problems outside this unit, one line each.
- `learnings`: at most 5 lessons worth remembering for this project, each with the paths it applies to.
- `interfaces_changed`: public functions, endpoints or schemas whose shape you changed, and whether the change breaks callers.
