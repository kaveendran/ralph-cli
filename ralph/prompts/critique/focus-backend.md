1. API design — RESTful, consistent naming, correct HTTP methods and status codes.
2. Data model — schema changes safe? Missing indexes? N+1 query risks? Migration reversibility?
3. Async/concurrency — race conditions, missing transaction boundaries, async patterns correct?
4. Security — auth checks on all protected endpoints? Input validation (e.g. Pydantic)? SQL injection?
5. Performance — missing pagination, unindexed queries, synchronous blocking calls in async paths?
6. Testing — async tests used correctly? Test database isolation? Fixtures properly scoped?
