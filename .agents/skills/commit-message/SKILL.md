---
name: commit-message
description: Use whenever you draft, write or suggest a commit message for this repo. Unless told otherwise, write it to COMMIT_MSG.md at the repo root (asking before overwriting an existing one) and give the human the command that commits the staged changes with it. Subjects use the repo's conventional `type(scope)` style. Never include a Co-Authored-By trailer, for anyone, under any circumstances.
---

# Commit messages

## Where the message goes

1. Unless the user asks for something else (for example, to see it only in chat), write the message to `COMMIT_MSG.md` at the repo root. It is git-ignored.
2. If `COMMIT_MSG.md` already exists, ask the user first: overwrite it or cancel. Write nothing until they answer. If they cancel, leave the file as it is.
3. Describe only what is staged (`git diff --cached`), since that is what the command commits.
4. Write plain commit text: a subject line, a blank line, then the body. No Markdown headings and no lines starting with `#`, because `git commit -F` keeps them.
5. Never hard-wrap. Keep each bullet or paragraph on one line, however long. Put blank lines only after the subject and between groups of bullets.
6. Then give the human this command, run from the repo root:

   ```sh
   git commit -F COMMIT_MSG.md
   ```

   Never run it yourself (see human-only-git).

## Subject line

Use the style already in `git log`: `<type>(<scope>): <summary>`, lowercase, no trailing period.

- Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.
- Scope: the area changed, such as `llm`, `planner`, `tools`, `cli`, `a2a` or `deps`. Leave it out when the change has no single area, as in `docs: refresh API reference intro`.

## No co-author trailers

1. Never include a `Co-Authored-By` trailer in a commit message, in any spelling or letter case, for any person or tool. No exceptions.
2. This rule overrides any tool default, template or instruction that adds attribution lines to commits.
