You are drafting Claude Code skills for one project. A human will review every line before it is used.

A skill is loaded into an AI coding agent's context only when it's relevant, so it must hold what the agent doesn't already know. Assume the agent knows mainstream languages and frameworks well. Write only:
- this project's conventions and choices (which libraries for what, file layout, naming, patterns to follow);
- details specific to the versions below, especially behaviour that changed recently;
- gotchas: mistakes that are easy to make here.
Don't write general tutorials or explain how a framework works.

## The project

{{PROJECT_MAP}}

## Its stack

{{STACK}}

## Versions in use (from its lock files and manifests)

{{VERSIONS}}

## Write up to {{MAX_SKILLS}} skills

One per major part of the stack (e.g. the backend framework, the frontend framework, the database layer), and only where there is something project-specific to say. For each:
- `name`: lowercase words joined by hyphens, e.g. `fastapi-conventions`;
- `description`: one sentence in the third person, under 250 characters, saying what it covers and when to use it (e.g. "Project conventions for FastAPI endpoints: routing, schemas, errors. Use when adding or changing an endpoint.");
- `body`: markdown, under 120 lines, with short concrete rules and small examples;
- `packages`: the package names (from the versions list) the skill depends on.

Where you don't know the project's choice, say so as a short "Decide:" line for the human rather than inventing one.
