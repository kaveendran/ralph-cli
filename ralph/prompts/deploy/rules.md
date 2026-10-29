SCAFFOLD RULES — read carefully:
- DO fill in everything that is derivable from the plan: unit order, unit types, per-unit deploy intent, rollback procedure text (each unit's "Rollback Plan" section is given to you verbatim).
- DO NOT invent environment specifics. These MUST appear as explicit `<TODO: ...>` markers for the human to fill:
    * Cluster / region names
    * Secrets manager paths
    * Service URLs / endpoints
    * Monitoring dashboard URLs
    * Feature-flag service identifiers
    * On-call rotation contact / escalation path
    * Maintenance windows / quiet hours
    * Traffic percentages for canary stages
    * Database connection strings
    * Specific Kubernetes namespaces / contexts
- If a unit has Type: migration (or database), its deploy step MUST flag online vs offline, lock duration and reversibility — leave the answer as `<TODO: ...>` if it isn't derivable.
- If a unit has Type: api with breaking changes, its deploy step MUST reference a versioning / deprecation path.
- If a unit introduces a feature flag, its deploy step MUST list the default state and kill-switch path as `<TODO: ...>`.
- Keep unit IDs everywhere. Cross-reference by ID, not by name.
- No prose padding: bullet lists, tables and headed sections only.
- Return the complete markdown file as `document`.
