Backend:
1. API design — RESTful, consistent naming, correct HTTP methods and status codes.
2. Data model — schema changes safe? Missing indexes? N+1 risks? Migration reversibility?
3. Async/concurrency — race conditions, missing transaction boundaries, async patterns correct?
4. Security — auth checks on all protected endpoints? Input validation? SQL injection risks?
5. Performance — missing pagination, unindexed queries, blocking calls in async paths?

Frontend:
1. Component design — properly decomposed? Missing shared components? Prop drilling?
2. State management — client state and server state kept apart? Over-fetching?
3. TypeScript — types defined upfront? No implicit `any` escapes?
4. Accessibility — ARIA labels, keyboard navigation, screen reader support planned?
5. Performance — missing memoization? Large lists without virtualization?

Contract between them:
1. Are request and response shapes defined once and shared, or duplicated by hand?
