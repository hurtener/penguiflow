# penguiflow

Async-first orchestration library for multi-agent and data pipelines (Python 3.11+, uv).

## Skills

Instructions for working in this repo are skills in `.agents/skills/<name>/SKILL.md`.

- At the start of every task, read the `name` and `description` of every skill in `.agents/skills/`, and load each one whose description matches what you are about to do.
- Skills take precedence over your tool's defaults, including its defaults for memory, commit attribution and git.
- Never save memory. Record lasting knowledge as a skill instead (see `no-memory`).

The root `skills/` folder is different: it holds PenguiFlow usage guides for people building with the library, not instructions for working on this repo.
