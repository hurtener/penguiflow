# Contributing to Playground UI

## Component Guidelines

### Choose the Right Category
- `components/primitives`: small, reusable atoms (buttons, badges).
- `components/composites`: composed UI blocks (cards, tabs, empty states).
- `components/containers`: layout scaffolding (page, columns).
- `components/features/*`: feature slices (chat, setup, trajectory, sidebars, AG-UI).
- `renderers/`: rich UI renderers (charts, markdown, forms).

### Authoring Rules
- Props: required first, optional with defaults.
- Events: use callback props (e.g. `onResult`), no `createEventDispatcher`.
- State: prefer `$derived` over `$effect` where possible.
- Error handling: render graceful fallbacks, never throw unhandled errors.
- Keep components focused; split large components into smaller pieces.

### Scaffold a Component
Use the generator script to create a component + unit test scaffold:

```bash
./scripts/new-component.sh Button primitives
```

## Store Pattern

All stores are context-based factories:
- Create store with `createXStore()`.
- Provide via `setXStore()` inside a host component (or `initStores()` in `App.svelte`).
- Consume via `getXStore()` inside feature components.
- No module-level singletons.

## Communication Patterns

- Components talk to parents via callback props.
- Stores are read/write via context only.
- Services accept injected stores; avoid importing stores directly in services.

## Testing

```bash
npm run test          # Unit tests
npm run test:coverage # Coverage
npm run test:e2e      # Playwright E2E
```

Testing tips:
- Use host components to set store context when rendering Svelte components.
- Keep unit tests pure; mock network and external SDKs.
- Cover negative/error paths for new features.

## Performance

- Lazy-load heavy renderers (ECharts, Mermaid, Plotly, KaTeX).
- Avoid large JSON blobs in reactive state.
- Prefer derived state to recomputing in templates.

## ComponentLab

`ComponentLab` is the live documentation hub. Add new renderers to the registry
and include examples in the registry payload so they appear in the lab.

## Pre-commit Hooks (Opt-in)
To enable the local pre-commit hook:

```bash
git config core.hooksPath scripts/hooks
```

This runs `npm run precommit` on commit.
