Design a single-page, developer-focused web app called “PenguiFlow Agent Studio”.

This app is bundled with the penguiflow CLI and serves two main purposes:

Agent Spec & Generator — let developers load/edit a YAML spec and run penguiflow generate visually.

Dev Playground — let them run their agent through a realistic chat + trajectory UI (penguiflow dev), with live planner telemetry.

The target audience is backend / data / ML engineers, not end-users. The experience should be sleek, modern, calm, and make it easy to understand:

how an agent is defined (spec & templates)

how an agent is wired (tools, flows, planner, LLMs)

how an agent behaves at runtime (chat, trajectory, events).

0. Technical & Structural Constraints

Implemented as a Svelte 5 SPA using the Runes API only: $state, $derived, $effect, $props. Do not rely on legacy stores or export let.

The SPA is prebuilt and bundled into the CLI, served by penguiflow dev. Users do not run npm themselves.

Component boundaries should map clearly to:

SpecPanel

GeneratorPanel

PlaygroundChat

PlaygroundTrajectory

PlaygroundEvents

ConfigPanel

ProjectSwitcher / AgentHeader

Layout is a three-column layout and should remain conceptually fixed (feel free to adjust paddings, card sizes, etc., but not the macro regions):

Left Sidebar – Project & Spec

Center Column – Playground Chat & Trajectory (“live run” area)

Right Column – Telemetry & Config

Think of it as a minimal IDE for agents:

Left: definition (what this agent is).

Center: behavior (what this agent does).

Right: introspection (how this agent works internally).

1. Overall Aesthetic & Mood (“Warm Floating Canvas”)
1.1 Global look

Aim for a light, calm “floating canvas” aesthetic, often seen in modern SaaS tools.

Background of the entire app:

Very light warm beige / cream (e.g. around #F5F1EB–#F7F3EC), not pure white or cool grey.

Primary surfaces (cards, panels, code areas):

White or almost-white with a slightly warm tint (e.g. #FCFAF7).

Typography:

Clean sans-serif (Inter / SF-like).

Headings: dark grey (#111–#222), medium or semibold.

Body: regular weight, mid-grey (#555–#777).

Use a clear hierarchy but keep density comfortable — information-dense but not cramped.

1.2 Color system

Primary accent: muted teal or soft blue (e.g. #31A6A0 or #5B8DEF) for primary actions, active indicators, links, and success states.

Secondary accent: very pale mint or soft peach used for subtle backgrounds and highlights.

Status colors:

Success: soft green/teal.

Warning: muted amber.

Error: gentle desaturated red (no neon).

Validation & error messaging in spec and generator: use amber/red text + icons, but still soft and readable (no harsh blocks).

1.3 Surfaces & depth

Avoid harsh solid borders and sharp corners.

Cards and major panels should have:

Large border radius (16–24px).

Soft, blurred shadows with low opacity and relatively large blur radius.

Light inner strokes only when necessary (very low-contrast border).

Inputs and buttons are mostly pill-shaped or heavily rounded.

No “raw terminal” look: everything is in structured cards, panels, timelines, tags, and chips.

2. Left Sidebar — Project & Spec

(SpecPanel + GeneratorPanel)

The left column sits on a slightly darker warm strip of the background to visually separate it, but it’s still very light.

2.1 Top: Project header card

At the top of the sidebar, place a floating card summarizing the active project/spec:

Agent/project name (from agent.name) as main heading.

Short description from agent.description in smaller, lighter text.

A template badge pill (e.g. tmpl: analyst, tmpl: wayfinder, tmpl: enterprise) using a pastel accent color.

Small status pill: Generated, Needs generate, or Dirty spec in a tiny rounded chip.

Flags as pill tags:

streaming, hitl, a2a, memory — each a small pastel pill with soft outline and darker text.

Below that header, show mini counters as part of the same card or immediately below in a row of tiny tiles:

TOOLS: N

FLOWS: M

SVCS: x/y enabled

with small icons and subtle labels.

2.2 Section A – Spec Overview & Counters

Either integrated into the header card or as a second card, show:

Status of services from spec:

memory_iceberg, lighthouse, wayfinder with tiny icons and text (enabled) / (disabled).

LLMs summary:

Primary LLM (llm.primary.model & provider).

Summarizer / reflection models with “inherits from primary” badges if they fall back.

Everything in this section uses small uppercase section labels in mid-grey, with content in darker text.

2.3 Section B – Spec Editor / Viewer (tabbed card)

A large vertical card fills most of the sidebar height:

Top row: tabs styled as rounded pills:

Tab 1: Spec YAML

Tab 2: Validation

Selected tab has a soft tinted background and either a tiny colored underline or dot.

Spec YAML tab content:

Embedded code editor area for agent.yaml with:

Line numbers.

YAML syntax coloring.

A warm white background and very subtle border inside the card.

Read-only by default is fine, but design should not forbid editing (e.g. show an “Edit” mode toggle).

Validation tab content:

Structured list of validation messages.

Each error row shows:

Icon (❌ or ⚠️).

file:line (e.g. agent.yaml:42).

Clear message.

Optional suggestion text in smaller font.

When validation passes:

Show a ✅ “Spec valid” badge near the top.

Optionally highlight relevant parts of the summary card above (e.g., fade-in “Valid” pill).

2.4 Section C – Generator Controls

At the bottom of the sidebar, add a Generator card containing:

Two pill-shaped buttons:

“Validate spec” — secondary / ghost style:

Transparent background, accent-colored border and text.

“Generate project” — primary style:

Filled with primary accent color (teal/blue), white text, subtle shadow.

Below, a vertical stepper representing generator phases:

Validation

Scaffold

Tools

Flows

Planner

Tests

Config

Each step row:

Left: small circular icon containing step number or icon.

Middle: step label text.

Right: status icon:

Neutral: grey dot.

Running: tiny spinner.

Success: green check.

Error: red exclamation.

Active step:

Slightly tinted background (pale accent).

Thin accent-colored border or line on the left.

At the bottom or right under the stepper, a compact text log area:

Short lines like:

Phase 3: Generated 5 tools.

Phase 4: FlowBundle for customer_360_flow.

3. Center Column — Dev Playground

(PlaygroundChat + PlaygroundTrajectory)

This is the core interactive area. It’s one big floating canvas card where chat, responses, and trajectories live.

3.1 Overall canvas card

Place a large central card that occupies most of the column height:

Background: white / very pale warm tone.

Large corner radius.

Soft drop shadow.

Inner padding: comfortable enough for chat bubbles and a header area.

Inside this card, split functionally:

Header strip at the top.

Scrollable messages / content area in the middle.

Docked input bar at the bottom (inside the card).

3.2 Chat header strip

At the top of the card:

A pill-shaped agent chip centered or left-aligned:

Contains agent name and a tiny green dot + text like Active.

Same style as other pills: rounded, white, light shadow.

On the right of the header strip (or upper right of the card), a subtle DEV PLAYGROUND text button:

Ghost pill style, small caps.

Accent on hover but not a big call-to-action.

Optional small streaming status pill (e.g. streaming).

3.3 Messages area (PlaygroundChat content)

The central portion of the card (between header and input) is a scrollable messages area.

When empty:

Center a minimal line icon (star/spark).

Display soft grey text:

Ready to test agent behavior.
Type a message below to start a run.

This empty state disappears once the first message is sent.

When messages exist:

User and agent messages appear as chat bubbles within this card:

Agent messages:

Left-aligned.

Bubble background: plain white.

Rounded corners (16–20px radius).

Light shadow or very subtle outline.

Optionally, a tiny “Agent” label above the first bubble of a group in small, light grey text.

User messages:

Right-aligned.

Bubble background: very light teal/blue tint.

Same corner radius as agent bubbles.

No heavy border; rely on background color.

Vertical spacing:

Small gaps between bubbles in the same turn.

Slightly larger gaps between different speaker turns.

Optional per-message metadata:

A small, low-contrast line under or above the bubble with:

trace_id (or truncated version).

Latency (e.g. 456 ms).

Tiny “View trajectory” link that scrolls down to the relevant trajectory section.

Streaming indicator:

For in-progress agent messages, show animated ellipsis or a subtle “typing” indicator either at the end of the bubble or as a small line.

Scrolling behavior:

Messages area scrolls inside the card.

The bottom input bar remains fixed within the card.

Add a soft internal shadow or gradient at the top edge of the input bar, suggesting that messages scroll behind it.

3.4 Input bar (inside the card, bottom-docked)

The input bar is inside the same central card, not floating separately over the background.

Style:

A large, pill-shaped input that spans nearly the width of the card (respect card padding).

White background, subtle inner shadow.

Strong radius (pill).

Slight shadow to stand out within the card.

Content:

Multi-line textarea or input field with placeholder such as:

Ask your agent: e.g. “Generate a weekly sales summary agent” or “Run the customer_360_flow for ACME”…

On the right, a send button:

Icon-only (paper plane) or icon + text.

Primary accent color fill.

Rounded, with a slight shadow.

Behavior:

When a request is in flight, disable the input and button:

Slight opacity reduction.

Possibly show a tiny loader inside the button.

Automatically scroll messages view to keep the latest messages visible.

3.5 Trajectory Timeline (PlaygroundTrajectory)

The Execution Trajectory appears below the messages section in the same center column (can be inside the main card or as a second card under it; pick whichever is clearer but keep separation).

Title row:

Label: Execution Trajectory.

Small pill with current trace_id (if any).

Timeline:

Render a vertical timeline of steps representing the agent’s run.

Each step shows:

Node name (e.g. search_docs, call_lighthouse, emit_flow, reflect_answer) as main label.

Thought (LLM reasoning snippet) if available.

Latency badge (e.g. 123 ms) with accent color if fast, amber/red tinted if slow.

Reflection info if enabled:

Score (e.g. score: 0.85).

Pass/fail indicator.

Visual grouping:

Sequential steps: simple vertical line connecting step dots.

Parallel groups: container grouping multiple steps side-by-side or stacked with a label Parallel and a subtle border or background tint.

Expandable details:

Each step has a “Details” toggle/chevron.

Expanded view shows:

args as pretty-printed JSON in a small code block.

result as pretty-printed JSON in another code block.

Error states:

If a step fails:

Highlight the step background with a pale red/pink tint.

Show a clear error message.

Optionally, display PlannerEvent error payload (summarized).

4. Right Column — Events & Config

(PlaygroundEvents + ConfigPanel)

The right column contains two main vertical sections, each as a floating card on the warm background.

4.1 Planner Events (PlaygroundEvents)

Top card: Planner Events.

Header:

Title: Planner Events.

Small icons/buttons on the right:

Filter icon opening event_type filter dropdown (e.g. step_start, step_complete, reflection, error).

Toggle button Pause stream / Resume stream (pill-style).

Body:

Empty state:

When no events yet, center soft icon and text “No events yet”, plenty of padding.

When events exist:

Display as a clean list or table:

Columns/fields:

Timestamp.

Event type.

Node name.

Latency (if present).

Short message / payload summary.

Use small, pastel pills for event type (e.g. reflection, step_complete).

Latency highlighting:

Normal: neutral text.

> p95: gently tinted amber.

> p99: gently tinted red.

Overall, this panel should feel like a structured firehose viewer: compact, legible, and easy to scan.

4.2 Config & Catalog (ConfigPanel)

Below Planner Events (or in a separate tabbed area inside the same card), show Config & Catalog.

Structure this card into three sections, each with its own small subdivision:

Planner Config

Read-only summary of key planner parameters derived from spec:

max_iters

hop_budget

absolute_max_parallel

Reflection configuration (enabled? model? max_revisions? quality_threshold?)

Display each as a small tile with label and value, using light tiles with rounded corners.

LLM & Services

Show:

Primary LLM model & provider.

Summarizer model.

Reflection model.

Indicate inheritance from primary with small labels like inherits primary when appropriate.

Services from spec:

memory_iceberg, lighthouse, wayfinder.

Show each service with:

Name.

Base URL (if set).

Status pill: enabled / disabled.

Tool Catalog

Compact list of registered tools:

Name (e.g. stock_lookup).

Short description.

side_effects value.

Tags (e.g. finance, search, external).

Tags are rendered as small pastel pill chips with soft outlines, not heavy solid badges.

This column is read-only in v2.6; it’s for observability and understanding, not editing.

5. Visualizing State & Flows

The UI should make the overall agent pipeline tangible without surfacing raw Python:

Conceptual flow:

Spec → 2. Generator → 3. Generated Project → 4. Playground.

Represent this in the UI:

Generator phases (Validation → Scaffold → Tools → Flows → Planner → Tests → Config) act as a mini static pipeline on the left.

Execution Trajectory in the center acts as the dynamic pipeline at runtime.

The user should have the intuitive sense:

“The spec defines the static graph and templates.
The playground shows the actual run (nodes, tools, flows) with live telemetry.”

6. User Flows to Support

Design the UI so these journeys feel natural and smooth.

6.1 New Agent from Spec

Developer provides or writes a new agent.yaml.

Clicks “Validate spec”:

Sees validation errors with clear file:line messages.

Fixes errors, re-validates → sees ✅ “Spec valid”.

Clicks “Generate project”:

Stepper shows phases progressing (Validation → Scaffold → … → Config).

Any failure is clearly marked on that phase.

Once generation is complete:

Developer runs penguiflow dev (or it’s already running) and switches attention to the Chat and Trajectory sections in the center.

They now test the newly generated agent live.

6.2 Debug Existing Agent

Developer runs penguiflow dev . in an existing project.

If no spec is found, left sidebar gracefully shows Spec not found / read-only summary from code or similar.

Developer sends queries via the Chat panel.

They observe:

Execution Trajectory timeline in the center.

Planner Events stream in the right.

Planner Config & Tool catalog to the right for context.

To debug, they correlate:

“Why did it call this tool?” → look at the relevant timeline step.

“What reflection score did this step get?” → inspect step metadata.

“Which template & flags is this agent built with?” → look at spec summary & badges in the left header.

6.3 Reflection & Memory Awareness

When reflection and/or memory are enabled in the spec:

Show small pills like Reflection enabled and Memory enabled in appropriate places (spec summary header, Config panel).

In the trajectory:

Display reflection scores per step (score, pass/fail).

In Config panel:

Note that memory is active, with quick reference to the memory prompt behavior (at least in text).

7. Tone & Experience

The interface should teach by showing:

Developers should be able to grasp PenguiFlow concepts—tools, flows, planner, reflection, memory—just by interacting with the UI, even before reading docs.

Avoid noisy terminal and raw logs:

Prefer structured cards, timelines, tags, chips, and pill-style controls.

Use icons and subtle color cues, not walls of monospaced text.

Keep interactions one or two clicks away between:

Spec → Generator → Playground → Events/Config.

No part of the experience should feel buried or obscure. It should feel like a small, opinionated, warm, and friendly IDE for agents.