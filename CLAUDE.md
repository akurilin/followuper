# Python style

- Keep it simple. Do not overengineer, do not future-proof — build only what's asked
  for now.
- Document the code: docstrings and comments that explain *why*, not *what*.
- Optimize for readability and maintainability above cleverness or extensibility.
- Avoid speculative abstractions, config knobs, and wrapper functions that add no value.
- Prefer the standard library over dependencies when reasonable.

# Reviewing conversations for follow-up

- To review the script's output and report which conversations need a follow-up, use
  `/followups` (defined in `.claude/commands/followups.md`). It holds the run command,
  the criteria for what counts as needing a follow-up, and the output format.
