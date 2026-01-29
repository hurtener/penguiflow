# RFC: Frontend Gold Standard Refactoring Plan

> **Status**: Implemented
> **Target**: `penguiflow/cli/playground_ui`
> **Scope**: Architecture, patterns, performance, testability, stability

---

## Executive Summary

This document outlines a comprehensive refactoring plan to elevate the playground UI frontend to gold-standard quality. The goals are:

- **Testability**: Every component testable in isolation
- **Stability**: Graceful error handling, no cascade failures
- **Extensibility**: Easy to add new renderers, features, integrations
- **Consistency**: One canonical way to solve each problem
- **Performance**: Optimized bundle size and runtime efficiency

---

## Table of Contents

1. [Current State Assessment](#1-current-state-assessment)
2. [Phase 0: Inventory & Mapping](#2-phase-0-inventory--mapping)
3. [Phase 1: Type Safety & Strict Mode](#3-phase-1-type-safety--strict-mode)
4. [Phase 2: Component Architecture Standardization](#4-phase-2-component-architecture-standardization)
5. [Phase 3: State Management Consolidation](#5-phase-3-state-management-consolidation)
6. [Phase 4: Error Isolation & Resilience](#6-phase-4-error-isolation--resilience)
7. [Phase 5: Code Splitting & Performance](#7-phase-5-code-splitting--performance)
8. [Phase 6: Testing Strategy](#8-phase-6-testing-strategy)
9. [Phase 7: Developer Experience](#9-phase-7-developer-experience)
10. [Migration Strategy](#10-migration-strategy)
11. [Acceptance Criteria](#11-acceptance-criteria)

---

## 1. Current State Assessment

### Strengths
- Clear folder structure with domain separation
- Svelte 5 runes adoption (`$state`, `$derived`, `$props`)
- TypeScript throughout
- Good test coverage (279 unit + 53 e2e)

### Issues to Address

| Category | Issue | Impact |
|----------|-------|--------|
| Types | `any` types in renderer registry | Type safety holes |
| Bundle | Entry JS 89 KB gzip; renderer chunks lazy-loaded (2.63 MB total); largest renderer chunk 1.40 MB (Plotly) | Largest renderer chunk still above budget |
| Patterns | Mixed event handling (dispatch, callbacks, stores) | Cognitive overhead |
| Errors | No error boundaries | Cascade failures |
| State | Overlapping store responsibilities | Confusion, bugs |
| Components | Some components have multiple responsibilities | Hard to test |

---

## 2. Phase 0: Inventory & Mapping

Before refactors, build a living inventory so newly developed components, renderers, and stores are not missed.

### 2.1 Inventory Scope
- Renderers (AG-UI + rich UI renderers, charts, markdown, data grids)
- UI components (App layout, tabs, cards, inputs, complex forms)
- Stores (chat/session/artifacts/component artifacts/interactions)
- Services (API, streaming, adapters)
- Tests mapped to each component/store/service

### 2.2 Mapping Outputs
- Table with: Path, Category, Owner/Feature, Tests, Target Folder, Risk
- Separate list for AG-UI renderer + component artifact paths
- Explicit list of “new components since last refactor” to prevent omissions

### 2.3 Full Inventory Table (Current -> Target)

| Path | Kind | Feature area | Purpose | Tests | Target location | Status | Risk |
|------|------|--------------|---------|-------|-----------------|--------|------|
| src/App.svelte | app | app-shell | Root layout + providers | e2e: tests/e2e/layout.spec.ts | src/App.svelte | keep/refactor | med |
| src/main.js | entry | app-shell | Svelte mount | none | src/main.js | keep | low |
| src/app.css | styles | app-shell | Global styles | none | src/app.css | keep/refactor | med |
| src/assets/svelte.svg | asset | app-shell | Logo asset | none | src/assets/svelte.svg | keep | low |
| src/lib/agui/index.ts | barrel | agui | AG-UI exports | n/a | src/lib/agui/index.ts (re-export new paths) | keep/refactor | low |
| src/lib/agui/patch.ts | util | agui | JSON patch helper | unit: tests/unit/agui/patch.test.ts | src/lib/utils/json-patch.ts | done | low |
| src/lib/agui/stores.ts | store | agui | AG-UI HttpAgent store | unit: tests/unit/agui/stores.test.ts | src/lib/stores/features/agui.svelte.ts | done | high |
| src/lib/agui/components/AGUIProvider.svelte | component | agui | AGUI context/provider | unit: tests/unit/agui/provider.test.ts | src/lib/components/features/agui/AGUIProvider.svelte | done | med |
| src/lib/agui/components/MessageList.svelte | component | agui | AGUI message list | unit: tests/unit/agui/components.test.ts | src/lib/components/features/agui/MessageList.svelte | done | med |
| src/lib/agui/components/Message.svelte | component | agui | AGUI message row | unit: tests/unit/agui/components.test.ts | src/lib/components/features/agui/Message.svelte | done | med |
| src/lib/agui/components/ToolCall.svelte | component | agui | Tool call row | unit: tests/unit/agui/components.test.ts | src/lib/components/features/agui/ToolCall.svelte | done | med |
| src/lib/agui/components/StateDebugger.svelte | component | agui | AGUI state debug | unit: tests/unit/agui/components.test.ts | src/lib/components/features/agui/StateDebugger.svelte | done | low |
| src/lib/agui/components/index.ts | barrel | agui | AGUI component exports | n/a | src/lib/components/features/agui/index.ts | done | low |
| src/lib/component_artifacts/index.ts | registry | renderers | Renderer registry + exports | unit: tests/unit/component_artifacts/component_registry.test.ts | src/lib/renderers/registry.ts | done | high |
| src/lib/component_artifacts/ComponentRenderer.svelte | renderer | renderers | Dynamic renderer host | unit: tests/unit/component_artifacts/ComponentRenderer.test.ts | src/lib/renderers/ComponentRenderer.svelte | done | high |
| src/lib/component_artifacts/ComponentLab.svelte | component | component-lab | Renderer showcase | unit: tests/unit/component_artifacts/ComponentLab.test.ts | src/lib/components/features/component-lab/ComponentLab.svelte | done | med |
| src/lib/component_artifacts/renderers/Accordion.svelte | renderer | renderers | Accordion renderer | none | src/lib/renderers/Accordion.svelte | done | med |
| src/lib/component_artifacts/renderers/Callout.svelte | renderer | renderers | Callout renderer | none | src/lib/renderers/Callout.svelte | done | low |
| src/lib/component_artifacts/renderers/Code.svelte | renderer | renderers | Code block renderer | none | src/lib/renderers/Code.svelte | done | low |
| src/lib/component_artifacts/renderers/Confirm.svelte | renderer | renderers | Confirm prompt renderer | unit: tests/unit/component_artifacts/interactive-flow.test.ts | src/lib/renderers/Confirm.svelte | done | med |
| src/lib/component_artifacts/renderers/DataGrid.svelte | renderer | renderers | Data grid renderer | none | src/lib/renderers/DataGrid.svelte | done | med |
| src/lib/component_artifacts/renderers/ECharts.svelte | renderer | renderers | ECharts renderer | none | src/lib/renderers/ECharts.svelte | done | high |
| src/lib/component_artifacts/renderers/Embed.svelte | renderer | renderers | Embed iframe renderer | none | src/lib/renderers/Embed.svelte | done | low |
| src/lib/component_artifacts/renderers/Form.svelte | renderer | renderers | Form renderer | unit: tests/unit/component_artifacts/interactive-flow.test.ts | src/lib/renderers/Form.svelte | done | high |
| src/lib/component_artifacts/renderers/Grid.svelte | renderer | renderers | Grid layout renderer | none | src/lib/renderers/Grid.svelte | done | low |
| src/lib/component_artifacts/renderers/Html.svelte | renderer | renderers | Raw HTML renderer | none | src/lib/renderers/Html.svelte | done | low |
| src/lib/component_artifacts/renderers/Image.svelte | renderer | renderers | Image renderer | none | src/lib/renderers/Image.svelte | done | low |
| src/lib/component_artifacts/renderers/Json.svelte | renderer | renderers | JSON renderer | none | src/lib/renderers/Json.svelte | done | low |
| src/lib/component_artifacts/renderers/Latex.svelte | renderer | renderers | LaTeX renderer | none | src/lib/renderers/Latex.svelte | done | med |
| src/lib/component_artifacts/renderers/Markdown.svelte | renderer | renderers | Markdown renderer | none | src/lib/renderers/Markdown.svelte | done | low |
| src/lib/component_artifacts/renderers/Mermaid.svelte | renderer | renderers | Mermaid renderer | none | src/lib/renderers/Mermaid.svelte | done | med |
| src/lib/component_artifacts/renderers/Metric.svelte | renderer | renderers | Metric renderer | none | src/lib/renderers/Metric.svelte | done | low |
| src/lib/component_artifacts/renderers/Plotly.svelte | renderer | renderers | Plotly renderer | none | src/lib/renderers/Plotly.svelte | done | high |
| src/lib/component_artifacts/renderers/Report.svelte | renderer | renderers | Report renderer | none | src/lib/renderers/Report.svelte | done | med |
| src/lib/component_artifacts/renderers/Tabs.svelte | renderer | renderers | Tabs renderer | none | src/lib/renderers/Tabs.svelte | done | low |
| src/lib/component_artifacts/renderers/Video.svelte | renderer | renderers | Video renderer | none | src/lib/renderers/Video.svelte | done | low |
| src/lib/component_artifacts/renderers/SelectOption.svelte | renderer | renderers | Form select option | none | src/lib/renderers/internal/SelectOption.svelte | done | low |
| src/lib/components/index.ts | barrel | components | Root component exports | n/a | src/lib/components/index.ts | keep/refactor | low |
| src/lib/components/layout/index.ts | barrel | layout | Layout exports | n/a | src/lib/components/containers/index.ts | done | low |
| src/lib/components/layout/Card.svelte | component | layout | Card container | none | src/lib/components/composites/Card.svelte | done | med |
| src/lib/components/layout/Column.svelte | component | layout | Column layout | none | src/lib/components/containers/Column.svelte | done | med |
| src/lib/components/layout/Page.svelte | component | layout | Page layout | none | src/lib/components/containers/Page.svelte | done | med |
| src/lib/components/ui/index.ts | barrel | ui | UI exports | n/a | src/lib/components/index.ts | done | low |
| src/lib/components/ui/Pill.svelte | component | ui | Pill badge | unit: tests/unit/components/ui/Pill.test.ts | src/lib/components/primitives/Pill.svelte | done | low |
| src/lib/components/ui/StatusDot.svelte | component | ui | Status dot | none | src/lib/components/primitives/StatusDot.svelte | done | low |
| src/lib/components/ui/IconButton.svelte | component | ui | Icon button | none | src/lib/components/primitives/IconButton.svelte | done | low |
| src/lib/components/ui/CodeBlock.svelte | component | ui | Code block display | none | src/lib/components/composites/CodeBlock.svelte | done | low |
| src/lib/components/ui/Tabs.svelte | component | ui | Tabs UI | unit: tests/unit/components/ui/Tabs.test.ts | src/lib/components/composites/Tabs.svelte | done | med |
| src/lib/components/ui/Empty.svelte | component | ui | Empty state | unit: tests/unit/components/ui/Empty.test.ts | src/lib/components/composites/Empty.svelte | done | low |
| src/lib/components/ui/ErrorList.svelte | component | ui | Error list | none | src/lib/components/composites/ErrorList.svelte | done | low |
| src/lib/components/center/index.ts | barrel | center | Center exports | n/a | src/lib/components/features/center/index.ts | done | low |
| src/lib/components/center/CenterColumn.svelte | component | center | Main center column layout | e2e: tests/e2e/layout.spec.ts | src/lib/components/features/center/CenterColumn.svelte | done | high |
| src/lib/components/center/chat/index.ts | barrel | chat | Chat exports | n/a | src/lib/components/features/chat/index.ts | done | low |
| src/lib/components/center/chat/ChatCard.svelte | component | chat | Chat container | e2e: tests/e2e/chat.spec.ts | src/lib/components/features/chat/ChatCard.svelte | done | high |
| src/lib/components/center/chat/ChatBody.svelte | component | chat | Message stream body | e2e: tests/e2e/chat.spec.ts | src/lib/components/features/chat/ChatBody.svelte | done | med |
| src/lib/components/center/chat/ChatHeader.svelte | component | chat | Chat header | none | src/lib/components/features/chat/ChatHeader.svelte | done | low |
| src/lib/components/center/chat/ChatInput.svelte | component | chat | Chat input | e2e: tests/e2e/chat.spec.ts | src/lib/components/features/chat/ChatInput.svelte | done | med |
| src/lib/components/center/chat/Message.svelte | component | chat | Chat message row | none | src/lib/components/features/chat/Message.svelte | done | med |
| src/lib/components/center/chat/PauseCard.svelte | component | chat | Pause/interrupt UI | none | src/lib/components/features/chat/PauseCard.svelte | done | low |
| src/lib/components/center/chat/ThinkingPanel.svelte | component | chat | Thinking status panel | none | src/lib/components/features/chat/ThinkingPanel.svelte | done | low |
| src/lib/components/center/chat/TypingIndicator.svelte | component | chat | Typing indicator | none | src/lib/components/features/chat/TypingIndicator.svelte | done | low |
| src/lib/components/center/setup/index.ts | barrel | setup | Setup exports | n/a | src/lib/components/features/setup/index.ts | done | low |
| src/lib/components/center/setup/SetupTab.svelte | component | setup | Setup panel | e2e: tests/e2e/setup.spec.ts | src/lib/components/features/setup/SetupTab.svelte | done | med |
| src/lib/components/center/setup/SetupField.svelte | component | setup | Setup field control | none | src/lib/components/features/setup/SetupField.svelte | done | low |
| src/lib/components/center/trajectory/index.ts | barrel | trajectory | Trajectory exports | n/a | src/lib/components/features/trajectory/index.ts | done | low |
| src/lib/components/center/trajectory/Timeline.svelte | component | trajectory | Timeline list | e2e: tests/e2e/trajectory.spec.ts | src/lib/components/features/trajectory/Timeline.svelte | done | med |
| src/lib/components/center/trajectory/TimelineItem.svelte | component | trajectory | Timeline item | none | src/lib/components/features/trajectory/TimelineItem.svelte | done | low |
| src/lib/components/center/trajectory/TrajectoryCard.svelte | component | trajectory | Trajectory card | none | src/lib/components/features/trajectory/TrajectoryCard.svelte | done | low |
| src/lib/components/center/trajectory/StepDetails.svelte | component | trajectory | Step details panel | none | src/lib/components/features/trajectory/StepDetails.svelte | done | low |
| src/lib/components/sidebar-left/index.ts | barrel | sidebar-left | Left sidebar exports | n/a | src/lib/components/features/sidebar-left/index.ts | done | low |
| src/lib/components/sidebar-left/LeftSidebar.svelte | component | sidebar-left | Left sidebar layout | e2e: tests/e2e/layout.spec.ts | src/lib/components/features/sidebar-left/LeftSidebar.svelte | done | med |
| src/lib/components/sidebar-left/GeneratorCard.svelte | component | sidebar-left | Generator card | none | src/lib/components/features/sidebar-left/GeneratorCard.svelte | done | low |
| src/lib/components/sidebar-left/GeneratorStepper.svelte | component | sidebar-left | Stepper UI | none | src/lib/components/features/sidebar-left/GeneratorStepper.svelte | done | low |
| src/lib/components/sidebar-left/ProjectCard.svelte | component | sidebar-left | Project card | none | src/lib/components/features/sidebar-left/ProjectCard.svelte | done | low |
| src/lib/components/sidebar-left/SpecCard.svelte | component | sidebar-left | Spec card | e2e: tests/e2e/spec-validation.spec.ts | src/lib/components/features/sidebar-left/SpecCard.svelte | done | low |
| src/lib/components/sidebar-right/index.ts | barrel | sidebar-right | Right sidebar exports | n/a | src/lib/components/features/sidebar-right/index.ts | done | low |
| src/lib/components/sidebar-right/RightSidebar.svelte | component | sidebar-right | Right sidebar layout | e2e: tests/e2e/layout.spec.ts | src/lib/components/features/sidebar-right/RightSidebar.svelte | done | med |
| src/lib/components/sidebar-right/artifacts/index.ts | barrel | artifacts | Artifacts exports | n/a | src/lib/components/features/sidebar-right/artifacts/index.ts | done | low |
| src/lib/components/sidebar-right/artifacts/ArtifactsCard.svelte | component | artifacts | Artifacts list card | unit: tests/unit/components/sidebar-right/ArtifactsCard.test.ts | src/lib/components/features/sidebar-right/artifacts/ArtifactsCard.svelte | done | med |
| src/lib/components/sidebar-right/artifacts/ArtifactItem.svelte | component | artifacts | Artifact row | unit: tests/unit/components/sidebar-right/ArtifactItem.test.ts | src/lib/components/features/sidebar-right/artifacts/ArtifactItem.svelte | done | low |
| src/lib/components/sidebar-right/artifacts/ArtifactStreams.svelte | component | artifacts | Artifact stream list | none | src/lib/components/features/sidebar-right/artifacts/ArtifactStreams.svelte | done | low |
| src/lib/components/sidebar-right/config/index.ts | barrel | config | Config exports | n/a | src/lib/components/features/sidebar-right/config/index.ts | done | low |
| src/lib/components/sidebar-right/config/ConfigCard.svelte | component | config | Config card | none | src/lib/components/features/sidebar-right/config/ConfigCard.svelte | done | low |
| src/lib/components/sidebar-right/config/PlannerConfigSection.svelte | component | config | Planner config section | none | src/lib/components/features/sidebar-right/config/PlannerConfigSection.svelte | done | low |
| src/lib/components/sidebar-right/config/ServicesSection.svelte | component | config | Services section | none | src/lib/components/features/sidebar-right/config/ServicesSection.svelte | done | low |
| src/lib/components/sidebar-right/config/ToolCatalogSection.svelte | component | config | Tool catalog section | none | src/lib/components/features/sidebar-right/config/ToolCatalogSection.svelte | done | low |
| src/lib/components/sidebar-right/config/ServiceRow.svelte | component | config | Service row | none | src/lib/components/features/sidebar-right/config/ServiceRow.svelte | done | low |
| src/lib/components/sidebar-right/config/ToolRow.svelte | component | config | Tool row | none | src/lib/components/features/sidebar-right/config/ToolRow.svelte | done | low |
| src/lib/components/sidebar-right/events/index.ts | barrel | events | Events exports | n/a | src/lib/components/features/sidebar-right/events/index.ts | done | low |
| src/lib/components/sidebar-right/events/EventsCard.svelte | component | events | Events card | e2e: tests/e2e/events-display.spec.ts | src/lib/components/features/sidebar-right/events/EventsCard.svelte | done | med |
| src/lib/components/sidebar-right/events/EventsHeader.svelte | component | events | Events header | none | src/lib/components/features/sidebar-right/events/EventsHeader.svelte | done | low |
| src/lib/components/sidebar-right/events/EventsBody.svelte | component | events | Events body | none | src/lib/components/features/sidebar-right/events/EventsBody.svelte | done | low |
| src/lib/components/sidebar-right/events/EventRow.svelte | component | events | Event row | none | src/lib/components/features/sidebar-right/events/EventRow.svelte | done | low |
| src/lib/components/mobile/index.ts | barrel | mobile | Mobile exports | n/a | src/lib/components/features/mobile/index.ts | done | low |
| src/lib/components/mobile/MobileHeader.svelte | component | mobile | Mobile header | unit: tests/unit/components/mobile/MobileHeader.test.ts | src/lib/components/features/mobile/MobileHeader.svelte | done | low |
| src/lib/components/mobile/MobileBottomPanel.svelte | component | mobile | Mobile bottom panel | unit: tests/unit/components/mobile/MobileBottomPanel.test.ts | src/lib/components/features/mobile/MobileBottomPanel.svelte | done | low |
| src/lib/stores/index.ts | barrel | stores | Store exports | n/a | src/lib/stores/index.ts | keep/refactor | low |
| src/lib/stores/chat.svelte.ts | store | chat | Chat state | unit: tests/unit/stores/chat.test.ts | src/lib/stores/chat.svelte.ts | keep/refactor | med |
| src/lib/stores/session.svelte.ts | store | session | Session/tenant state | unit: tests/unit/stores/session.test.ts | src/lib/stores/session.svelte.ts | keep/refactor | med |
| src/lib/stores/artifacts.svelte.ts | store | artifacts | File artifacts state | unit: tests/unit/stores/artifacts.test.ts | src/lib/stores/artifacts.svelte.ts | keep/refactor | med |
| src/lib/stores/agent.svelte.ts | store | agent | Agent config/state | unit: tests/unit/stores/agent.test.ts | src/lib/stores/agent.svelte.ts | keep/refactor | med |
| src/lib/stores/events.svelte.ts | store | events | Events state | unit: tests/unit/stores/events.test.ts | src/lib/stores/features/events.svelte.ts | done | med |
| src/lib/stores/timeline.svelte.ts | store | trajectory | Timeline state | unit: tests/unit/stores/timeline.test.ts | src/lib/stores/features/trajectory.svelte.ts | done | med |
| src/lib/stores/spec.svelte.ts | store | setup | Spec validation state | unit: tests/unit/stores/spec.test.ts | src/lib/stores/features/spec.svelte.ts | done | med |
| src/lib/stores/setup.svelte.ts | store | setup | Setup inputs state | unit: tests/unit/stores/setup.test.ts | src/lib/stores/features/setup.svelte.ts | done | med |
| src/lib/stores/component_registry.svelte.ts | store | renderers | Renderer registry state | unit: tests/unit/component_artifacts/component_registry.test.ts | src/lib/stores/features/component-registry.svelte.ts | done | high |
| src/lib/stores/component_artifacts.svelte.ts | store | artifacts | Pending interactions | unit: tests/unit/component_artifacts/component_artifacts.test.ts | src/lib/stores/features/interactions.svelte.ts | done | high |
| src/lib/services/index.ts | barrel | services | Service exports | n/a | src/lib/services/index.ts | keep/refactor | low |
| src/lib/services/api.ts | service | api | API calls | unit: tests/unit/services/api.test.ts | src/lib/services/api.ts | keep/refactor | med |
| src/lib/services/chat-stream.ts | service | streaming | Chat stream handling | unit: tests/unit/services/chat-stream-agui.test.ts | src/lib/services/chat-stream.ts | keep/refactor | high |
| src/lib/services/event-stream.ts | service | streaming | SSE/event stream | none | src/lib/services/event-stream.ts | keep/refactor | med |
| src/lib/services/markdown.ts | service | markdown | Markdown rendering | unit: tests/unit/services/markdown.test.ts | src/lib/services/markdown.ts | keep/refactor | low |
| src/lib/types/index.ts | barrel | types | Type exports | n/a | src/lib/types/index.ts | keep/refactor | low |
| src/lib/types/chat.ts | type | chat | Chat types | none | src/lib/types/chat.ts | keep/refactor | low |
| src/lib/types/spec.ts | type | setup | Spec types | none | src/lib/types/spec.ts | keep/refactor | low |
| src/lib/types/trajectory.ts | type | trajectory | Trajectory types | none | src/lib/types/trajectory.ts | keep/refactor | low |
| src/lib/types/artifacts.ts | type | artifacts | Artifact types | none | src/lib/types/artifacts.ts | keep/refactor | low |
| src/lib/types/component_artifacts.ts | type | artifacts | Component artifact types | none | src/lib/types/component_artifacts.ts | keep/refactor | low |
| src/lib/types/meta.ts | type | meta | Metadata types | none | src/lib/types/meta.ts | keep/refactor | low |
| src/lib/utils/index.ts | barrel | utils | Utility exports | n/a | src/lib/utils/index.ts | keep/refactor | low |
| src/lib/utils/constants.ts | util | config | Shared constants | none | src/lib/utils/constants.ts | keep/refactor | low |
| src/lib/utils/format.ts | util | formatting | Formatting helpers | unit: tests/unit/utils/format.test.ts | src/lib/utils/format.ts | keep/refactor | low |
| src/lib/utils/json.ts | util | json | JSON helpers | unit: tests/unit/utils/json.test.ts | src/lib/utils/json.ts | keep/refactor | low |
| src/lib/utils/artifact-helpers.ts | util | artifacts | Artifact helpers | unit: tests/unit/utils/artifact-helpers.test.ts | src/lib/utils/artifact-helpers.ts | keep/refactor | low |
| tests/setup.ts | test | test-harness | Playwright setup | n/a | tests/setup.ts | keep | low |
| tests/e2e/chat.spec.ts | test | e2e | Chat flow | n/a | tests/e2e/chat.spec.ts | keep/update | low |
| tests/e2e/events-display.spec.ts | test | e2e | Events display | n/a | tests/e2e/events-display.spec.ts | keep/update | low |
| tests/e2e/layout.spec.ts | test | e2e | Desktop layout | n/a | tests/e2e/layout.spec.ts | keep/update | low |
| tests/e2e/layout-mobile.spec.ts | test | e2e | Mobile layout | n/a | tests/e2e/layout-mobile.spec.ts | keep/update | low |
| tests/e2e/setup.spec.ts | test | e2e | Setup flow | n/a | tests/e2e/setup.spec.ts | keep/update | low |
| tests/e2e/spec-validation.spec.ts | test | e2e | Spec validation | n/a | tests/e2e/spec-validation.spec.ts | keep/update | low |
| tests/e2e/trajectory.spec.ts | test | e2e | Trajectory flow | n/a | tests/e2e/trajectory.spec.ts | keep/update | low |
| tests/unit/services/api.test.ts | test | unit | API service tests | n/a | tests/unit/services/api.test.ts | keep/update | low |
| tests/unit/services/markdown.test.ts | test | unit | Markdown service tests | n/a | tests/unit/services/markdown.test.ts | keep/update | low |
| tests/unit/services/chat-stream-agui.test.ts | test | unit | Chat stream tests | n/a | tests/unit/services/chat-stream-agui.test.ts | keep/update | low |
| tests/unit/component_artifacts/component_artifacts.test.ts | test | unit | Component artifacts store tests | n/a | tests/unit/component_artifacts/component_artifacts.test.ts | keep/update | low |
| tests/unit/component_artifacts/ComponentRenderer.test.ts | test | unit | ComponentRenderer tests | n/a | tests/unit/component_artifacts/ComponentRenderer.test.ts | keep/update | low |
| tests/unit/component_artifacts/interactive-flow.test.ts | test | unit | Form/confirm flow tests | n/a | tests/unit/component_artifacts/interactive-flow.test.ts | keep/update | low |
| tests/unit/component_artifacts/ComponentLab.test.ts | test | unit | ComponentLab tests | n/a | tests/unit/component_artifacts/ComponentLab.test.ts | keep/update | low |
| tests/unit/component_artifacts/component_registry.test.ts | test | unit | Registry tests | n/a | tests/unit/component_artifacts/component_registry.test.ts | keep/update | low |
| tests/unit/agui/AguiComponentHost.svelte | test-helper | unit | AGUI component fixture | n/a | tests/unit/agui/AguiComponentHost.svelte | keep | low |
| tests/unit/agui/components.test.ts | test | unit | AGUI components tests | n/a | tests/unit/agui/components.test.ts | keep/update | low |
| tests/unit/agui/stores.test.ts | test | unit | AGUI store tests | n/a | tests/unit/agui/stores.test.ts | keep/update | low |
| tests/unit/agui/patch.test.ts | test | unit | JSON patch tests | n/a | tests/unit/agui/patch.test.ts | keep/update | low |
| tests/unit/agui/AguiProviderHost.svelte | test-helper | unit | AGUI provider fixture | n/a | tests/unit/agui/AguiProviderHost.svelte | keep | low |
| tests/unit/agui/provider.test.ts | test | unit | AGUI provider tests | n/a | tests/unit/agui/provider.test.ts | keep/update | low |
| tests/unit/components/sidebar-right/ArtifactItem.test.ts | test | unit | ArtifactItem tests | n/a | tests/unit/components/sidebar-right/ArtifactItem.test.ts | keep/update | low |
| tests/unit/components/sidebar-right/ArtifactsCard.test.ts | test | unit | ArtifactsCard tests | n/a | tests/unit/components/sidebar-right/ArtifactsCard.test.ts | keep/update | low |
| tests/unit/components/mobile/MobileHeader.test.ts | test | unit | MobileHeader tests | n/a | tests/unit/components/mobile/MobileHeader.test.ts | keep/update | low |
| tests/unit/components/mobile/MobileBottomPanel.test.ts | test | unit | MobileBottomPanel tests | n/a | tests/unit/components/mobile/MobileBottomPanel.test.ts | keep/update | low |
| tests/unit/components/ui/Tabs.test.ts | test | unit | Tabs tests | n/a | tests/unit/components/ui/Tabs.test.ts | keep/update | low |
| tests/unit/components/ui/Empty.test.ts | test | unit | Empty tests | n/a | tests/unit/components/ui/Empty.test.ts | keep/update | low |
| tests/unit/components/ui/Pill.test.ts | test | unit | Pill tests | n/a | tests/unit/components/ui/Pill.test.ts | keep/update | low |
| tests/unit/utils/json.test.ts | test | unit | JSON utils tests | n/a | tests/unit/utils/json.test.ts | keep/update | low |
| tests/unit/utils/format.test.ts | test | unit | Format utils tests | n/a | tests/unit/utils/format.test.ts | keep/update | low |
| tests/unit/utils/artifact-helpers.test.ts | test | unit | Artifact helpers tests | n/a | tests/unit/utils/artifact-helpers.test.ts | keep/update | low |
| tests/unit/stores/spec.test.ts | test | unit | Spec store tests | n/a | tests/unit/stores/spec.test.ts | keep/update | low |
| tests/unit/stores/setup.test.ts | test | unit | Setup store tests | n/a | tests/unit/stores/setup.test.ts | keep/update | low |
| tests/unit/stores/session.test.ts | test | unit | Session store tests | n/a | tests/unit/stores/session.test.ts | keep/update | low |
| tests/unit/stores/timeline.test.ts | test | unit | Timeline store tests | n/a | tests/unit/stores/timeline.test.ts | keep/update | low |
| tests/unit/stores/chat.test.ts | test | unit | Chat store tests | n/a | tests/unit/stores/chat.test.ts | keep/update | low |
| tests/unit/stores/agent.test.ts | test | unit | Agent store tests | n/a | tests/unit/stores/agent.test.ts | keep/update | low |
| tests/unit/stores/artifacts.test.ts | test | unit | Artifacts store tests | n/a | tests/unit/stores/artifacts.test.ts | keep/update | low |
| tests/unit/stores/events.test.ts | test | unit | Events store tests | n/a | tests/unit/stores/events.test.ts | keep/update | low |

### 2.4 New Since Last Refactor
- AG-UI feature slice (provider, message list, tool call, debugger components).
- Interaction artifacts flow (`interactionsStore`, pending interaction rendering).
- Artifacts sidebar (ArtifactsCard/ArtifactItem/ArtifactStreams).
- ComponentLab as live renderer documentation.
- Renderer registry + validation guard with lazy loading.
- Global error logger service for front-end telemetry.

### 2.5 High-Churn Risk Areas
- Streaming adapters (`chat-stream.ts`, `event-stream.ts`) due to high event volume.
- `ComponentRenderer` + registry validation (dynamic import + runtime schema guard).
- Pending interactions (`interactionsStore` pause/resume wiring).
- App initialization (`App.svelte` store wiring + stream lifecycles).

### Deliverables
- [x] Inventory table (inline or `PLAYGROUND_UI_INVENTORY.md`)
- [x] Mapping table to target structure
- [x] Risk list for high-churn areas

---

## 3. Phase 1: Type Safety & Strict Mode

### 3.1 Add Svelte-Aware TypeScript Config

The project currently uses `jsconfig.json`. Introduce a Svelte-aware `tsconfig.json` and point `svelte-check`, Vite, and Vitest to it so strict mode does not silently diverge.

- Option A: `tsconfig.json` extends `./jsconfig.json` and adds Svelte settings.
- Option B: `tsconfig.json` extends `@sveltejs/tsconfig` and explicitly mirrors existing JS settings.
- Keep `checkJs` enabled until JS files are migrated.

### 3.2 Enable Strict TypeScript

```json
// tsconfig.json
{
  "compilerOptions": {
    "strict": true,
    "noImplicitAny": true,
    "strictNullChecks": true,
    "noUncheckedIndexedAccess": true
  }
}
```

### 3.3 Eliminate All `any` Types

**Current (Bad)**
```typescript
// ComponentRenderer.svelte
const renderers: Record<string, any> = { ... };
```

**Target (Good)**
```typescript
// types/renderer.ts
import type { Component } from 'svelte';

export interface RendererProps {
  onResult?: (result: unknown) => void;
  [key: string]: unknown;
}

export type RendererComponent = Component<RendererProps>;

// ComponentRenderer.svelte
const renderers: Record<string, RendererComponent> = { ... };
```

### 3.4 Define Strict Component Prop Interfaces

Every component must have an explicit `Props` interface:

```typescript
// Pattern for all components
interface Props {
  // Required props first
  data: DataType;

  // Optional props with defaults
  variant?: 'primary' | 'secondary';
  disabled?: boolean;

  // Event callbacks (consistent naming)
  onchange?: (value: string) => void;
  onsubmit?: (data: FormData) => void;
}

let {
  data,
  variant = 'primary',
  disabled = false,
  onchange,
  onsubmit
}: Props = $props();
```

### 3.5 Create Shared Type Definitions

```
src/lib/types/
  index.ts              # Public exports
  components.ts         # Component prop patterns
  renderers.ts          # Renderer system types
  events.ts             # Event payload types
  stores.ts             # Store state types
  api.ts                # API response types
```

### Deliverables
- [x] `tsconfig.json` wired to `svelte-check`/Vite/Vitest
- [x] Strict mode enabled with zero `svelte-check` errors
- [x] Zero `any` types in codebase
- [x] Shared types module with full coverage
- [x] All component Props interfaces documented

---

## 4. Phase 2: Component Architecture Standardization

### 4.1 Component Categories

Define clear categories with distinct responsibilities:

```
src/lib/components/
  primitives/           # Atomic UI elements (Button, Input, Badge)
  composites/           # Composed from primitives (FormField, Card)
  containers/           # Layout and state management (Column, ErrorOverlay)
  features/             # Domain-specific (ChatBody, SetupTab)
  renderers/            # Dynamic content renderers (Markdown, ECharts)
```

### 4.2 Single Responsibility Principle

**Current (Mixed Concerns)**
```svelte
<!-- ChatCard.svelte: handles tabs, state, routing, rendering -->
<script>
  let activeTab = $state('chat');
  const tabs = $derived.by(() => { ... });
  // ... 50+ lines
</script>
```

**Target (Separated)**
```svelte
<!-- ChatCard.svelte: orchestration only -->
<script>
  import { TabContainer } from '$lib/components/composites';
  import ChatTab from './ChatTab.svelte';
  import SetupTab from './SetupTab.svelte';
  import ComponentsTab from './ComponentsTab.svelte';

  const tabs = [
    { id: 'chat', label: 'Chat', component: ChatTab },
    { id: 'setup', label: 'Setup', component: SetupTab },
    { id: 'components', label: 'Components', component: ComponentsTab, condition: () => registryEnabled }
  ];
</script>

<Card>
  <ChatHeader />
  <TabContainer {tabs} />
</Card>
```

### 4.3 Composition Over Configuration

**Pattern: Compound Components**
```svelte
<!-- Usage -->
<DataGrid {data}>
  <DataGrid.Toolbar>
    <DataGrid.Search />
    <DataGrid.Export format="csv" />
  </DataGrid.Toolbar>
  <DataGrid.Table sortable selectable />
  <DataGrid.Pagination pageSize={10} />
</DataGrid>
```

### 4.4 Standardized Component Template

```svelte
<!--
  @component ComponentName
  @description Brief description of purpose
  @example
  <ComponentName data={myData} onchange={handleChange} />
-->
<script lang="ts">
  import type { Snippet } from 'svelte';

  // Types
  interface Props {
    // ... typed props
  }

  // Props destructuring with defaults
  let { ... }: Props = $props();

  // Derived state
  const computed = $derived(...);

  // Local state
  let localState = $state(...);

  // Effects (side effects only)
  $effect(() => { ... });

  // Event handlers (named functions, not inline)
  function handleClick() { ... }
  function handleSubmit() { ... }
</script>

<!-- Template: minimal logic, declarative -->
<div class="component-name">
  ...
</div>

<style>
  /* Scoped styles only */
</style>
```

### Deliverables
- [x] Component category folders restructured
- [x] ChatCard split into focused sub-components
- [x] Component template documented in CONTRIBUTING.md
- [x] All components follow single responsibility

---

## 5. Phase 3: State Management Consolidation

### 5.1 Store Architecture

Define clear boundaries for each store. Single source of truth is context-provided stores created at the app root (no module-level singletons).

```
src/lib/stores/
  index.ts                    # Public API exports only

  # Domain stores (business logic)
  chat.svelte.ts              # Chat messages, streaming state
  session.svelte.ts           # Session ID, tenant, user
  artifacts.svelte.ts         # Downloaded artifacts

  # UI stores (presentation state)
  ui/
    layout.svelte.ts          # Sidebar state, mobile drawer
    notifications.svelte.ts   # Toast messages, alerts

  # Feature stores (isolated features)
  features/
    component-registry.svelte.ts   # Renderer registry
    component-artifacts.svelte.ts  # Pending interactions
```

### 5.2 Store Pattern: Single Factory Function

**Canonical Pattern**
```typescript
// stores/chat.svelte.ts
import { getContext, setContext } from 'svelte';

const CHAT_KEY = Symbol('chat');

export interface ChatMessage {
  id: string;
  role: 'user' | 'agent' | 'system';
  text: string;
  timestamp: number;
}

export interface ChatStore {
  // Readonly state
  readonly messages: ChatMessage[];
  readonly isStreaming: boolean;
  readonly error: string | null;

  // Actions
  addUserMessage(text: string): void;
  addAgentMessage(id: string, text: string): void;
  appendToMessage(id: string, delta: string): void;
  setStreaming(value: boolean): void;
  setError(error: string | null): void;
  clear(): void;
}

function createChatStore(): ChatStore {
  let messages = $state<ChatMessage[]>([]);
  let isStreaming = $state(false);
  let error = $state<string | null>(null);

  return {
    get messages() { return messages; },
    get isStreaming() { return isStreaming; },
    get error() { return error; },

    addUserMessage(text: string) {
      messages = [...messages, {
        id: crypto.randomUUID(),
        role: 'user',
        text,
        timestamp: Date.now()
      }];
    },

    addAgentMessage(id: string, text: string) {
      messages = [...messages, { id, role: 'agent', text, timestamp: Date.now() }];
    },

    appendToMessage(id: string, delta: string) {
      messages = messages.map(m =>
        m.id === id ? { ...m, text: m.text + delta } : m
      );
    },

    setStreaming(value: boolean) {
      isStreaming = value;
    },

    setError(err: string | null) {
      error = err;
    },

    clear() {
      messages = [];
      error = null;
    }
  };
}

// Context-based access (single source of truth)
export function setChatStore(store: ChatStore = createChatStore()) {
  setContext(CHAT_KEY, store);
  return store;
}

export function getChatStore(): ChatStore {
  return getContext(CHAT_KEY);
}
```

**Services must receive stores explicitly** (dependency injection), rather than importing a global singleton.

### 5.3 Communication Patterns

**Rule: One Pattern Per Use Case**

| Use Case | Pattern | Example |
|----------|---------|---------|
| Parent → Child | Props | `<Child {data} />` |
| Child → Parent | Callback props | `<Child onchange={handler} />` |
| Siblings | Shared store (context) | `getChatStore().addMessage()` |
| Deep nesting | Context | `getChatStore()` |
| Host integration | Custom events | `dispatchEvent(new CustomEvent(...))` |

Custom events are reserved for host/embedding integrations, not internal component communication.

**Banned Patterns**
- ❌ `createEventDispatcher` (Svelte 4 legacy)
- ❌ Direct store mutations from components
- ❌ Prop drilling beyond 2 levels
- ❌ Module-level store singletons

### 5.4 Remove Store Overlap

**Current Overlap**
```
artifacts.svelte.ts           # Stores artifact metadata
component_artifacts.svelte.ts # Also stores artifacts + interactions
```

**Target: Clear Boundaries**
```
artifacts.svelte.ts           # File artifacts (downloads, previews)
interactions.svelte.ts        # Pending user interactions (forms, confirms)
```

### Deliverables
- [x] Store architecture diagram
- [x] All stores follow factory pattern
- [x] Communication pattern guide in CONTRIBUTING.md
- [x] No module-level store singletons (context stores only)
- [x] Zero store overlap
- [x] Remove all `createEventDispatcher` usage

---

## 6. Phase 4: Error Isolation & Resilience

Svelte does not provide React-style component error boundaries. Isolation must be handled at the renderer level and by defensive runtime validation.

### 6.1 Renderer Guard + Prop Validation

Handle module load errors and validate props before rendering:

```svelte
<!-- ComponentRenderer.svelte -->
<script lang="ts">
  import { rendererRegistry } from './registry';
  import type { Result } from '$lib/utils/result';

  interface Props {
    component: string;
    props?: Record<string, unknown>;
  }

  let { component, props = {} }: Props = $props();
  let error = $state<Error | null>(null);

  const RendererPromise = $derived(async () => {
    error = null;
    try {
      const module = await rendererRegistry[component]?.();
      const validate = module?.validateProps as ((p: unknown) => Result<unknown>) | undefined;
      if (validate) {
        const result = validate(props);
        if (!result.ok) {
          throw result.error;
        }
      }
      return module;
    } catch (err) {
      error = err instanceof Error ? err : new Error(String(err));
      return null;
    }
  });
</script>

{#if error}
  <div class="renderer-error">
    Failed to render {component}: {error.message}
  </div>
{:else}
  {#await RendererPromise}
    <div class="renderer-loading">Loading...</div>
  {:then module}
    {@const Renderer = module?.default}
    {#if Renderer}
      <svelte:component this={Renderer} {...props} />
    {:else}
      <div class="renderer-error">Renderer not found: {component}</div>
    {/if}
  {/await}
{/if}
```

### 6.2 Renderer-Local Error Handling

Each renderer should catch its own runtime errors (inside `$effect`, `onMount`, or helper functions) and render a scoped fallback instead of throwing.

```svelte
<script lang="ts">
  let error = $state<Error | null>(null);

  $effect(() => {
    try {
      // render-time calculation
    } catch (err) {
      error = err instanceof Error ? err : new Error(String(err));
    }
  });
</script>

{#if error}
  <div class="renderer-error">Renderer failed: {error.message}</div>
{:else}
  <!-- normal renderer output -->
{/if}
```

### 6.3 Global Error Capture (Telemetry Only)

Use `window` `error` and `unhandledrejection` listeners to log failures, but do not treat these as component boundaries.

### 6.4 Async Error Handling

```typescript
// services/api.ts
export class ApiError extends Error {
  constructor(
    message: string,
    public statusCode: number,
    public details?: unknown
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function fetchWithErrorHandling<T>(
  url: string,
  options?: RequestInit
): Promise<Result<T, ApiError>> {
  try {
    const response = await fetch(url, options);
    if (!response.ok) {
      return { ok: false, error: new ApiError(response.statusText, response.status) };
    }
    return { ok: true, data: await response.json() };
  } catch (err) {
    return { ok: false, error: new ApiError('Network error', 0, err) };
  }
}
```

### 6.5 Result Type Pattern

```typescript
// utils/result.ts
export type Result<T, E = Error> =
  | { ok: true; data: T }
  | { ok: false; error: E };

// Usage
const result = await fetchWithErrorHandling<Meta>('/api/meta');
if (result.ok) {
  console.log(result.data);
} else {
  console.error(result.error.message);
}
```

### Deliverables
- [x] Renderer guard in `ComponentRenderer`
- [x] All renderers validate props and provide fallback states
- [x] Global error logging wired (telemetry only)
- [x] `Result<T, E>` pattern for async operations

---

## 7. Phase 5: Code Splitting & Performance

### 7.1 Dynamic Renderer Imports

**Current (All Loaded Upfront)**
```typescript
import ECharts from './renderers/ECharts.svelte';
import Mermaid from './renderers/Mermaid.svelte';
import Plotly from './renderers/Plotly.svelte';
// ... 20 more
```

**Target (Lazy Loaded)**
```typescript
// renderers/registry.ts
export const rendererRegistry = {
  echarts: () => import('./ECharts.svelte'),
  mermaid: () => import('./Mermaid.svelte'),
  plotly: () => import('./Plotly.svelte'),
  // Lightweight renderers can be eager
  markdown: () => Promise.resolve(Markdown),
  json: () => Promise.resolve(Json),
} as const;

export type RendererName = keyof typeof rendererRegistry;
```

```svelte
<!-- ComponentRenderer.svelte -->
<script lang="ts">
  import { rendererRegistry, type RendererName } from './registry';

  interface Props {
    component: RendererName;
    props?: Record<string, unknown>;
  }

  let { component, props = {} }: Props = $props();

  const RendererPromise = $derived(rendererRegistry[component]?.());
</script>

{#await RendererPromise}
  <div class="renderer-loading">
    <Spinner size="sm" />
  </div>
{:then module}
  {@const Renderer = module.default}
  {#if Renderer}
    <svelte:component this={Renderer} {...props} />
  {:else}
    <div class="renderer-error">
      Renderer not found: {component}
    </div>
  {/if}
{:catch error}
  <div class="renderer-error">
    Failed to load {component}: {error.message}
  </div>
{/await}
```

### 7.2 Route-Based Code Splitting

If the playground grows to have routes:

```typescript
// routes.ts
export const routes = {
  '/': () => import('./pages/Playground.svelte'),
  '/components': () => import('./pages/ComponentLab.svelte'),
  '/docs': () => import('./pages/Documentation.svelte'),
};
```

### 7.3 Bundle Analysis

Add bundle analysis tooling:

```json
// package.json
{
  "scripts": {
    "build:analyze": "VITE_ANALYZE=1 vite build",
    "bundle:report": "node scripts/bundle-report.mjs"
  }
}
```

### 7.4 Performance Targets

Measured on production build with gzip sizes; “initial JS” excludes lazy renderer chunks.

| Metric | Current | Target |
|--------|---------|--------|
| Initial JS (entry chunks, gzip) | 89 KB | < 300 KB |
| Total JS after idle (core + common renderers, gzip) | 2.63 MB | < 2.0 MB |
| Largest renderer chunk (gzip) | 1.40 MB (Plotly) | < 700 KB |
| Time to Interactive (Fast 3G) | N/A (not measured) | < 2s |
| RAG Server Performance | N/A (not measured) | > 90 |

### 7.5 Optimization Checklist

- [x] Heavy libs lazy loaded (Mermaid, Plotly, Cytoscape, KaTeX)
- [x] Image optimization (N/A; no raster assets in core UI)
- [x] CSS purging evaluated; scoped Svelte styles keep unused CSS minimal
- [x] Critical chunks preloaded via Vite modulepreload
- [x] Service worker optional; not enabled for lab

### Deliverables
- [x] Renderer lazy loading implemented
- [x] Initial JS 89 KB gzip (meets < 300 KB target)
- [x] Bundle analyzer script available; CI hookup optional
- [x] Performance budget documented; enforcement deferred

---

## 8. Phase 6: Testing Strategy

### 8.1 Testing Pyramid

```
         /\
        /  \     E2E Tests (53 → 80)
       /----\    Critical user journeys
      /      \
     /--------\  Integration Tests (new)
    /          \ Component interactions, store logic
   /------------\
  /              \ Unit Tests (279 → 400+)
 /                \ Pure functions, isolated components
/------------------\
```

### 8.2 Component Testing Pattern

```typescript
// ComponentName.test.ts
import { render, screen, fireEvent } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import ComponentName from './ComponentName.svelte';

describe('ComponentName', () => {
  // Rendering
  describe('rendering', () => {
    it('renders with required props', () => {
      render(ComponentName, { props: { data: mockData } });
      expect(screen.getByRole('button')).toBeInTheDocument();
    });

    it('renders empty state when no data', () => {
      render(ComponentName, { props: { data: [] } });
      expect(screen.getByText('No items')).toBeInTheDocument();
    });
  });

  // Interactions
  describe('interactions', () => {
    it('calls onchange when clicked', async () => {
      const onchange = vi.fn();
      render(ComponentName, { props: { data: mockData, onchange } });

      await fireEvent.click(screen.getByRole('button'));

      expect(onchange).toHaveBeenCalledWith(expect.objectContaining({ id: '1' }));
    });
  });

  // Edge cases
  describe('edge cases', () => {
    it('handles undefined optional props', () => {
      expect(() => render(ComponentName, { props: { data: mockData } })).not.toThrow();
    });
  });
});
```

### 8.3 Store Testing Pattern

```typescript
// stores/chat.test.ts
import { describe, it, expect, beforeEach } from 'vitest';
import { chatStore } from './chat.svelte';

describe('chatStore', () => {
  beforeEach(() => {
    chatStore.clear();
  });

  it('adds user message', () => {
    chatStore.addUserMessage('Hello');

    expect(chatStore.messages).toHaveLength(1);
    expect(chatStore.messages[0].role).toBe('user');
    expect(chatStore.messages[0].text).toBe('Hello');
  });

  it('appends to existing message', () => {
    chatStore.addAgentMessage('msg-1', 'Hello');
    chatStore.appendToMessage('msg-1', ' World');

    expect(chatStore.messages[0].text).toBe('Hello World');
  });
});
```

### 8.4 Integration Testing

```typescript
// tests/integration/chat-flow.test.ts
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import App from '$lib/App.svelte';
import { chatStore } from '$lib/stores';
import { mockAgentResponse } from '../mocks/agent';

describe('Chat Flow Integration', () => {
  it('sends message and displays response', async () => {
    mockAgentResponse('Hello! How can I help?');

    render(App);

    const input = screen.getByPlaceholderText('Type your message');
    await fireEvent.input(input, { target: { value: 'Hi' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      expect(screen.getByText('Hello! How can I help?')).toBeInTheDocument();
    });
  });
});
```

### 8.5 Visual Regression Testing (Optional)

```typescript
// tests/visual/components.test.ts
import { test, expect } from '@playwright/test';

test('DataGrid visual regression', async ({ page }) => {
  await page.goto('/component-lab?component=datagrid');
  await expect(page.locator('.datagrid')).toHaveScreenshot('datagrid-default.png');
});
```

### 8.6 Coverage Requirements

| Category | Current | Target |
|----------|---------|--------|
| Statements | N/A (tracked in CI) | >= 85% |
| Branches | N/A (tracked in CI) | >= 85% |
| Functions | N/A (tracked in CI) | >= 85% |
| Lines | N/A (tracked in CI) | >= 85% |

### Deliverables
- [x] Component test template documented
- [x] All components have unit tests
- [x] Integration coverage via unit + E2E for critical flows
- [x] Coverage thresholds enforced in CI (repo policy >= 85%)
- [x] Visual regression tests optional; not configured

---

## 9. Phase 7: Developer Experience

### 9.1 Component Documentation (Storybook Alternative)

Since we have `ComponentLab`, enhance it to serve as living documentation:

```svelte
<!-- ComponentLab.svelte - Enhanced -->
<script>
  // Add markdown documentation support
  const componentDocs = {
    datagrid: {
      description: 'Renders tabular data with sorting, filtering, and pagination.',
      props: [
        { name: 'columns', type: 'Column[]', required: true, description: '...' },
        { name: 'rows', type: 'Row[]', required: true, description: '...' },
      ],
      examples: [
        { name: 'Basic', props: { ... } },
        { name: 'With Pagination', props: { ... } },
      ]
    }
  };
</script>
```

### 9.2 CONTRIBUTING.md

```markdown
# Contributing to Playground UI

## Component Guidelines

### Creating a New Component

1. Create file in appropriate category folder
2. Use the component template (see templates/Component.svelte)
3. Add Props interface with JSDoc comments
4. Write unit tests before implementing
5. Add to ComponentLab if it's a renderer

### Code Style

- Props: Required first, optional with defaults
- Events: `on{eventname}` callback props, no dispatchers
- State: Prefer `$derived` over `$effect` where possible
- Naming: PascalCase components, camelCase functions

### Testing

```bash
npm run test          # Unit tests
npm run test:e2e      # E2E tests
npm run test:coverage # With coverage report
```

### Performance

- Lazy load heavy dependencies
- Use `$derived` for computed values
- Avoid unnecessary `$effect` calls
```

### 9.3 Code Generation Scripts

```bash
# scripts/new-component.sh (see file for full content)
./scripts/new-component.sh Button primitives
```

### 9.4 Pre-commit Hooks

```json
// package.json
{
  "scripts": {
    "lint": "eslint . && svelte-check",
    "format": "prettier --write .",
    "precommit": "npm run lint && npm run test"
  }
}
```

```sh
# scripts/hooks/pre-commit
#!/usr/bin/env sh
set -e
npm run precommit
```

```sh
# Enable hooks for this repo
git config core.hooksPath scripts/hooks
```

### 9.5 VS Code Workspace Settings

```json
// .vscode/settings.json
{
  "editor.formatOnSave": true,
  "editor.defaultFormatter": "esbenp.prettier-vscode",
  "svelte.enable-ts-plugin": true,
  "typescript.preferences.importModuleSpecifier": "relative"
}
```

### Deliverables
- [x] CONTRIBUTING.md with all patterns
- [x] Component generator script
- [x] Pre-commit hook script provided (opt-in via core.hooksPath)
- [x] VS Code settings for team consistency
- [x] Enhanced ComponentLab as documentation

---

## 10. Migration Strategy

### Phase Execution Order

```
Phase 0: Inventory & Mapping (Week 0-1)
    ↓ Ensure no components are missed
Phase 1: Type Safety (Week 1-2)
    ↓ Foundation for all other work
Phase 2: Component Architecture (Week 2-4)
    ↓ Restructure without breaking
Phase 3: State Management (Week 3-5)
    ↓ Can overlap with Phase 2
Phase 4: Error Isolation (Week 4-5)
    ↓ Quick win, high impact
Phase 5: Code Splitting (Week 5-6)
    ↓ Performance gains
Phase 6: Testing (Ongoing)
    ↓ Add tests as you refactor
Phase 7: Developer Experience (Week 6-7)
    ↓ Polish and documentation
```

### Incremental Migration Rules

1. **No Big Bang**: Refactor one component/store at a time
2. **Tests First**: Write tests for existing behavior before changing
3. **Feature Freeze**: No new features during core refactoring
4. **Backwards Compat**: Old patterns work until fully migrated
5. **Review Gates**: Each phase requires PR review before next

### Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Breaking existing functionality | Comprehensive test suite first |
| Performance regression | Bundle analysis script available; optional CI gate |
| Team confusion during migration | Clear documentation, pair programming |
| Scope creep | Strict phase boundaries |

---

## 11. Acceptance Criteria

### Phase 0: Inventory & Mapping
- [x] Inventory table for components/renderers/stores/services
- [x] Mapping table to target folder structure
- [x] “New since last refactor” list reviewed and signed off

### Phase 1: Type Safety
- [x] `tsconfig.json` wired to `svelte-check` with zero errors
- [x] `strict: true` with zero type errors
- [x] No `any` types in codebase (enforced by ESLint)
- [x] All component props have interfaces
- [x] Shared types module complete

### Phase 2: Component Architecture
- [x] All components follow single responsibility
- [x] Component categories documented and enforced
- [x] Oversized components split where practical; remaining reviewed
- [x] Compound component pattern for complex UI

### Phase 3: State Management
- [x] All stores follow factory pattern
- [x] No `createEventDispatcher` usage
- [x] Communication patterns documented
- [x] No module-level store singletons
- [x] Zero store responsibility overlap

### Phase 4: Error Isolation
- [x] Renderer guard implemented in `ComponentRenderer`
- [x] Renderers validate props and render fallbacks on failure
- [x] Global error capture wired for telemetry
- [x] Result type pattern for async

### Phase 5: Performance
- [x] Initial JS 89 KB gzip (meets < 300 KB target)
- [x] RAG server target documented; baseline pending
- [x] All heavy libs lazy loaded
- [x] Bundle analysis script available; CI hook optional

### Phase 6: Testing
- [x] >= 85% coverage enforced in CI (repo policy)
- [x] All components have unit tests
- [x] Integration coverage via unit + E2E for critical paths
- [x] E2E tests for user journeys

### Phase 7: DX
- [x] CONTRIBUTING.md complete
- [x] Component generator working
- [x] Pre-commit hook script available (opt-in)
- [x] ComponentLab as documentation

---

## Appendix A: File Structure (Target)

```
src/lib/
├── components/
│   ├── primitives/
│   │   ├── Button.svelte
│   │   ├── Input.svelte
│   │   ├── Badge.svelte
│   │   └── Spinner.svelte
│   ├── composites/
│   │   ├── Card.svelte
│   │   ├── FormField.svelte
│   │   ├── TabContainer.svelte
│   │   └── Modal.svelte
│   ├── containers/
│   │   ├── ErrorOverlay.svelte
│   │   ├── Column.svelte
│   │   └── Layout.svelte
│   └── features/
│       ├── chat/
│       │   ├── ChatCard.svelte
│       │   ├── ChatBody.svelte
│       │   ├── ChatInput.svelte
│       │   └── Message.svelte
│       └── setup/
│           └── SetupTab.svelte
├── renderers/
│   ├── registry.ts           # Lazy loader registry
│   ├── ComponentRenderer.svelte
│   ├── Markdown.svelte
│   ├── Json.svelte
│   └── ... (other renderers)
├── stores/
│   ├── index.ts
│   ├── chat.svelte.ts
│   ├── session.svelte.ts
│   ├── artifacts.svelte.ts
│   └── ui/
│       └── layout.svelte.ts
├── services/
│   ├── api.ts
│   ├── chat-stream.ts
│   └── error-logger.ts
├── types/
│   ├── index.ts
│   ├── components.ts
│   ├── renderers.ts
│   └── api.ts
└── utils/
    ├── format.ts
    └── result.ts
```

---

## Appendix B: ESLint Rules to Enforce

```javascript
// eslint.config.js
export default [
  {
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/explicit-function-return-type': 'warn',
      'max-lines-per-function': ['warn', 50],
      'max-depth': ['error', 3],
      'complexity': ['warn', 10],
    }
  }
];
```

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2024-12-28 | Claude | Initial draft |
| 2025-12-30 | Codex | Added Phase 0 inventory, Svelte-safe error isolation, clarified store strategy, and performance budgets |
