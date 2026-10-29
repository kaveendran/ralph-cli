1. Component design — properly decomposed? Missing shared components? Prop drilling risks?
2. State management — client state and server state kept apart (e.g. Zustand, React Query)? Over-fetching?
3. TypeScript — types defined before implementation? Shared types between components? No `any`?
4. Accessibility — ARIA labels planned? Keyboard navigation? Screen reader support?
5. Performance — missing memoization, large lists without virtualization, unnecessary re-renders?
6. Testing — component tests planned (e.g. Vitest + Testing Library)? User interaction and async state tested?
