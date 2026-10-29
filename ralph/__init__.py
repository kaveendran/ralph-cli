"""ralph — a meta-harness that runs Claude Code one bounded unit at a time.

The harness owns everything deterministic: which unit runs, what context the
agent sees, the limits on each run, the test gates, the commits and the
records. The agent owns the creative step: implementing one unit.
"""

__version__ = "0.2.0.dev0"
