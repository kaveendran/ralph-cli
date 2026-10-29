Every unit of this change request is done. Before it counts as complete, check the implementation against the spec, the way a careful reviewer would.

Use Read, Grep and Glob. Don't change anything.

## The spec's acceptance criteria

{{CRITERIA}}

The full spec is at `{{SPEC_PATH}}`; open it for the details of any criterion.

## Check
For each acceptance criterion, find the code and the test that satisfy it. A criterion is met only when both exist and the test would fail without the code.

Answer with the structured result:
- `converged`: true only if every criterion is met.
- `gaps`: one entry per criterion that isn't met, with `criterion` (its ID or a short quote) and `detail` (what is missing, specific enough to become a unit of work).
