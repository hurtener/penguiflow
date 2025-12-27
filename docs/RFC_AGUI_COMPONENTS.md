# RFC: Component Artifact System

| Field | Value |
|-------|-------|
| **RFC ID** | RFC-2025-002 |
| **Title** | Component Artifact System for LLM-Driven Rich UI Generation |
| **Author** | Platform Team |
| **Status** | Draft |
| **Created** | 2025-12-24 |
| **Depends On** | RFC-2025-001 (AG-UI Protocol Integration) |

---

## 1. Executive Summary

Problem: LLMs default to plain text. Users want rich experiences: charts, interactive tables, forms, dashboards, and report-style documents with embedded visualizations.

Solution: teach the LLM what UI components exist (via a registry), let it request them by emitting **structured component payloads**, and have the frontend render them.

The LLM does **not** generate HTML/JavaScript. It only says: “render component X with props Y”.

### 1.1 Mental Model

```
┌─────────────────────────────────────────────────────────────┐
│                         LLM                                  │
│  "I know I can ask for: charts, tables, forms, reports..."   │
│  "User wants sales data → request echarts + datagrid"        │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ Structured output:
                              │ { component: "echarts", props: {...} }
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Frontend                                │
│  "I received 'echarts' → render ECharts component"           │
│  "I received 'form' → render form, wait, send result back"   │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 What Makes This Powerful

| Capability | What it enables |
|-----------|------------------|
| Passive components | Charts, tables, diagrams, code blocks; LLM outputs data, frontend visualizes it |
| Interactive components | Forms, confirmations, selections; LLM can pause and wait for user input, then continue |
| Composite layouts | Reports that combine text + charts + tables in one document |
| Registry-driven | Add a new component to the registry → the LLM automatically learns it |

### 1.3 Why a Playground?

The Playground is the test lab where we validate:
- AG-UI streaming works end-to-end (backend → frontend).
- Components render correctly (ECharts, DataGrid, Form, Report, etc).
- The LLM actually uses the registry effectively (chooses the right component + shape).
- Interactive flows complete (frontend response reaches the agent and it continues).
- Compositions work (multi-component dashboards/reports).

### 1.4 One-liner

We’re building a system where LLMs generate rich, interactive UI by requesting components from a registry, and the Playground is where we prove it works before shipping.

### 1.5 Terminology (avoid collisions)

This RFC uses the term “artifact” in the “UI component artifact” sense (a renderable component payload). PenguiFlow also has “binary artifacts” (stored bytes for download). They are related but distinct:
- **UI component artifact**: `{ component, props }` rendered inline.
- **Binary artifact**: out-of-band stored bytes (PDFs, images, big JSON) referenced by ID/URL.

To avoid collisions in naming and event handling:
- **UI component artifacts**: `artifact_chunk` with `artifact_type: ui_component` (legacy SSE uses the `artifact_chunk` event; AG-UI uses `CUSTOM name="artifact_chunk"`).
- **Binary artifacts**: `artifact_stored` (legacy SSE uses the `artifact_stored` event; AG-UI uses `CUSTOM name="artifact_stored"`).

---

## 2. Architecture Overview

The architecture is intentionally simple: the LLM requests a component; the frontend renders it.

In practice, a backend adapter streams these requests over AG-UI so the UI can render them incrementally and reliably.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              LLM                                         │
│  System prompt includes component registry → knows what it can render   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ Emits structured output / tool call
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Backend Adapter                                  │
│  - Passive UI artifacts → artifact_chunk (`artifact_type=ui_component`)  │
│  - Interactive UI artifacts → Tool calls (ui_form, ui_confirm, ...)      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ AG-UI event stream
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Svelte Frontend                                   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    ComponentRenderer                             │   │
│  │  Routes component type → specific Svelte renderer                │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                    │                                     │
│         ┌──────────────┬──────────┴───────────┬──────────────┐         │
│         ▼              ▼                      ▼              ▼         │
│    ┌─────────┐   ┌─────────┐           ┌─────────┐   ┌─────────┐      │
│    │ ECharts │   │DataGrid │           │  Form   │   │ Report  │      │
│    └─────────┘   └─────────┘           └─────────┘   └─────────┘      │
│                                              │                          │
│                                              ▼                          │
│                                    User interaction                     │
│                                              │                          │
│                                              ▼                          │
│                                    TOOL_CALL_RESULT                     │
│                                    (back to agent)                      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Registry

The registry is the **single source of truth** for all available components. It defines:
- Component metadata (name, description, category)
- Props schema (JSON Schema)
- Whether the component is interactive (pauses agent execution)
- Examples for LLM few-shot learning

Implementation note:
- The registry must be consumable by **both** the backend (prompting + validation) and the frontend (rendering).
- Avoid diverging “one TS registry + one Python registry”. Prefer a single JSON source-of-truth (or a generated export) that both sides load.
  - Suggested repo layout: canonical registry data in `penguiflow/rich_output/registry.json` (or similar), with a generated TS wrapper for `penguiflow/cli/playground_ui/src/lib/component_artifacts/registry.ts` if needed.

Security note:
- Treat all component props as **untrusted** (LLM-controlled). Components like `html` / `embed` must be sandboxed or disabled by default behind an allowlist.

### 3.1 Registry Schema

```typescript
// penguiflow/cli/playground_ui/src/lib/component_artifacts/registry.ts

import type { JSONSchema7 } from 'json-schema';

export interface ComponentDefinition {
  /** Unique component identifier (lowercase, no spaces) */
  name: string;
  
  /** Human-readable description — this is what the LLM sees */
  description: string;
  
  /** JSON Schema defining component props */
  propsSchema: JSONSchema7;
  
  /** 
   * If true, component requires user interaction before agent continues.
   * Rendered via tool calls with pause-and-wait semantics.
   */
  interactive: boolean;
  
  /** Category for organization and filtering */
  category: 'visualization' | 'data' | 'document' | 'interactive' | 'layout' | 'media';
  
  /** Optional example for few-shot prompting */
  example?: {
    description: string;
    props: Record<string, unknown>;
  };
  
  /** Optional tags for search/filtering */
  tags?: string[];
}

export type ComponentRegistry = Record<string, ComponentDefinition>;
```

### 3.2 Complete Registry

```typescript
// penguiflow/cli/playground_ui/src/lib/component_artifacts/registry.ts

export const componentRegistry: ComponentRegistry = {
  
  // ═══════════════════════════════════════════════════════════════════════
  // VISUALIZATION COMPONENTS
  // ═══════════════════════════════════════════════════════════════════════
  
  echarts: {
    name: 'echarts',
    description: `Render interactive charts using Apache ECharts. Supports 20+ chart types including:
- Line, Bar, Area charts (trends, comparisons)
- Pie, Donut, Sunburst (proportions, hierarchies)  
- Scatter, Bubble (correlations, distributions)
- Heatmap, Treemap (density, hierarchical data)
- Candlestick, Boxplot (financial, statistical)
- Radar, Parallel (multivariate)
- Sankey, Graph (flows, networks)
- Gauge, Funnel (KPIs, conversions)

Use this for any data visualization need.`,
    category: 'visualization',
    interactive: false,
    tags: ['chart', 'graph', 'plot', 'visualization', 'data'],
    propsSchema: {
      type: 'object',
      required: ['option'],
      properties: {
        option: {
          type: 'object',
          description: 'ECharts option configuration object. See https://echarts.apache.org/en/option.html',
        },
        height: {
          type: 'string',
          default: '400px',
          description: 'Chart height (CSS value)',
        },
        width: {
          type: 'string', 
          default: '100%',
          description: 'Chart width (CSS value)',
        },
        theme: {
          type: 'string',
          enum: ['light', 'dark', 'vintage', 'macarons', 'roma', 'shine'],
          default: 'light',
          description: 'ECharts theme',
        },
        loading: {
          type: 'boolean',
          default: false,
          description: 'Show loading animation',
        },
      },
    },
    example: {
      description: 'Monthly sales trend with area fill',
      props: {
        option: {
          title: { text: 'Monthly Sales 2024' },
          tooltip: { trigger: 'axis' },
          xAxis: { 
            type: 'category', 
            data: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'] 
          },
          yAxis: { type: 'value', name: 'Sales ($)' },
          series: [{
            name: 'Sales',
            type: 'line',
            smooth: true,
            areaStyle: {},
            data: [12000, 15000, 14000, 18000, 22000, 21000],
          }],
        },
        height: '350px',
      },
    },
  },

  mermaid: {
    name: 'mermaid',
    description: `Render diagrams from text using Mermaid syntax. Supports:
- Flowcharts (graph TD/LR)
- Sequence diagrams
- Class diagrams
- Entity Relationship diagrams
- State diagrams
- Gantt charts
- User journey maps
- Git graphs
- Mindmaps
- Timeline

Use for any diagram that can be expressed as text.`,
    category: 'visualization',
    interactive: false,
    tags: ['diagram', 'flowchart', 'sequence', 'erd', 'uml'],
    propsSchema: {
      type: 'object',
      required: ['code'],
      properties: {
        code: {
          type: 'string',
          description: 'Mermaid diagram definition',
        },
        theme: {
          type: 'string',
          enum: ['default', 'dark', 'forest', 'neutral', 'base'],
          default: 'default',
        },
      },
    },
    example: {
      description: 'User authentication flow',
      props: {
        code: `sequenceDiagram
    participant U as User
    participant F as Frontend
    participant A as Auth API
    participant D as Database
    
    U->>F: Enter credentials
    F->>A: POST /login
    A->>D: Validate user
    D-->>A: User record
    A-->>F: JWT token
    F-->>U: Redirect to dashboard`,
      },
    },
  },

  plotly: {
    name: 'plotly',
    description: 'Render interactive charts using Plotly.js. Best for 3D plots, scientific visualizations, and charts requiring zoom/pan/hover interactions.',
    category: 'visualization',
    interactive: false,
    tags: ['chart', '3d', 'scientific', 'interactive'],
    propsSchema: {
      type: 'object',
      required: ['data'],
      properties: {
        data: {
          type: 'array',
          description: 'Plotly trace objects',
        },
        layout: {
          type: 'object',
          description: 'Plotly layout configuration',
        },
        config: {
          type: 'object',
          description: 'Plotly config options',
        },
        height: {
          type: 'string',
          default: '400px',
        },
      },
    },
  },

  // ═══════════════════════════════════════════════════════════════════════
  // DATA COMPONENTS
  // ═══════════════════════════════════════════════════════════════════════

  datagrid: {
    name: 'datagrid',
    description: `Render tabular data with rich features:
- Column sorting (click headers)
- Filtering/search
- Pagination
- Column resizing
- Row selection
- Cell formatting (numbers, dates, currencies)
- Export to CSV

Use for displaying query results, data tables, spreadsheet-like views.`,
    category: 'data',
    interactive: false,
    tags: ['table', 'grid', 'data', 'spreadsheet'],
    propsSchema: {
      type: 'object',
      required: ['columns', 'rows'],
      properties: {
        columns: {
          type: 'array',
          description: 'Column definitions',
          items: {
            type: 'object',
            required: ['field'],
            properties: {
              field: { type: 'string', description: 'Property name in row objects' },
              header: { type: 'string', description: 'Display header (defaults to field)' },
              width: { type: 'number', description: 'Column width in pixels' },
              minWidth: { type: 'number' },
              sortable: { type: 'boolean', default: true },
              filterable: { type: 'boolean', default: false },
              format: { 
                type: 'string', 
                enum: ['text', 'number', 'currency', 'percent', 'date', 'datetime', 'boolean'],
                default: 'text',
              },
              align: { type: 'string', enum: ['left', 'center', 'right'] },
            },
          },
        },
        rows: {
          type: 'array',
          items: { type: 'object' },
          description: 'Row data objects',
        },
        pageSize: {
          type: 'number',
          default: 10,
          description: 'Rows per page (0 for no pagination)',
        },
        sortable: {
          type: 'boolean',
          default: true,
        },
        filterable: {
          type: 'boolean',
          default: false,
        },
        selectable: {
          type: 'boolean',
          default: false,
          description: 'Enable row selection',
        },
        exportable: {
          type: 'boolean',
          default: false,
          description: 'Show export button',
        },
        striped: {
          type: 'boolean',
          default: true,
        },
        compact: {
          type: 'boolean',
          default: false,
        },
      },
    },
    example: {
      description: 'Sales data by region',
      props: {
        columns: [
          { field: 'region', header: 'Region', width: 150 },
          { field: 'sales', header: 'Sales', format: 'currency', align: 'right' },
          { field: 'growth', header: 'YoY Growth', format: 'percent', align: 'right' },
          { field: 'date', header: 'Last Updated', format: 'date' },
        ],
        rows: [
          { region: 'North America', sales: 1250000, growth: 0.12, date: '2024-03-15' },
          { region: 'Europe', sales: 980000, growth: 0.08, date: '2024-03-15' },
          { region: 'Asia Pacific', sales: 1100000, growth: 0.22, date: '2024-03-15' },
        ],
        pageSize: 10,
        exportable: true,
      },
    },
  },

  json: {
    name: 'json',
    description: 'Render JSON data as an interactive, collapsible tree viewer. Use for displaying API responses, configuration objects, or any structured data.',
    category: 'data',
    interactive: false,
    tags: ['json', 'tree', 'data', 'debug'],
    propsSchema: {
      type: 'object',
      required: ['data'],
      properties: {
        data: {
          description: 'Any JSON-serializable value',
        },
        expandLevel: {
          type: 'number',
          default: 2,
          description: 'Initial expansion depth',
        },
        sortKeys: {
          type: 'boolean',
          default: false,
        },
        theme: {
          type: 'string',
          enum: ['light', 'dark'],
          default: 'light',
        },
      },
    },
  },

  metric: {
    name: 'metric',
    description: 'Display a single KPI/metric with optional trend indicator, sparkline, and comparison. Use for dashboards, summaries, key numbers.',
    category: 'data',
    interactive: false,
    tags: ['kpi', 'number', 'metric', 'dashboard'],
    propsSchema: {
      type: 'object',
      required: ['value', 'label'],
      properties: {
        value: {
          oneOf: [{ type: 'number' }, { type: 'string' }],
          description: 'The metric value',
        },
        label: {
          type: 'string',
          description: 'Metric label/title',
        },
        format: {
          type: 'string',
          enum: ['number', 'currency', 'percent', 'compact'],
          default: 'number',
        },
        prefix: { type: 'string', description: 'e.g., "$"' },
        suffix: { type: 'string', description: 'e.g., "%", "users"' },
        change: {
          type: 'number',
          description: 'Change from previous period (for trend indicator)',
        },
        changeLabel: {
          type: 'string',
          default: 'vs last period',
        },
        sparkline: {
          type: 'array',
          items: { type: 'number' },
          description: 'Historical values for mini chart',
        },
        icon: {
          type: 'string',
          description: 'Icon name (e.g., "users", "dollar", "chart")',
        },
        color: {
          type: 'string',
          description: 'Accent color',
        },
      },
    },
    example: {
      description: 'Monthly revenue metric',
      props: {
        value: 125000,
        label: 'Monthly Revenue',
        format: 'currency',
        change: 0.12,
        changeLabel: 'vs last month',
        sparkline: [95000, 102000, 98000, 115000, 108000, 125000],
        icon: 'dollar',
      },
    },
  },

  // ═══════════════════════════════════════════════════════════════════════
  // DOCUMENT COMPONENTS
  // ═══════════════════════════════════════════════════════════════════════

  markdown: {
    name: 'markdown',
    description: `Render rich markdown content with support for:
- Headings, paragraphs, lists
- Tables
- Code blocks with syntax highlighting
- Math equations (KaTeX)
- Footnotes
- Task lists
- Blockquotes
- Horizontal rules

Use for formatted text content, documentation, explanations.`,
    category: 'document',
    interactive: false,
    tags: ['text', 'markdown', 'document', 'content'],
    propsSchema: {
      type: 'object',
      required: ['content'],
      properties: {
        content: {
          type: 'string',
          description: 'Markdown text',
        },
        allowHtml: {
          type: 'boolean',
          default: false,
          description: 'Allow raw HTML in markdown',
        },
        syntaxHighlight: {
          type: 'boolean',
          default: true,
        },
        mathEnabled: {
          type: 'boolean',
          default: true,
          description: 'Enable KaTeX math rendering',
        },
      },
    },
  },

  code: {
    name: 'code',
    description: 'Render source code with syntax highlighting, line numbers, and optional diff view. Use for code snippets, examples, file contents.',
    category: 'document',
    interactive: false,
    tags: ['code', 'syntax', 'programming'],
    propsSchema: {
      type: 'object',
      required: ['code'],
      properties: {
        code: {
          type: 'string',
          description: 'Source code content',
        },
        language: {
          type: 'string',
          description: 'Language for syntax highlighting (e.g., "python", "typescript", "sql")',
        },
        filename: {
          type: 'string',
          description: 'Optional filename to display',
        },
        showLineNumbers: {
          type: 'boolean',
          default: true,
        },
        startLine: {
          type: 'number',
          default: 1,
          description: 'Starting line number',
        },
        highlightLines: {
          type: 'array',
          items: { type: 'number' },
          description: 'Line numbers to highlight',
        },
        diff: {
          type: 'boolean',
          default: false,
          description: 'Render as diff (code should use +/- prefix)',
        },
        maxHeight: {
          type: 'string',
          description: 'Max height with scroll (e.g., "400px")',
        },
        copyable: {
          type: 'boolean',
          default: true,
        },
      },
    },
    example: {
      description: 'Python function with highlighted lines',
      props: {
        code: `def calculate_metrics(data: list[dict]) -> dict:
    """Calculate summary metrics from data."""
    total = sum(d['value'] for d in data)
    average = total / len(data) if data else 0
    
    return {
        'total': total,
        'average': average,
        'count': len(data),
    }`,
        language: 'python',
        filename: 'metrics.py',
        highlightLines: [3, 4],
      },
    },
  },

  latex: {
    name: 'latex',
    description: 'Render mathematical equations using LaTeX notation. Use for formulas, mathematical expressions, scientific notation.',
    category: 'document',
    interactive: false,
    tags: ['math', 'latex', 'equation', 'formula'],
    propsSchema: {
      type: 'object',
      required: ['expression'],
      properties: {
        expression: {
          type: 'string',
          description: 'LaTeX math expression',
        },
        displayMode: {
          type: 'boolean',
          default: true,
          description: 'true for block display, false for inline',
        },
      },
    },
    example: {
      description: 'Quadratic formula',
      props: {
        expression: 'x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}',
        displayMode: true,
      },
    },
  },

  callout: {
    name: 'callout',
    description: 'Render an attention-grabbing callout box for important information, warnings, tips, or notes.',
    category: 'document',
    interactive: false,
    tags: ['alert', 'warning', 'info', 'note'],
    propsSchema: {
      type: 'object',
      required: ['content'],
      properties: {
        content: {
          type: 'string',
          description: 'Callout text (supports markdown)',
        },
        type: {
          type: 'string',
          enum: ['info', 'warning', 'error', 'success', 'tip', 'note'],
          default: 'info',
        },
        title: {
          type: 'string',
          description: 'Optional title',
        },
        collapsible: {
          type: 'boolean',
          default: false,
        },
      },
    },
  },

  // ═══════════════════════════════════════════════════════════════════════
  // INTERACTIVE COMPONENTS (Human-in-the-loop)
  // ═══════════════════════════════════════════════════════════════════════

  form: {
    name: 'form',
    description: `Render a dynamic form to collect user input. **This pauses agent execution until the user submits.**

Use for:
- Collecting parameters for a query or action
- Getting user preferences
- Multi-field data entry
- Configuration settings

Supported field types: text, number, email, password, textarea, select, multiselect, checkbox, radio, date, datetime, time, file, range, color.`,
    category: 'interactive',
    interactive: true,
    tags: ['form', 'input', 'collect', 'hitl'],
    propsSchema: {
      type: 'object',
      required: ['fields'],
      properties: {
        title: {
          type: 'string',
          description: 'Form title',
        },
        description: {
          type: 'string',
          description: 'Form description/instructions',
        },
        fields: {
          type: 'array',
          description: 'Form field definitions',
          items: {
            type: 'object',
            required: ['name', 'type'],
            properties: {
              name: {
                type: 'string',
                description: 'Field name (key in result object)',
              },
              type: {
                type: 'string',
                enum: [
                  'text', 'number', 'email', 'password', 'url', 'tel',
                  'textarea', 'select', 'multiselect', 
                  'checkbox', 'radio', 'switch',
                  'date', 'datetime', 'time',
                  'file', 'range', 'color',
                ],
              },
              label: {
                type: 'string',
                description: 'Display label',
              },
              placeholder: {
                type: 'string',
              },
              required: {
                type: 'boolean',
                default: false,
              },
              disabled: {
                type: 'boolean',
                default: false,
              },
              default: {
                description: 'Default value',
              },
              options: {
                type: 'array',
                description: 'Options for select/radio/multiselect',
                items: {
                  oneOf: [
                    { type: 'string' },
                    { 
                      type: 'object', 
                      properties: {
                        value: { type: 'string' },
                        label: { type: 'string' },
                      },
                    },
                  ],
                },
              },
              validation: {
                type: 'object',
                properties: {
                  min: { type: 'number' },
                  max: { type: 'number' },
                  minLength: { type: 'number' },
                  maxLength: { type: 'number' },
                  pattern: { type: 'string', description: 'Regex pattern' },
                  message: { type: 'string', description: 'Custom error message' },
                },
              },
              helpText: {
                type: 'string',
                description: 'Helper text below field',
              },
              width: {
                type: 'string',
                enum: ['full', 'half', 'third'],
                default: 'full',
              },
            },
          },
        },
        submitLabel: {
          type: 'string',
          default: 'Submit',
        },
        cancelLabel: {
          type: 'string',
          description: 'If provided, shows cancel button',
        },
        layout: {
          type: 'string',
          enum: ['vertical', 'horizontal', 'inline'],
          default: 'vertical',
        },
      },
    },
    example: {
      description: 'Report parameters form',
      props: {
        title: 'Generate Report',
        description: 'Configure the report parameters',
        fields: [
          {
            name: 'dateRange',
            type: 'select',
            label: 'Date Range',
            required: true,
            options: ['Last 7 days', 'Last 30 days', 'Last quarter', 'Custom'],
          },
          {
            name: 'regions',
            type: 'multiselect',
            label: 'Regions',
            options: ['North America', 'Europe', 'Asia Pacific', 'Latin America'],
            default: ['North America'],
          },
          {
            name: 'includeCharts',
            type: 'checkbox',
            label: 'Include visualizations',
            default: true,
          },
          {
            name: 'email',
            type: 'email',
            label: 'Send copy to',
            placeholder: 'email@example.com',
            required: false,
          },
        ],
        submitLabel: 'Generate Report',
      },
    },
  },

  confirm: {
    name: 'confirm',
    description: `Display a confirmation dialog. **This pauses agent execution until the user confirms or cancels.**

Use for:
- Confirming destructive actions
- Approving operations
- Yes/No decisions
- Acknowledging important information`,
    category: 'interactive',
    interactive: true,
    tags: ['confirm', 'dialog', 'approve', 'hitl'],
    propsSchema: {
      type: 'object',
      required: ['message'],
      properties: {
        title: {
          type: 'string',
          description: 'Dialog title',
        },
        message: {
          type: 'string',
          description: 'Confirmation message (supports markdown)',
        },
        confirmLabel: {
          type: 'string',
          default: 'Confirm',
        },
        cancelLabel: {
          type: 'string',
          default: 'Cancel',
        },
        variant: {
          type: 'string',
          enum: ['info', 'warning', 'danger', 'success'],
          default: 'info',
          description: 'Visual style indicating severity',
        },
        details: {
          type: 'string',
          description: 'Additional details (collapsible)',
        },
      },
    },
    example: {
      description: 'Confirm data deletion',
      props: {
        title: 'Delete Records',
        message: 'Are you sure you want to delete **23 records** from the database?',
        confirmLabel: 'Yes, Delete',
        cancelLabel: 'Keep Records',
        variant: 'danger',
        details: 'This action cannot be undone. The following tables will be affected: users, orders, logs.',
      },
    },
  },

  select_option: {
    name: 'select_option',
    description: `Present a list of options for the user to choose from. **This pauses agent execution until the user makes a selection.**

Use for:
- Choosing between alternatives
- Selecting from search results
- Picking a template or preset
- Multi-choice decisions`,
    category: 'interactive',
    interactive: true,
    tags: ['select', 'choice', 'options', 'hitl'],
    propsSchema: {
      type: 'object',
      required: ['options'],
      properties: {
        title: {
          type: 'string',
          description: 'Selection prompt',
        },
        description: {
          type: 'string',
        },
        options: {
          type: 'array',
          items: {
            type: 'object',
            required: ['value', 'label'],
            properties: {
              value: {
                type: 'string',
                description: 'Value returned when selected',
              },
              label: {
                type: 'string',
                description: 'Display label',
              },
              description: {
                type: 'string',
                description: 'Additional description',
              },
              icon: {
                type: 'string',
                description: 'Icon name',
              },
              disabled: {
                type: 'boolean',
                default: false,
              },
              metadata: {
                type: 'object',
                description: 'Additional data to display',
              },
            },
          },
        },
        multiple: {
          type: 'boolean',
          default: false,
          description: 'Allow multiple selections',
        },
        minSelections: {
          type: 'number',
          default: 1,
        },
        maxSelections: {
          type: 'number',
        },
        layout: {
          type: 'string',
          enum: ['list', 'grid', 'cards'],
          default: 'list',
        },
        searchable: {
          type: 'boolean',
          default: false,
        },
      },
    },
    example: {
      description: 'Choose a chart type',
      props: {
        title: 'Select Visualization Type',
        description: 'Choose how you want to visualize this data:',
        layout: 'cards',
        options: [
          { value: 'line', label: 'Line Chart', description: 'Best for trends over time', icon: 'chart-line' },
          { value: 'bar', label: 'Bar Chart', description: 'Best for comparisons', icon: 'chart-bar' },
          { value: 'pie', label: 'Pie Chart', description: 'Best for proportions', icon: 'chart-pie' },
          { value: 'table', label: 'Data Table', description: 'Show raw numbers', icon: 'table' },
        ],
      },
    },
  },

  // ═══════════════════════════════════════════════════════════════════════
  // LAYOUT COMPONENTS (Composable containers)
  // ═══════════════════════════════════════════════════════════════════════

  report: {
    name: 'report',
    description: `A document-style layout for creating structured reports with multiple sections. Each section can contain:
- Markdown text content
- Embedded components (charts, tables, etc.)

Use for:
- Multi-section analyses
- Business reports
- Data summaries
- Documentation with embedded visualizations`,
    category: 'layout',
    interactive: false,
    tags: ['report', 'document', 'layout', 'sections'],
    propsSchema: {
      type: 'object',
      required: ['sections'],
      properties: {
        title: {
          type: 'string',
          description: 'Report title',
        },
        subtitle: {
          type: 'string',
        },
        metadata: {
          type: 'object',
          description: 'Report metadata (author, date, etc.)',
          properties: {
            author: { type: 'string' },
            date: { type: 'string' },
            version: { type: 'string' },
          },
        },
        toc: {
          type: 'boolean',
          default: false,
          description: 'Show table of contents',
        },
        sections: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              id: {
                type: 'string',
                description: 'Section ID for linking',
              },
              title: {
                type: 'string',
                description: 'Section heading',
              },
              content: {
                type: 'string',
                description: 'Markdown content',
              },
              components: {
                type: 'array',
                description: 'Embedded components',
                items: {
                  type: 'object',
                  required: ['component', 'props'],
                  properties: {
                    component: {
                      type: 'string',
                      description: 'Component name from registry',
                    },
                    props: {
                      type: 'object',
                      description: 'Component props',
                    },
                    caption: {
                      type: 'string',
                      description: 'Optional caption below component',
                    },
                  },
                },
              },
              subsections: {
                type: 'array',
                description: 'Nested subsections',
                items: { $ref: '#' },
              },
            },
          },
        },
        footer: {
          type: 'string',
          description: 'Footer content (markdown)',
        },
      },
    },
    example: {
      description: 'Quarterly sales report',
      props: {
        title: 'Q3 2024 Sales Report',
        subtitle: 'North America Region',
        metadata: { author: 'Analytics Team', date: '2024-10-01' },
        toc: true,
        sections: [
          {
            title: 'Executive Summary',
            content: 'Q3 showed strong growth with **15% increase** in overall revenue...',
          },
          {
            title: 'Sales by Region',
            content: 'Regional breakdown shows varied performance:',
            components: [
              {
                component: 'echarts',
                props: { option: { /* chart config */ } },
                caption: 'Figure 1: Regional sales distribution',
              },
            ],
          },
          {
            title: 'Detailed Data',
            content: 'The following table shows month-by-month breakdown:',
            components: [
              {
                component: 'datagrid',
                props: { columns: [], rows: [] },
                caption: 'Table 1: Monthly sales data',
              },
            ],
          },
        ],
      },
    },
  },

  grid: {
    name: 'grid',
    description: 'A responsive grid layout for arranging multiple components in columns. Use for dashboards, multi-chart displays, card layouts.',
    category: 'layout',
    interactive: false,
    tags: ['grid', 'layout', 'dashboard', 'columns'],
    propsSchema: {
      type: 'object',
      required: ['items'],
      properties: {
        columns: {
          type: 'number',
          default: 2,
          description: 'Number of columns (1-4)',
        },
        gap: {
          type: 'string',
          default: '1rem',
          description: 'Gap between items',
        },
        items: {
          type: 'array',
          items: {
            type: 'object',
            required: ['component', 'props'],
            properties: {
              component: {
                type: 'string',
                description: 'Component name',
              },
              props: {
                type: 'object',
                description: 'Component props',
              },
              colSpan: {
                type: 'number',
                default: 1,
                description: 'Columns to span (1-4)',
              },
              rowSpan: {
                type: 'number',
                default: 1,
              },
              title: {
                type: 'string',
                description: 'Optional card title',
              },
            },
          },
        },
        equalHeight: {
          type: 'boolean',
          default: true,
        },
      },
    },
    example: {
      description: 'Dashboard with 4 metrics and 2 charts',
      props: {
        columns: 4,
        gap: '1rem',
        items: [
          { component: 'metric', props: { label: 'Revenue', value: 125000, format: 'currency' } },
          { component: 'metric', props: { label: 'Users', value: 8420 } },
          { component: 'metric', props: { label: 'Conversion', value: 0.032, format: 'percent' } },
          { component: 'metric', props: { label: 'Avg Order', value: 85, format: 'currency' } },
          { component: 'echarts', props: { option: {} }, colSpan: 2, title: 'Revenue Trend' },
          { component: 'echarts', props: { option: {} }, colSpan: 2, title: 'User Growth' },
        ],
      },
    },
  },

  tabs: {
    name: 'tabs',
    description: 'Tabbed interface for organizing multiple components or content sections. Use when you have related but distinct views.',
    category: 'layout',
    interactive: false,
    tags: ['tabs', 'layout', 'navigation'],
    propsSchema: {
      type: 'object',
      required: ['tabs'],
      properties: {
        tabs: {
          type: 'array',
          items: {
            type: 'object',
            required: ['label'],
            properties: {
              id: { type: 'string' },
              label: { type: 'string' },
              icon: { type: 'string' },
              component: { type: 'string' },
              props: { type: 'object' },
              content: { type: 'string', description: 'Markdown content (alternative to component)' },
              disabled: { type: 'boolean', default: false },
            },
          },
        },
        defaultTab: {
          type: 'number',
          default: 0,
          description: 'Index of initially active tab',
        },
        variant: {
          type: 'string',
          enum: ['line', 'enclosed', 'pills'],
          default: 'line',
        },
      },
    },
  },

  accordion: {
    name: 'accordion',
    description: 'Collapsible sections for organizing content. Use for FAQs, expandable details, space-efficient layouts.',
    category: 'layout',
    interactive: false,
    tags: ['accordion', 'collapse', 'expandable'],
    propsSchema: {
      type: 'object',
      required: ['items'],
      properties: {
        items: {
          type: 'array',
          items: {
            type: 'object',
            required: ['title'],
            properties: {
              title: { type: 'string' },
              content: { type: 'string', description: 'Markdown content' },
              component: { type: 'string' },
              props: { type: 'object' },
              defaultOpen: { type: 'boolean', default: false },
            },
          },
        },
        allowMultiple: {
          type: 'boolean',
          default: false,
          description: 'Allow multiple sections open at once',
        },
      },
    },
  },

  // ═══════════════════════════════════════════════════════════════════════
  // MEDIA COMPONENTS
  // ═══════════════════════════════════════════════════════════════════════

  image: {
    name: 'image',
    description: 'Display an image with optional caption, zoom, and lightbox.',
    category: 'media',
    interactive: false,
    tags: ['image', 'picture', 'media'],
    propsSchema: {
      type: 'object',
      required: ['src'],
      properties: {
        src: {
          type: 'string',
          description: 'Image URL or base64 data URI',
        },
        alt: {
          type: 'string',
          description: 'Alt text for accessibility',
        },
        caption: {
          type: 'string',
        },
        width: { type: 'string' },
        maxWidth: { type: 'string' },
        height: { type: 'string' },
        objectFit: {
          type: 'string',
          enum: ['contain', 'cover', 'fill', 'none'],
          default: 'contain',
        },
        zoomable: {
          type: 'boolean',
          default: false,
          description: 'Enable click-to-zoom',
        },
        border: {
          type: 'boolean',
          default: false,
        },
      },
    },
  },

  html: {
    name: 'html',
    description: 'Render custom HTML/CSS/JS in a sandboxed iframe. Use for interactive demos, embedded widgets, or custom visualizations.',
    category: 'media',
    interactive: false,
    tags: ['html', 'iframe', 'custom', 'embed'],
    propsSchema: {
      type: 'object',
      required: ['html'],
      properties: {
        html: {
          type: 'string',
          description: 'HTML content',
        },
        css: {
          type: 'string',
          description: 'CSS styles',
        },
        js: {
          type: 'string',
          description: 'JavaScript code',
        },
        height: {
          type: 'string',
          default: '400px',
        },
        sandbox: {
          type: 'string',
          default: 'allow-scripts',
          description: 'iframe sandbox attribute',
        },
      },
    },
  },

  video: {
    name: 'video',
    description: 'Embed a video from URL or file.',
    category: 'media',
    interactive: false,
    tags: ['video', 'media', 'embed'],
    propsSchema: {
      type: 'object',
      required: ['src'],
      properties: {
        src: { type: 'string' },
        poster: { type: 'string', description: 'Poster image URL' },
        autoplay: { type: 'boolean', default: false },
        controls: { type: 'boolean', default: true },
        loop: { type: 'boolean', default: false },
        muted: { type: 'boolean', default: false },
        width: { type: 'string' },
        height: { type: 'string' },
      },
    },
  },

  embed: {
    name: 'embed',
    description: 'Embed external content via URL (YouTube, maps, documents, etc.).',
    category: 'media',
    interactive: false,
    tags: ['embed', 'iframe', 'external'],
    propsSchema: {
      type: 'object',
      required: ['url'],
      properties: {
        url: { type: 'string' },
        title: { type: 'string' },
        width: { type: 'string', default: '100%' },
        height: { type: 'string', default: '400px' },
        allowFullscreen: { type: 'boolean', default: true },
      },
    },
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// Registry Utilities
// ═══════════════════════════════════════════════════════════════════════════

export function getComponentsByCategory(category: ComponentDefinition['category']): ComponentDefinition[] {
  return Object.values(componentRegistry).filter(c => c.category === category);
}

export function getInteractiveComponents(): ComponentDefinition[] {
  return Object.values(componentRegistry).filter(c => c.interactive);
}

export function getPassiveComponents(): ComponentDefinition[] {
  return Object.values(componentRegistry).filter(c => !c.interactive);
}

export function searchComponents(query: string): ComponentDefinition[] {
  const q = query.toLowerCase();
  return Object.values(componentRegistry).filter(c => 
    c.name.includes(q) ||
    c.description.toLowerCase().includes(q) ||
    c.tags?.some(t => t.includes(q))
  );
}
```

---

## 4. Teaching the LLM

The LLM needs to know what components are available. We use **three mechanisms**:

### 4.1 System Prompt Generation

This runs **server-side** when constructing the LLM system prompt (or tool docs). The UI never generates prompts.

```python
# penguiflow/rich_output/prompting.py

from typing import Any
import json

def generate_component_system_prompt(registry: dict[str, Any]) -> str:
    """
    Generate system prompt section that teaches the LLM about available components.
    """
    lines = [
        "# Rich Output Components",
        "",
        "You can create rich, interactive outputs beyond plain text. Use the `render_component` tool to emit visualizations, data displays, forms, and composite layouts.",
        "",
        "## Quick Reference",
        "",
        "| Need | Component | When to Use |",
        "|------|-----------|-------------|",
    ]
    
    # Quick reference table
    quick_ref = [
        ("Chart/Graph", "echarts", "Any data visualization"),
        ("Data table", "datagrid", "Tabular data, query results"),
        ("Diagram", "mermaid", "Flowcharts, sequences, ERDs"),
        ("Single metric", "metric", "KPIs, key numbers"),
        ("Formatted text", "markdown", "Rich text with formatting"),
        ("Code snippet", "code", "Source code, examples"),
        ("User input", "form", "Collect parameters (PAUSES)"),
        ("Confirmation", "confirm", "Yes/No decisions (PAUSES)"),
        ("Selection", "select_option", "Choose from options (PAUSES)"),
        ("Multi-section doc", "report", "Reports with text + charts"),
        ("Dashboard grid", "grid", "Multiple components in columns"),
        ("Tabbed content", "tabs", "Related but distinct views"),
    ]
    
    for need, comp, when in quick_ref:
        lines.append(f"| {need} | `{comp}` | {when} |")
    
    lines.extend([
        "",
        "## Important: Interactive Components",
        "",
        "Components marked with **(PAUSES)** will pause your execution until the user responds. Use these when you need user input before continuing:",
        "- `form` — Collect structured input",
        "- `confirm` — Get yes/no approval", 
        "- `select_option` — Let user choose from options",
        "",
        "## Component Details",
        "",
    ])
    
    # Group by category
    categories = {}
    for name, defn in registry.items():
        cat = defn.get('category', 'other')
        if cat not in categories:
            categories[cat] = []
        categories[cat].append((name, defn))
    
    category_order = ['visualization', 'data', 'document', 'interactive', 'layout', 'media']
    category_titles = {
        'visualization': 'Visualization',
        'data': 'Data Display',
        'document': 'Document & Text',
        'interactive': 'Interactive (Human-in-the-Loop)',
        'layout': 'Layout & Composition',
        'media': 'Media & Embeds',
    }
    
    for cat in category_order:
        if cat not in categories:
            continue
        
        lines.append(f"### {category_titles.get(cat, cat.title())}")
        lines.append("")
        
        for name, defn in categories[cat]:
            # Component header
            interactive_badge = " ⏸️ **PAUSES**" if defn.get('interactive') else ""
            lines.append(f"#### `{name}`{interactive_badge}")
            lines.append("")
            
            # Description (truncated for prompt efficiency)
            desc = defn.get('description', '').split('\n')[0]  # First line only
            lines.append(desc)
            lines.append("")
            
            # Key props (simplified)
            schema = defn.get('propsSchema', {})
            required = schema.get('required', [])
            props = schema.get('properties', {})
            
            if props:
                prop_lines = []
                for prop_name, prop_schema in list(props.items())[:5]:  # Limit to 5 key props
                    req = "(required)" if prop_name in required else ""
                    prop_type = prop_schema.get('type', 'any')
                    prop_desc = prop_schema.get('description', '')[:50]  # Truncate
                    prop_lines.append(f"  - `{prop_name}` {req}: {prop_desc}")
                
                lines.append("**Key props:**")
                lines.extend(prop_lines)
                lines.append("")
            
            # Example if available
            example = defn.get('example')
            if example:
                lines.append(f"**Example** ({example.get('description', '')}):")
                lines.append("```json")
                lines.append(json.dumps({
                    "component": name,
                    "props": example.get('props', {})
                }, indent=2)[:500])  # Truncate large examples
                lines.append("```")
                lines.append("")
    
    # Usage patterns
    lines.extend([
        "## Usage Patterns",
        "",
        "### Single Component",
        "```",
        "render_component(component='echarts', props={'option': {...}})",
        "```",
        "",
        "### Dashboard with Multiple Charts",
        "Use `grid` component to arrange multiple visualizations:",
        "```json",
        '{',
        '  "component": "grid",',
        '  "props": {',
        '    "columns": 2,',
        '    "items": [',
        '      {"component": "metric", "props": {...}},',
        '      {"component": "echarts", "props": {...}, "colSpan": 2}',
        '    ]',
        '  }',
        '}',
        "```",
        "",
        "### Report with Sections",
        "Use `report` component for document-style output with text and embedded charts:",
        "```json",
        '{',
        '  "component": "report",',
        '  "props": {',
        '    "title": "Analysis Report",',
        '    "sections": [',
        '      {"title": "Summary", "content": "...markdown..."},',
        '      {"title": "Data", "components": [{"component": "datagrid", "props": {...}}]}',
        '    ]',
        '  }',
        '}',
        "```",
        "",
        "### Collecting User Input",
        "When you need user input before proceeding, call an interactive UI tool (example: `ui_form`):",
        "```json",
        '{',
        '  "title": "Configure Report",',
        '  "fields": [',
        '    {"name": "period", "type": "select", "options": ["Q1", "Q2", "Q3", "Q4"]}',
        '  ]',
        '}',
        "```",
        "The user's response will be returned to you as the tool result. Continue from there.",
    ])
    
    return "\n".join(lines)
```

### 4.2 Meta-tools (requesting UI components)

The core mechanism is an opt-in set of **meta-tools** that the LLM can call:
- `render_component` for passive components (charts, tables, docs, layouts).
- `ui_*` tools for interactive components (forms, confirmations, selections).

These tools are how the LLM produces a structured request without generating frontend code.

The request payload is exactly the mental model:
- `component`: which renderer to use (registry key)
- `props`: the JSON props for that renderer

```python
# PenguiFlow meta-tools (nodes)
#
# Note: In PenguiFlow, tools are nodes. Their schemas come from Pydantic models
# and are surfaced to the LLM through the planner catalog (see `penguiflow/catalog.py`).
# The Playground does not rely on frontend-supplied AG-UI `RunAgentInput.tools`.

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from penguiflow.catalog import tool
from penguiflow.planner import ToolContext


class RenderComponentArgs(BaseModel):
    component: str = Field(..., description="Registry component name")
    props: dict[str, Any] = Field(default_factory=dict, description="Component props")
    id: str | None = Field(default=None, description="Optional stable component id (for updates/dedupe)")
    title: str | None = None
    metadata: dict[str, Any] | None = None


class RenderComponentResult(BaseModel):
    ok: bool = True


@tool(desc="Request a rich UI component render (passive).", tags=["rich_output", "ui"], side_effects="pure")
async def render_component(args: RenderComponentArgs, ctx: ToolContext) -> RenderComponentResult:
    # Validate args.component/args.props against the registry + allowlist here.
    await ctx.emit_artifact(
        "ui",
        {
            "id": args.id,
            "component": args.component,
            "props": args.props,
            "title": args.title,
        },
        done=True,
        artifact_type="ui_component",
        meta=args.metadata or {},
    )
    return RenderComponentResult()
```

Notes:
- Keep `props` small. Large payloads should be referenced (e.g., via URLs or stored artifacts) instead of embedded directly.
- The backend adapter is responsible for translating these tool calls into AG-UI events the frontend can render (see §5).

### 4.3 Few-Shot Examples (Optional Enhancement)

```python
FEW_SHOT_EXAMPLES = """
## Example Interactions

**User**: Show me our Q3 sales data by region

**Assistant**: I'll create a visualization of the Q3 sales data.

[render_component]
{
  "component": "grid",
  "props": {
    "columns": 3,
    "items": [
      {"component": "metric", "props": {"label": "Total Revenue", "value": 1250000, "format": "currency", "change": 0.12}},
      {"component": "metric", "props": {"label": "Units Sold", "value": 8523, "change": 0.08}},
      {"component": "metric", "props": {"label": "Avg Order Value", "value": 147, "format": "currency", "change": -0.02}},
      {
        "component": "echarts",
        "props": {
          "option": {
            "title": {"text": "Sales by Region"},
            "xAxis": {"type": "category", "data": ["North", "South", "East", "West"]},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "data": [420000, 380000, 290000, 160000]}]
          }
        },
        "colSpan": 2
      },
      {
        "component": "datagrid",
        "props": {
          "columns": [
            {"field": "region", "header": "Region"},
            {"field": "revenue", "header": "Revenue", "format": "currency"},
            {"field": "growth", "header": "Growth", "format": "percent"}
          ],
          "rows": [
            {"region": "North", "revenue": 420000, "growth": 0.15},
            {"region": "South", "revenue": 380000, "growth": 0.08}
          ]
        },
        "colSpan": 1
      }
    ]
  }
}

---

**User**: I need to generate a report but first ask me about the parameters

**Assistant**: I'll collect the report parameters from you first.

[ui_form]
{
  "title": "Report Configuration",
  "description": "Please configure the report parameters:",
  "fields": [
    {"name": "period", "type": "select", "label": "Time Period", "required": true, "options": ["Last 7 days", "Last 30 days", "Last quarter", "Year to date"]},
    {"name": "regions", "type": "multiselect", "label": "Regions", "options": ["North America", "Europe", "Asia Pacific"]},
    {"name": "includeForecasts", "type": "checkbox", "label": "Include forecasts", "default": false}
  ],
  "submitLabel": "Generate Report"
}

[User submits form with: {"period": "Last quarter", "regions": ["North America", "Europe"], "includeForecasts": true}]

**Assistant**: Great, I'll generate the quarterly report for North America and Europe including forecasts.

[render_component]
{
  "component": "report",
  "props": {
    "title": "Quarterly Report",
    "subtitle": "North America & Europe",
    "sections": [...]
  }
}
"""
```

---

## 5. Render Protocol: AG-UI Integration

### 5.1 Passive Artifacts via CUSTOM Events

Non-interactive components are emitted as `artifact_chunk` events with `artifact_type: ui_component`.

- Legacy Playground SSE: emits an `artifact_chunk` SSE event.
- AG-UI streaming: emits a `CUSTOM name="artifact_chunk"` event (same payload shape).

```python
# Backend adapter

from ag_ui.core import CustomEvent, EventType


def emit_ui_component_artifact(
    component: str,
    props: dict,
    component_id: str | None = None,
) -> CustomEvent:
    """Emit a passive UI component artifact via AG-UI CUSTOM event."""
    return CustomEvent(
        type=EventType.CUSTOM,
        name="artifact_chunk",
        value={
            "stream_id": "ui",
            "seq": 0,
            "done": True,
            "artifact_type": "ui_component",
            "chunk": {
                "id": component_id,
                "component": component,
                "props": props,
            },
        },
    )
```

Notes:
- `component_id` is optional, but recommended for stable identity (dedupe, updates, linking to messages).
- Keep payloads small; large datasets should be referenced by URL or stored as a binary artifact.

### 5.2 Interactive Artifacts via Tool Calls

Interactive components use the standard **AG-UI tool call pattern**:

1. The LLM calls an interactive tool (e.g. `ui_form`) with component props.
2. The frontend renders the component and waits for the user.
3. The frontend returns the user’s response back to the agent.
4. The agent continues with that data.

Conceptually:

```python
# When the LLM requests ui_form, the backend streams tool call events
yield ToolCallStartEvent(
    tool_call_id=tool_id,
    tool_call_name="ui_form",
    parent_message_id=message_id,
)
yield ToolCallArgsEvent(tool_call_id=tool_id, delta=json.dumps(form_props))
yield ToolCallEndEvent(tool_call_id=tool_id)

# Frontend renders form, user submits
# Frontend sends the form result back (implementation-specific transport)
# Agent continues with that result
```

Implementation note (PenguiFlow):
- Phase 2 can start by rendering the interactive UI from the tool call, pausing via HITL (`PlannerPause.resume_token`), then resuming with the user payload.
- Later phases can upgrade to a true tool-result channel (e.g., `POST /agui/tool_result`) so the resumed execution emits `TOOL_CALL_RESULT` with the user response.

### 5.3 Event Flow Diagram

```
Agent                    Backend                    Frontend
  │                         │                          │
  │  "Show sales chart"     │                          │
  │ ───────────────────────►│                          │
  │                         │  RUN_STARTED             │
  │                         │ ─────────────────────────►
  │                         │  TEXT_MESSAGE_*          │
  │                         │ ─────────────────────────►
  │                         │                          │
  │  render_component(...)  │                          │
  │ ───────────────────────►│                          │
  │                         │  CUSTOM name=artifact_chunk (ui_component) │
  │                         │ ─────────────────────────► Render ECharts
  │                         │                          │
  │                         │  RUN_FINISHED            │
  │                         │ ─────────────────────────►

=== Interactive flow (form) ===

Agent                    Backend                    Frontend
  │                         │                          │
  │  ui_form(...)           │                          │
  │ ───────────────────────►│                          │
  │                         │  TOOL_CALL_*             │
  │                         │ ─────────────────────────► Render Form
  │                         │                          │ User submits
  │                         │ ◄───────────────────────── Return result
  │  (continues execution)  │                          │
```

---

## 6. Backend Implementation

The frontend described in this RFC only works if the backend provides **three things**:
1. A component registry the LLM can be taught from (and the frontend can validate/render against).
2. A set of meta-tools the LLM can call to request components (passive + interactive).
3. A runtime/event adapter that translates those requests into AG-UI events.

This section specifies the backend requirements independent of any single LLM provider.

**Opt-in requirement (spec + templates):**
- This entire feature set (registry prompting + meta-tools) MUST be **opt-in**.
- Default behavior for new projects: disabled (no UI meta-tools in the tool catalog; no registry prompt injection).
- When enabled, the spec and template generator should turn on the meta-tools and add the registry-driven prompt section.

Proposed spec shape (example):

```yaml
planner:
  rich_output:
    enabled: false
    allowlist: ["markdown", "json", "echarts", "datagrid", "report", "grid", "form", "confirm", "select_option"]
    include_prompt_catalog: true
    include_prompt_examples: false
```

Interactive note:
- If you enable interactive components (e.g., `form`, `confirm`, `select_option`), also enable HITL/pause-resume in your project (`agent.flags.hitl: true`) so the backend can pause and resume when the user responds.

Template generator requirement (example):
- `penguiflow new --with-rich-output` sets `planner.rich_output.enabled: true` and includes the recommended allowlist and prompt catalog.

### 6.1 Component Registry as a Backend Contract

Backend responsibilities:
- Load the registry at startup (fail fast if invalid).
- Expose a “registry digest” to the LLM (prompt section) so it can choose correct components.
- Validate every component request against the registry before emitting it to the UI.

Recommended registry contract:
- Unique `name` per component (stable identifier).
- JSON Schema for props (`propsSchema`).
- `interactive` boolean (drives tool type and UI “pause-and-wait” behavior).
- Optional examples (used for prompting and/or docs).

Operational requirements:
- Version the registry (`registry_version`) so you can evolve schemas without breaking old runs.
- Cap prompt size: the LLM does not need the full JSON schema for every component on every run. Prefer:
  - a short “catalog” summary in the system prompt, plus
  - “schema-on-demand” via a helper tool (fully implement a searchable catalog meta-tool).

### 6.2 Meta-tools (Backend Surface Area)

The LLM should request UI artifacts through tools (not by emitting raw HTML).

Required tools:
- `render_component` (passive): request a UI component render with `{component, props}`.
- `ui_form` (interactive): ask the user for structured input.
- `ui_confirm` (interactive): yes/no confirmation.
- `ui_select_option` (interactive): single-choice selection.

Tool shape (baseline):
- `render_component(component: str, props: object, id?: str, title?: str, metadata?: object)`
  - `id` is optional but recommended for stable identity (updates, referencing).
- Each `ui_*` tool accepts component-specific props and **must** result in a user response that returns to the LLM as the tool result.

Backend implementation requirements:
- Provide these tools to the agent runtime via the PenguiFlow tool catalog (nodes) when rich output is enabled. Do not rely on frontend-supplied AG-UI `RunAgentInput.tools` (the Playground sends `tools: []` today).
- Enforce allowlists (e.g., disable `html`/`embed` by default).

### 6.3 Validation, Safety, and Size Limits

Before emitting an `artifact_chunk` (`artifact_type=ui_component`):
- Validate `component` exists in registry.
- Validate `props` against `propsSchema`.
- Enforce max size limits (bytes) for:
  - per-artifact payload
  - per-run total artifact payload

Large payload strategy:
- Never stream megabytes of table data or base64 blobs as component props.
- Store large/binary data out-of-band (artifact store, object storage, etc.) and reference it:
  - `props.data_url` (HTTP URL), or
  - `props.artifact_id` (backend-owned identifier), plus a `/artifacts/{id}` download/read API.

Security requirements (non-negotiable):
- Treat all props as untrusted input (LLM-controlled).
- Sandbox / disable any renderer that can execute arbitrary code (HTML, embeds, iframes).
- Sanitize markdown (or render with a safe subset).

### 6.4 Emitting Passive UI Component Artifacts (`artifact_chunk`)

Backend must translate a `render_component` tool call into an `artifact_chunk` event with `artifact_type: ui_component`.

- Legacy Playground SSE: emit an `artifact_chunk` SSE event.
- AG-UI streaming: emit a `CUSTOM name="artifact_chunk"` event with the same payload shape.

Recommended PenguiFlow-native mechanism:
- Implement `render_component` as a PenguiFlow tool that calls `ToolContext.emit_artifact(...)` with `artifact_type="ui_component"` and `chunk={id, component, props, ...}`.

```json
{
  "type": "CUSTOM",
  "name": "artifact_chunk",
  "value": {
    "stream_id": "ui",
    "seq": 0,
    "done": true,
    "artifact_type": "ui_component",
    "chunk": {
      "id": "ui_123",
      "component": "echarts",
      "props": { "...": "..." }
    },
    "meta": {
      "registry_version": "2025-12-24",
      "source_tool": "render_component",
      "message_id": "msg_123"
    }
  }
}
```

Recommended additions:
- `meta.message_id` (attach the artifact to a specific assistant message).
- `seq` (ordering when multiple artifacts are emitted).
- `replace` / `update` semantics for incremental improvement:
  - Option A (preferred): `STATE_DELTA` updates if your UI artifacts are derived from state.
  - Option B: `CUSTOM name="artifact_update"` with `{id, patch}`.

### 6.5 Interactive Artifact Runtime (Tool Result Channel)

Interactive components require a concrete “wait for user → continue” mechanism.

Backend must support:
- Streaming `TOOL_CALL_START/ARGS/END` for `ui_*` tools.
- Pausing the agent execution until a user result arrives.
- Resuming execution by delivering the user result back to the agent as `TOOL_CALL_RESULT`.

PenguiFlow already has pause/resume primitives (`PlannerPause.resume_token`, `ReactPlanner.resume(...)`). Option B below can be implemented by reusing those primitives first, then upgraded to Option A if desired.

Implementation options (choose one; both are valid):

**Option A: Single streaming run (preferred UX)**
- Keep the `/agui/agent` stream open after `TOOL_CALL_END`.
- UI submits tool result to a separate endpoint (e.g., `POST /agui/tool_result`).
- Backend wakes the paused run and emits `TOOL_CALL_RESULT`, then continues streaming.

Concrete API contract (recommended):

`POST /agui/tool_result`

Request:
```json
{
  "thread_id": "thread-123",
  "run_id": "run-123",
  "tool_call_id": "call_abc",
  "result": { "any": "json" }
}
```

Rules:
- Reject unknown `tool_call_id` (404).
- Validate `result` against the interactive component’s expected output schema (400).
- Idempotency: allow re-posting the same `(run_id, tool_call_id)` result safely (either ignore or return the stored value).

**Option B: Resume-as-new-run (simpler backend)**
- End the current stream after emitting `TOOL_CALL_END` + a pause marker in state.
- UI submits tool result to `POST /agui/resume` (or re-calls `/agui/agent` with a “resume payload”).
- Backend starts a new run with the tool result injected into history/state.

Either way, the RFC requirement is the same: the LLM experiences interactive components as a tool call that returns a structured result.

### 6.6 Playground Backend Requirements

To serve as a “Component Lab”, the Playground backend needs:
- `/agui/agent` that supports:
  - `CUSTOM name="artifact_chunk"` with `artifact_type=ui_component`
  - `TOOL_CALL_*` events for `ui_*` interactive components
  - interactive result flow (Option A) or pause/resume (Option B)
- `/ui/components` (or similar) to return:
  - registry metadata (version, list of components, schemas, examples)
  - allowlist configuration (what components are enabled)

Feature gating:
- If `planner.rich_output.enabled` is false, the Playground should behave exactly as today:
  - do not advertise the meta-tools
  - do not inject the registry prompt section
  - (optional) hide the “Component Lab” UI

### 6.7 PenguiFlow-native Integration (Opt-in)

This repo should ship the meta-tools as first-class PenguiFlow tools (opt-in) so any planner-based agent can request UI artifacts.

Proposed Python package:

```
penguiflow/rich_output/
├── __init__.py          # public exports
├── registry.py          # load/validate registry + versioning
├── prompting.py         # prompt text generator (catalog summary)
├── tools.py             # tool metadata + schema helpers (render_component + ui_*)
├── nodes.py             # PenguiFlow @tool nodes implementing the meta-tools (opt-in)
├── validate.py          # jsonschema validation helpers + size checks
└── runtime.py           # interactive tool-call coordination (Option A/B)
```

If using `ReactPlanner`:
- Add the nodes from `penguiflow/rich_output/nodes.py` to the planner’s catalog when UI artifacts are enabled.
- Ensure the AG-UI adapter maps the resulting planner/tool events into:
  - `CUSTOM name="artifact_chunk"` with `artifact_type=ui_component` for passive UI artifacts
  - `TOOL_CALL_*` and `TOOL_CALL_RESULT` for interactive UI artifacts
- Implementation note: this mapping lives in `penguiflow/agui_adapter/penguiflow.py` (add handling for `event.event_type == "artifact_chunk"`).

### 6.8 Observability

Log/trace at minimum:
- Component requests (component name, size, validation success/failure).
- Interactive requests (tool name, tool_call_id, time-to-user-response).
- Registry version/digest used for each run.

---

## 7. Frontend Implementation

### 7.1 Component Artifact Store

In this repo, the Playground UI lives in `penguiflow/cli/playground_ui/`.

This store is **not** the same as the existing downloadable `artifactsStore` (binary artifacts). It stores renderable UI component payloads emitted as `artifact_chunk` with `artifact_type=ui_component`.

```typescript
// penguiflow/cli/playground_ui/src/lib/stores/component_artifacts.svelte.ts

export type ComponentArtifact = {
  id: string;
  component: string;
  props: Record<string, unknown>;
  title?: string;
  message_id?: string;
  seq: number;
  ts: number;
  meta?: Record<string, unknown>;
};

export type ArtifactChunkPayload = {
  stream_id?: string;
  seq?: number;
  done?: boolean;
  artifact_type?: string;
  chunk?: unknown;
  meta?: Record<string, unknown>;
  ts?: number;
};

export type PendingInteraction = {
  tool_call_id: string;
  tool_name: string; // ui_form, ui_confirm, ...
  props: Record<string, unknown>;
  message_id?: string;
};

function createComponentArtifactsStore() {
  let artifacts = $state<ComponentArtifact[]>([]);
  let pendingInteraction = $state<PendingInteraction | null>(null);

  function addArtifactChunk(payload: ArtifactChunkPayload, *, message_id: string | undefined = undefined): void {
    if (payload.artifact_type !== 'ui_component') return;
    if (!payload.chunk || typeof payload.chunk !== 'object') return;
    const chunk = payload.chunk as Record<string, unknown>;
    const component = chunk.component as string | undefined;
    const props = (chunk.props as Record<string, unknown>) || {};
    if (!component) return;

    artifacts = [
      ...artifacts,
      {
        id: (chunk.id as string) || `ui_${Date.now()}`,
        component,
        props,
        title: chunk.title as string | undefined,
        message_id: message_id ?? (payload.meta?.message_id as string | undefined),
        seq: payload.seq ?? 0,
        ts: payload.ts ?? Date.now(),
        meta: payload.meta ?? {}
      }
    ];
  }

  return {
    get artifacts() { return artifacts; },
    get pendingInteraction() { return pendingInteraction; },
    addArtifactChunk,
    setPendingInteraction(value: PendingInteraction | null) { pendingInteraction = value; },
    clear() { artifacts = []; pendingInteraction = null; }
  };
}

export const componentArtifactsStore = createComponentArtifactsStore();
```

### 7.2 Component Renderer

```svelte
<!-- penguiflow/cli/playground_ui/src/lib/component_artifacts/ComponentRenderer.svelte -->
<script lang="ts">
  import { componentRegistry } from './registry';
  
  // Import all renderers
  import ECharts from './renderers/ECharts.svelte';
  import Mermaid from './renderers/Mermaid.svelte';
  import Plotly from './renderers/Plotly.svelte';
  import DataGrid from './renderers/DataGrid.svelte';
  import Json from './renderers/Json.svelte';
  import Metric from './renderers/Metric.svelte';
  import Markdown from './renderers/Markdown.svelte';
  import Code from './renderers/Code.svelte';
  import Latex from './renderers/Latex.svelte';
  import Callout from './renderers/Callout.svelte';
  import Form from './renderers/Form.svelte';
  import Confirm from './renderers/Confirm.svelte';
  import SelectOption from './renderers/SelectOption.svelte';
  import Report from './renderers/Report.svelte';
  import Grid from './renderers/Grid.svelte';
  import Tabs from './renderers/Tabs.svelte';
  import Accordion from './renderers/Accordion.svelte';
  import Image from './renderers/Image.svelte';
  import Html from './renderers/Html.svelte';
  import Video from './renderers/Video.svelte';
  import Embed from './renderers/Embed.svelte';
  
  // Renderer mapping
  const renderers: Record<string, any> = {
    echarts: ECharts,
    mermaid: Mermaid,
    plotly: Plotly,
    datagrid: DataGrid,
    json: Json,
    metric: Metric,
    markdown: Markdown,
    code: Code,
    latex: Latex,
    callout: Callout,
    form: Form,
    confirm: Confirm,
    select_option: SelectOption,
    report: Report,
    grid: Grid,
    tabs: Tabs,
    accordion: Accordion,
    image: Image,
    html: Html,
    video: Video,
    embed: Embed,
  };
  
  export let component: string;
  export let props: Record<string, unknown>;
  export let onResult: ((result: unknown) => void) | undefined = undefined;
  
  $: Renderer = renderers[component];
  $: definition = componentRegistry[component];
  $: isInteractive = definition?.interactive ?? false;
</script>

{#if Renderer}
  <div 
    class="artifact"
    class:interactive={isInteractive}
    data-component={component}
  >
    <svelte:component 
      this={Renderer} 
      {...props} 
      {onResult}
    />
  </div>
{:else}
  <div class="artifact-error">
    <strong>Unknown component:</strong> {component}
    <pre>{JSON.stringify(props, null, 2)}</pre>
  </div>
{/if}

<style>
  .artifact {
    margin: 1rem 0;
    border-radius: 0.5rem;
    overflow: hidden;
    background: var(--artifact-bg, white);
    border: 1px solid var(--artifact-border, #e5e7eb);
  }
  
  .artifact.interactive {
    border-color: var(--interactive-border, #3b82f6);
    box-shadow: 0 0 0 3px var(--interactive-glow, rgba(59, 130, 246, 0.1));
  }
  
  .artifact-error {
    padding: 1rem;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 0.5rem;
    color: #dc2626;
  }
  
  .artifact-error pre {
    margin-top: 0.5rem;
    font-size: 0.75rem;
    overflow-x: auto;
  }
</style>
```

### 7.3 Example Renderers

#### ECharts Renderer

```svelte
<!-- penguiflow/cli/playground_ui/src/lib/component_artifacts/renderers/ECharts.svelte -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import * as echarts from 'echarts';
  
  export let option: echarts.EChartsOption;
  export let height: string = '400px';
  export let width: string = '100%';
  export let theme: string = 'light';
  export let loading: boolean = false;
  
  let container: HTMLElement;
  let chart: echarts.ECharts | null = null;
  
  onMount(() => {
    chart = echarts.init(container, theme);
    chart.setOption(option);
    
    if (loading) {
      chart.showLoading();
    }
    
    // Responsive resize
    const observer = new ResizeObserver(() => {
      chart?.resize();
    });
    observer.observe(container);
    
    return () => {
      observer.disconnect();
      chart?.dispose();
    };
  });
  
  // Update chart when options change
  $: if (chart && option) {
    chart.setOption(option, true);
  }
  
  $: if (chart) {
    loading ? chart.showLoading() : chart.hideLoading();
  }
</script>

<div 
  bind:this={container} 
  class="echarts-container"
  style:height
  style:width
/>

<style>
  .echarts-container {
    min-height: 200px;
  }
</style>
```

#### Form Renderer (Interactive)

```svelte
<!-- penguiflow/cli/playground_ui/src/lib/component_artifacts/renderers/Form.svelte -->
<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  
  export let title: string = '';
  export let description: string = '';
  export let fields: Array<{
    name: string;
    type: string;
    label?: string;
    placeholder?: string;
    required?: boolean;
    disabled?: boolean;
    default?: unknown;
    options?: Array<string | { value: string; label: string }>;
    validation?: Record<string, unknown>;
    helpText?: string;
    width?: 'full' | 'half' | 'third';
  }>;
  export let submitLabel: string = 'Submit';
  export let cancelLabel: string | undefined = undefined;
  export let layout: 'vertical' | 'horizontal' | 'inline' = 'vertical';
  export let onResult: ((result: Record<string, unknown>) => void) | undefined;
  
  const dispatch = createEventDispatcher<{
    submit: Record<string, unknown>;
    cancel: void;
  }>();
  
  // Initialize values with defaults
  let values: Record<string, unknown> = {};
  let errors: Record<string, string> = {};
  let touched: Record<string, boolean> = {};
  
  $: {
    for (const field of fields) {
      if (values[field.name] === undefined && field.default !== undefined) {
        values[field.name] = field.default;
      }
    }
  }
  
  function validate(): boolean {
    errors = {};
    let valid = true;
    
    for (const field of fields) {
      const value = values[field.name];
      
      // Required validation
      if (field.required && (value === undefined || value === '' || value === null)) {
        errors[field.name] = `${field.label || field.name} is required`;
        valid = false;
        continue;
      }
      
      // Custom validation
      const v = field.validation;
      if (v && value !== undefined && value !== '') {
        if (v.min !== undefined && typeof value === 'number' && value < v.min) {
          errors[field.name] = `Minimum value is ${v.min}`;
          valid = false;
        }
        if (v.max !== undefined && typeof value === 'number' && value > v.max) {
          errors[field.name] = `Maximum value is ${v.max}`;
          valid = false;
        }
        if (v.minLength !== undefined && typeof value === 'string' && value.length < v.minLength) {
          errors[field.name] = `Minimum length is ${v.minLength}`;
          valid = false;
        }
        if (v.pattern && typeof value === 'string' && !new RegExp(v.pattern as string).test(value)) {
          errors[field.name] = v.message as string || 'Invalid format';
          valid = false;
        }
      }
    }
    
    return valid;
  }
  
  function handleSubmit() {
    // Mark all as touched
    for (const field of fields) {
      touched[field.name] = true;
    }
    
    if (!validate()) return;
    
    const result = { ...values };
    dispatch('submit', result);
    onResult?.(result);
  }
  
  function handleCancel() {
    dispatch('cancel');
    onResult?.({ _cancelled: true });
  }
  
  function normalizeOptions(options: typeof fields[number]['options']) {
    return options?.map(o => typeof o === 'string' ? { value: o, label: o } : o) ?? [];
  }
</script>

<form 
  class="artifact-form"
  class:horizontal={layout === 'horizontal'}
  class:inline={layout === 'inline'}
  on:submit|preventDefault={handleSubmit}
>
  {#if title}
    <h3 class="form-title">{title}</h3>
  {/if}
  
  {#if description}
    <p class="form-description">{description}</p>
  {/if}
  
  <div class="form-fields">
    {#each fields as field}
      {@const fieldId = `field-${field.name}`}
      {@const hasError = touched[field.name] && errors[field.name]}
      
      <div 
        class="form-field"
        class:half={field.width === 'half'}
        class:third={field.width === 'third'}
        class:error={hasError}
      >
        {#if field.type !== 'checkbox' && field.type !== 'switch'}
          <label for={fieldId}>
            {field.label || field.name}
            {#if field.required}<span class="required">*</span>{/if}
          </label>
        {/if}
        
        {#if field.type === 'text' || field.type === 'email' || field.type === 'password' || field.type === 'url' || field.type === 'tel'}
          <input
            type={field.type}
            id={fieldId}
            bind:value={values[field.name]}
            on:blur={() => touched[field.name] = true}
            placeholder={field.placeholder}
            required={field.required}
            disabled={field.disabled}
          />
        
        {:else if field.type === 'number'}
          <input
            type="number"
            id={fieldId}
            bind:value={values[field.name]}
            on:blur={() => touched[field.name] = true}
            placeholder={field.placeholder}
            required={field.required}
            disabled={field.disabled}
            min={field.validation?.min}
            max={field.validation?.max}
          />
        
        {:else if field.type === 'textarea'}
          <textarea
            id={fieldId}
            bind:value={values[field.name]}
            on:blur={() => touched[field.name] = true}
            placeholder={field.placeholder}
            required={field.required}
            disabled={field.disabled}
            rows="3"
          />
        
        {:else if field.type === 'select'}
          <select
            id={fieldId}
            bind:value={values[field.name]}
            on:blur={() => touched[field.name] = true}
            required={field.required}
            disabled={field.disabled}
          >
            <option value="">Select...</option>
            {#each normalizeOptions(field.options) as opt}
              <option value={opt.value}>{opt.label}</option>
            {/each}
          </select>
        
        {:else if field.type === 'multiselect'}
          <select
            id={fieldId}
            bind:value={values[field.name]}
            on:blur={() => touched[field.name] = true}
            multiple
            disabled={field.disabled}
          >
            {#each normalizeOptions(field.options) as opt}
              <option value={opt.value}>{opt.label}</option>
            {/each}
          </select>
        
        {:else if field.type === 'checkbox' || field.type === 'switch'}
          <label class="checkbox-label">
            <input
              type="checkbox"
              id={fieldId}
              bind:checked={values[field.name]}
              disabled={field.disabled}
            />
            <span>{field.label || field.name}</span>
          </label>
        
        {:else if field.type === 'radio'}
          <div class="radio-group">
            {#each normalizeOptions(field.options) as opt, i}
              <label class="radio-label">
                <input
                  type="radio"
                  name={field.name}
                  value={opt.value}
                  bind:group={values[field.name]}
                  disabled={field.disabled}
                />
                <span>{opt.label}</span>
              </label>
            {/each}
          </div>
        
        {:else if field.type === 'date'}
          <input
            type="date"
            id={fieldId}
            bind:value={values[field.name]}
            on:blur={() => touched[field.name] = true}
            required={field.required}
            disabled={field.disabled}
          />
        
        {:else if field.type === 'datetime'}
          <input
            type="datetime-local"
            id={fieldId}
            bind:value={values[field.name]}
            on:blur={() => touched[field.name] = true}
            required={field.required}
            disabled={field.disabled}
          />
        
        {:else if field.type === 'time'}
          <input
            type="time"
            id={fieldId}
            bind:value={values[field.name]}
            on:blur={() => touched[field.name] = true}
            required={field.required}
            disabled={field.disabled}
          />
        
        {:else if field.type === 'range'}
          <input
            type="range"
            id={fieldId}
            bind:value={values[field.name]}
            min={field.validation?.min ?? 0}
            max={field.validation?.max ?? 100}
            disabled={field.disabled}
          />
          <span class="range-value">{values[field.name]}</span>
        
        {:else if field.type === 'color'}
          <input
            type="color"
            id={fieldId}
            bind:value={values[field.name]}
            disabled={field.disabled}
          />
        {/if}
        
        {#if field.helpText && !hasError}
          <span class="help-text">{field.helpText}</span>
        {/if}
        
        {#if hasError}
          <span class="error-text">{errors[field.name]}</span>
        {/if}
      </div>
    {/each}
  </div>
  
  <div class="form-actions">
    {#if cancelLabel}
      <button type="button" class="btn-cancel" on:click={handleCancel}>
        {cancelLabel}
      </button>
    {/if}
    <button type="submit" class="btn-submit">
      {submitLabel}
    </button>
  </div>
</form>

<style>
  .artifact-form {
    padding: 1.5rem;
  }
  
  .form-title {
    margin: 0 0 0.5rem;
    font-size: 1.25rem;
  }
  
  .form-description {
    margin: 0 0 1.5rem;
    color: #6b7280;
  }
  
  .form-fields {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 1rem;
  }
  
  .form-field {
    grid-column: span 6;
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
  }
  
  .form-field.half {
    grid-column: span 3;
  }
  
  .form-field.third {
    grid-column: span 2;
  }
  
  label {
    font-weight: 500;
    font-size: 0.875rem;
  }
  
  .required {
    color: #ef4444;
  }
  
  input, select, textarea {
    padding: 0.5rem 0.75rem;
    border: 1px solid #d1d5db;
    border-radius: 0.375rem;
    font-size: 0.875rem;
  }
  
  input:focus, select:focus, textarea:focus {
    outline: none;
    border-color: #3b82f6;
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
  }
  
  .form-field.error input,
  .form-field.error select,
  .form-field.error textarea {
    border-color: #ef4444;
  }
  
  .help-text {
    font-size: 0.75rem;
    color: #6b7280;
  }
  
  .error-text {
    font-size: 0.75rem;
    color: #ef4444;
  }
  
  .checkbox-label, .radio-label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: normal;
    cursor: pointer;
  }
  
  .radio-group {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  
  .form-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.75rem;
    margin-top: 1.5rem;
    padding-top: 1.5rem;
    border-top: 1px solid #e5e7eb;
  }
  
  button {
    padding: 0.5rem 1rem;
    border-radius: 0.375rem;
    font-weight: 500;
    cursor: pointer;
  }
  
  .btn-submit {
    background: #3b82f6;
    color: white;
    border: none;
  }
  
  .btn-submit:hover {
    background: #2563eb;
  }
  
  .btn-cancel {
    background: white;
    color: #374151;
    border: 1px solid #d1d5db;
  }
  
  .btn-cancel:hover {
    background: #f9fafb;
  }
</style>
```

#### Report Renderer (Recursive Layout)

```svelte
<!-- penguiflow/cli/playground_ui/src/lib/component_artifacts/renderers/Report.svelte -->
<script lang="ts">
  import ComponentRenderer from '../ComponentRenderer.svelte';
  import Markdown from './Markdown.svelte';
  
  interface Section {
    id?: string;
    title?: string;
    content?: string;
    components?: Array<{
      component: string;
      props: Record<string, unknown>;
      caption?: string;
    }>;
    subsections?: Section[];
  }
  
  export let title: string = '';
  export let subtitle: string = '';
  export let metadata: { author?: string; date?: string; version?: string } = {};
  export let toc: boolean = false;
  export let sections: Section[] = [];
  export let footer: string = '';
  
  // Generate TOC entries
  function generateToc(sections: Section[], level = 0): Array<{ title: string; id: string; level: number }> {
    const entries: Array<{ title: string; id: string; level: number }> = [];
    
    for (const section of sections) {
      if (section.title) {
        entries.push({
          title: section.title,
          id: section.id || slugify(section.title),
          level,
        });
      }
      if (section.subsections) {
        entries.push(...generateToc(section.subsections, level + 1));
      }
    }
    
    return entries;
  }
  
  function slugify(text: string): string {
    return text.toLowerCase().replace(/\s+/g, '-').replace(/[^\w-]/g, '');
  }
  
  $: tocEntries = toc ? generateToc(sections) : [];
</script>

<article class="artifact-report">
  <header class="report-header">
    {#if title}
      <h1>{title}</h1>
    {/if}
    {#if subtitle}
      <p class="subtitle">{subtitle}</p>
    {/if}
    {#if metadata.author || metadata.date}
      <div class="meta">
        {#if metadata.author}
          <span>By {metadata.author}</span>
        {/if}
        {#if metadata.date}
          <span>{metadata.date}</span>
        {/if}
        {#if metadata.version}
          <span>v{metadata.version}</span>
        {/if}
      </div>
    {/if}
  </header>
  
  {#if toc && tocEntries.length > 0}
    <nav class="toc">
      <h2>Contents</h2>
      <ul>
        {#each tocEntries as entry}
          <li style:margin-left="{entry.level * 1}rem">
            <a href="#{entry.id}">{entry.title}</a>
          </li>
        {/each}
      </ul>
    </nav>
  {/if}
  
  <div class="report-body">
    {#each sections as section}
      <section id={section.id || (section.title ? slugify(section.title) : undefined)}>
        {#if section.title}
          <h2>{section.title}</h2>
        {/if}
        
        {#if section.content}
          <Markdown content={section.content} />
        {/if}
        
        {#if section.components}
          {#each section.components as comp}
            <figure class="component-figure">
              <ComponentRenderer component={comp.component} props={comp.props} />
              {#if comp.caption}
                <figcaption>{comp.caption}</figcaption>
              {/if}
            </figure>
          {/each}
        {/if}
        
        {#if section.subsections}
          {#each section.subsections as subsection}
            <section class="subsection">
              {#if subsection.title}
                <h3>{subsection.title}</h3>
              {/if}
              {#if subsection.content}
                <Markdown content={subsection.content} />
              {/if}
              {#if subsection.components}
                {#each subsection.components as comp}
                  <figure class="component-figure">
                    <ComponentRenderer component={comp.component} props={comp.props} />
                    {#if comp.caption}
                      <figcaption>{comp.caption}</figcaption>
                    {/if}
                  </figure>
                {/each}
              {/if}
            </section>
          {/each}
        {/if}
      </section>
    {/each}
  </div>
  
  {#if footer}
    <footer class="report-footer">
      <Markdown content={footer} />
    </footer>
  {/if}
</article>

<style>
  .artifact-report {
    max-width: 900px;
    margin: 0 auto;
    padding: 2rem;
    font-family: system-ui, -apple-system, sans-serif;
    line-height: 1.6;
  }
  
  .report-header {
    margin-bottom: 2rem;
    padding-bottom: 1rem;
    border-bottom: 2px solid #e5e7eb;
  }
  
  .report-header h1 {
    margin: 0 0 0.5rem;
    font-size: 2rem;
  }
  
  .subtitle {
    margin: 0 0 0.5rem;
    font-size: 1.25rem;
    color: #6b7280;
  }
  
  .meta {
    display: flex;
    gap: 1rem;
    font-size: 0.875rem;
    color: #9ca3af;
  }
  
  .toc {
    margin-bottom: 2rem;
    padding: 1rem;
    background: #f9fafb;
    border-radius: 0.5rem;
  }
  
  .toc h2 {
    margin: 0 0 0.5rem;
    font-size: 1rem;
  }
  
  .toc ul {
    margin: 0;
    padding-left: 1rem;
    list-style: none;
  }
  
  .toc li {
    margin: 0.25rem 0;
  }
  
  .toc a {
    color: #3b82f6;
    text-decoration: none;
  }
  
  .toc a:hover {
    text-decoration: underline;
  }
  
  .report-body section {
    margin-bottom: 2rem;
  }
  
  .report-body h2 {
    margin: 0 0 1rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid #e5e7eb;
  }
  
  .report-body h3 {
    margin: 1.5rem 0 0.75rem;
  }
  
  .subsection {
    margin-left: 1rem;
    padding-left: 1rem;
    border-left: 2px solid #e5e7eb;
  }
  
  .component-figure {
    margin: 1.5rem 0;
  }
  
  figcaption {
    margin-top: 0.5rem;
    font-size: 0.875rem;
    color: #6b7280;
    text-align: center;
    font-style: italic;
  }
  
  .report-footer {
    margin-top: 2rem;
    padding-top: 1rem;
    border-top: 1px solid #e5e7eb;
    font-size: 0.875rem;
    color: #6b7280;
  }
</style>
```

### 7.4 Playground Component Lab (Development workflow)

The Playground UI is the test bench for this RFC. Add a “Component Lab” panel (opt-in) that:
- Lives as a third tab in `penguiflow/cli/playground_ui/src/lib/components/center/chat/ChatCard.svelte` (only when rich output is enabled).
- Lists the registry (`componentRegistry`) and shows the JSON schema + examples for each component.
- Lets developers paste/edit a `{component, props}` payload and preview it using `ComponentRenderer`.
- Lets developers capture the last emitted artifact payload and iterate quickly (copy/paste into the editor).

This keeps component development tight-loop and avoids requiring the full agent run for every renderer tweak.

---

## 8. Integration with Message Display

Component artifacts should appear inline under the assistant message bubble.

Playground UI wiring:
- **Ingest events**: update `penguiflow/cli/playground_ui/src/lib/services/chat-stream.ts` to route `artifact_chunk` events with `artifact_type === "ui_component"` into `componentArtifactsStore.addArtifactChunk(...)` (for both SSE and AG-UI `CUSTOM name="artifact_chunk"`).
- **Render inline**: update `penguiflow/cli/playground_ui/src/lib/components/center/chat/Message.svelte` to render the artifacts for that message.

Example (Message rendering):

```svelte
<!-- penguiflow/cli/playground_ui/src/lib/components/center/chat/Message.svelte -->
<script lang="ts">
  import type { ChatMessage } from '$lib/types';
  import { componentArtifactsStore } from '$lib/stores/component_artifacts.svelte';
  import ComponentRenderer from '$lib/component_artifacts/ComponentRenderer.svelte';

  interface Props {
    message: ChatMessage;
  }

  let { message }: Props = $props();

  const componentArtifacts = $derived(
    componentArtifactsStore.artifacts.filter(a => a.message_id === message.id)
  );
</script>

{#if message.role === 'agent' && componentArtifacts.length > 0}
  <div class="component-artifacts">
    {#each componentArtifacts as artifact (artifact.id)}
      <ComponentRenderer component={artifact.component} props={artifact.props} />
    {/each}
  </div>
{/if}
```

---

## 9. Implementation Phases

Each phase is designed to be shippable and to build on the previous one.

### Phase 1 — Passive Components MVP (Registry → Render)

Goal: the LLM can request passive UI artifacts and the frontend renders them.

Backend:
- Add opt-in config to the spec (`planner.rich_output`, default disabled).
- Add template generator flag (example): `penguiflow new --with-rich-output` to set `planner.rich_output.enabled: true` and include the prompt catalog.
- Define registry format and versioning (even if stored as TS initially).
- Add backend scaffolding (`penguiflow/rich_output/`: registry loading, validation helpers, tool/node helpers).
- Implement `render_component` as a PenguiFlow tool that validates `{component, props}` and calls `ctx.emit_artifact(..., artifact_type="ui_component")`.
- Validate `{component, props}` (component exists + schema validation + size caps).
- Emit `artifact_chunk` with `artifact_type=ui_component` (SSE) / `CUSTOM name="artifact_chunk"` (AG-UI), with stable IDs in `chunk.id`.
- Update `penguiflow/agui_adapter/penguiflow.py` to map planner `artifact_chunk` events into `CUSTOM name="artifact_chunk"` (currently only `artifact_stored` is mapped).
- Add `/ui/components` endpoint for registry introspection (Playground).

Frontend:
- Implement/ship `componentRegistry` + `ComponentRenderer`.
- Implement at least 3 baseline renderers: `markdown`, `json`, `echarts` (or `datagrid`).
- Render `artifact_chunk` with `artifact_type=ui_component` inline under the assistant message (for both SSE and AG-UI).

Acceptance tests:
- Unit test: invalid component rejected (backend).
- Unit test: invalid props rejected (backend).
- UI test: artifact renders for each baseline renderer.
- Config test: when `planner.rich_output.enabled` is false, meta-tools are not advertised/injected and no component artifacts are emitted.

### Phase 2 — Interactive Components MVP (Tool Call → User → Result)

Goal: the LLM can pause for user input via UI components and continue reliably.

Backend:
- Define interactive tools: `ui_form`, `ui_confirm`, `ui_select_option` (schemas from registry).
- Require a resume mechanism (HITL): if interactive UI tools are enabled, enforce `agent.flags.hitl: true` (or fail fast at startup/spec validation).
- Implement interactive tool result channel (start with Option B: resume-as-new-run).
- Emit correct tool call lifecycle events (`TOOL_CALL_START/ARGS/END/RESULT`).
- Enforce timeouts/cancellation semantics (user never responds).

Frontend:
- Render interactive components from tool calls.
- Collect user input and send result back to backend.
- Show “waiting for user” state and handle retries/cancel.

Acceptance tests:
- End-to-end: `ui_form` returns structured payload and agent continues.
- Negative: tool result rejected when schema invalid.

### Phase 3 — Composite Layouts (Reports/Dashboards)

Goal: the LLM can compose multiple components into higher-level UI artifacts.

Backend:
- Add optional “composite helpers” (e.g., `render_report`, `render_grid`) or rely on `render_component` with composite props.
- Validate nested component trees (recursive validation against registry).
- Support stable IDs for nested items (for updates).

Frontend:
- Implement composite renderers: `report`, `grid`, `tabs`, `accordion`.
- Ensure recursion is safe (depth limits) and performant (virtualization if needed).

Acceptance tests:
- Render a report containing at least one chart + one table.

### Phase 4 — Dynamic Teaching + Playground Lab

Goal: make the system self-documenting and easy to iterate.

Backend:
- Generate a compact “component catalog” prompt section from the registry (with examples).
- Add schema-on-demand tool: `describe_component(name)` to reduce prompt bloat.
- Add registry diagnostics to `/ui/components` (version, enabled components, counts).

Frontend:
- Add “Component Lab” view:
  - browse registry
  - preview payloads
  - copy/paste last artifact

Acceptance tests:
- Prompt snapshot tests (registry → prompt text stable).

### Phase 5 — Hardening (Security, Performance, Versioning)

Goal: production-grade behavior.

Backend:
- Component allowlists/denylists (by environment).
- Strict sanitization and sandboxing rules for risky renderers.
- Rate limits and size enforcement.
- Registry migration strategy (version negotiation + backward compatibility).
- Upgrade interactive runtime to Option A (single streaming run) if desired (better UX, more complex backend).

Frontend:
- Graceful fallbacks for unknown components.
- Schema-driven error UI (“LLM requested invalid props”).
- Performance: lazy-load heavy renderers, virtualize large tables.

---

## 10. Summary

### What We Build

| Component | Purpose | Lines (approx) |
|-----------|---------|----------------|
| `penguiflow/cli/playground_ui/src/lib/component_artifacts/registry.ts` | Component definitions + schemas | ~800 |
| `penguiflow/rich_output/prompting.py` | System prompt generator | ~200 |
| `penguiflow/rich_output/tools.py` | Tool metadata from registry | ~50 |
| `penguiflow/cli/playground_ui/src/lib/component_artifacts/ComponentRenderer.svelte` | Component router | ~100 |
| `penguiflow/cli/playground_ui/src/lib/stores/component_artifacts.svelte.ts` | Component artifact state management | ~150 |
| Renderers (15-20) | Individual component implementations | ~2000 total |

### Component Categories

| Category | Components | Interactive |
|----------|------------|-------------|
| Visualization | echarts, mermaid, plotly | No |
| Data | datagrid, json, metric | No |
| Document | markdown, code, latex, callout | No |
| Interactive | form, confirm, select_option | **Yes (HITL)** |
| Layout | report, grid, tabs, accordion | No |
| Media | image, html, video, embed | No |

### How the LLM Learns

1. **System prompt** — Generated from registry, describes all components
2. **Tools** — `render_component` for passive, `ui_*` for interactive
3. **Few-shot examples** — Optional, shows common patterns

### Integration with AG-UI

- **Passive artifacts** → `CUSTOM name="artifact_chunk"` with `artifact_type="ui_component"` (legacy Playground SSE uses `artifact_chunk`)
- **Interactive artifacts** → Tool calls with pause-and-wait semantics
- **State sync** → Can use `STATE_DELTA` to update component props reactively (advanced)

---

## 11. References

Internal:
- `docs/RFC_AGUI_INTEGRATION.md`
- `docs/PLAYGROUND_BACKEND_CONTRACTS.md`
- `docs/agui/README.md`
- `docs/RFC_MCP_BINARY_CONTENT_HANDLING.md`

External:
- [Apache ECharts](https://echarts.apache.org/)
- [Mermaid](https://mermaid.js.org/)
- [JSON Schema](https://json-schema.org/)
- [RFC 6902 JSON Patch](https://datatracker.ietf.org/doc/html/rfc6902)
- [AG-UI Protocol](https://docs.ag-ui.com/)
