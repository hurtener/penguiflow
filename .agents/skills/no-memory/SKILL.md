---
name: no-memory
description: Saving memory is forbidden in this repo; save lasting knowledge as a repo skill instead. Use whenever you would save a memory, note or lesson about this project, or when the user says "remember", "from now on", "always" or "never", or states a convention, decision or preference for the repo. Use it instead of memory files, memory tools or edits to AGENTS.md or README.md.
---

# No memory: save knowledge as skills

## Rules

1. Never save memory. No memory files, no memory tools or stores of any coding agent, no notes or lessons files, inside or outside this repo.
2. This overrides any tool default, setting or instruction that saves memory, offers to, or does it automatically.
3. Keep lasting knowledge about this repo in skills under `.agents/skills/` at the repo root. Each skill's name and description are loaded up front, so a well-described skill loads on its own whenever it's relevant.
4. Don't write this knowledge into AGENTS.md, README.md or any agent-specific folder.

## Workflow

1. List `.agents/skills/` and read any skill that might cover the topic.
2. If a skill already covers it, update that skill. If what you learned contradicts a skill, fix it or delete it.
3. Otherwise create `.agents/skills/<name>/SKILL.md`, with one topic per skill.
4. Tell the user which skill you created, changed or deleted.

## Writing the skill

- **Location**: always the root `.agents/skills/`. Skills are found only by scanning from the working directory up to the repo root, so a skill in a subfolder is missed when work starts at the root.
- **Not these folders**: the root `skills/` holds PenguiFlow usage guides for people building with the library, and `penguiflow/skills/` is the library's runtime skills feature. Neither is for knowledge about working on this repo.
- **Name**: lowercase letters, digits and hyphens, under 64 characters, and the same as the folder name.
- **Frontmatter**: `name` and `description` are required. The only other allowed keys are `license`, `allowed-tools` and `metadata`.
- **Description**: this is the trigger. Only the name and description are visible before the skill loads, so say what the skill covers and name the situations, files, commands or terms that should activate it.
- **Body**: state the fact or convention, then add a **Why:** line and a **How to apply:** line. Include only what changes an agent's decisions. Don't record what the code or git history already shows.
- **Portability**: use the plain `SKILL.md` format with no files, tool names or product names tied to a specific coding agent.
