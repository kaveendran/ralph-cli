"""The review and delivery workflows around the build loop.

  critique     8 experts review a plan, a lead merges their findings into one report
  refine-plan  critique → proposed plan edits → you approve → critique again (bounded)
  retro        3 analysts look back at a finished CR; drafts release notes and a PR body
  deploy       scaffold deploy documents from the plan, critique them, refine them

They share four pieces:
  council   runs read-only expert calls in parallel (at most 4 at once)
  inputs    keeps what each expert reads bounded, however large the plan
  report    reads severities and sections out of the markdown reports
  refine    the human-gated critique → edit loop
"""
