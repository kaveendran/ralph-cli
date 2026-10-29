"""Project memory that lives in git: lessons, their promotion, size budgets, decay.

The lifecycle (docs/meta-harness-plan.md §8.2):
  capture  — the agent reports up to 5 lessons per unit (.ralph/learnings/inbox.jsonl);
  use      — lessons whose paths match a unit's files go into its context pack;
  promote  — a lesson that recurs becomes a rule, a CLAUDE.md line or a skill,
             after you approve it (`ralph memory review`);
  prune    — size budgets are checked every run, and old notes are summarised.
"""
