# Project principles

Every unit is built and reviewed against these: a change that breaks one isn't accepted. Keep them few (5–9), short and checkable, and change them on purpose, since each change applies to every unit from then on.

1. Every behaviour change comes with a test that fails without it.
2. No secrets in code, logs or test fixtures.
3. Public interfaces change only by adding to them, unless the spec says otherwise.
4. Errors are handled where they happen or passed on explicitly; never swallowed.
5. Code follows the conventions already in the codebase; a new convention needs a reason in the spec.
