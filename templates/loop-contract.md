# Ralph loop contract

You are running inside an automated loop. Every iteration is a fresh session that works on exactly one unit of the plan.

1. **Work only on the unit named in the Loop State block** ("This iteration's unit"). Its complete plan entry is included in the message under "Your unit". You don't need to read the rest of the plan or the whole progress file; open other parts only when your unit's entry points you there.
2. **Do exactly one unit, then stop.** When it is implemented, its tests pass, the progress file is updated and the change is committed, end the session. Don't start the next unit, even if you have time left; the next iteration will.
3. **Keep your context small.**
   - Delegate codebase exploration (finding files, reading code to answer a question, tracing how something works, searching for usages) to the `explorer` subagent, and work from its summary.
   - Read a file yourself only when you are about to edit it, and read only the part you need.
   - While iterating, run the narrowest test command that covers your change, with quiet, fail-fast output (for example `pytest -x -q path/to/test_file.py`). Run the unit's full test plan once at the end.
4. **Don't fix unrelated problems.** If you find failures or issues outside your unit, record them in the progress file under your unit's notes as "Discovered:" items, and leave them for a later iteration.
5. **If you are blocked** (the spec is missing or contradictory, or an external dependency isn't available), mark the unit `[!]` with the reason, commit the progress file, and stop.
6. **Don't push.** Publishing is done by the developer.
