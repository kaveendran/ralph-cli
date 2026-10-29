"""Working with teammates, by hand (docs/meta-harness-plan.md §11).

  ledger      — one entry per finished unit, committed with it: what changed and by whom
  interfaces  — a deterministic check of which public functions a change removed or reshaped
  impact      — how teammates' entries affect a unit: CONFLICT, BREAKING, OVERLAP or INFO
  shared      — the shared branch as of your last fetch (local refs only)
  remote      — fetch and push; the only module that touches the network

The loop never imports `remote`, so it never contacts the remote: what it
knows about teammates is what your last `ralph sync` brought in.
"""
