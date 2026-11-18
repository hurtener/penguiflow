---
name: human-only-git
description: Use before running any git command in this repo, and whenever work is finished and ready to be committed. Agents must never stage, commit, push or merge; a human does those.
---

# Human-only git

## Rules

Never run these commands, in any form or with any flags:

- `git add` (and `git stage`)
- `git commit` (including `--amend`)
- `git push`
- `git merge`

This also covers commands that do the same thing implicitly: `git pull` (merges), `git rebase`, `git cherry-pick`, `git revert` and `git am` (create commits).

These are fine: read-only git such as `git status`, `git diff`, `git log`, `git show` and `git blame`.

## When work is done

Stop before staging. Tell the human which files were added, changed or deleted, so they can review, stage and commit themselves.
