# Merging a phase PR

GitHub's green button has three modes. Two of them keep your commits; one destroys them.
This repo uses **Create a merge commit**.

## The three modes

```
Create a merge commit    main ──────────────●   ← the merge commit
                                ╲          ╱
                                 ●●●●●●●●●     all 9 commits kept, branch shape visible

Rebase and merge         main ──●●●●●●●●●      all 9 kept, replayed linearly, no merge commit

Squash and merge         main ──●              all 9 collapsed into 1 new commit
```

This is **not** the merge-vs-rebase argument. Merge and rebase both preserve every commit;
squash is the odd one out.

## Why squash is wrong here

Squash does not lose content — the files on `main` are byte-identical whichever button you
press. It loses the *record*.

The ground rules in `CLAUDE.md` say one task = one commit, carrying the code, its tests and
its ticked box in `GAMEPLAN.md` together. That structure only exists in history. After a
squash, `git log` can no longer tell you that `NoDecode` was added because a test caught
pydantic-settings JSON-decoding a list field, or that `.env.example` was fixed only after
the acceptance run failed to boot. You get one commit called "Phase 00" and the reasoning
is gone.

## Why merge commit over rebase

Both keep the nine commits. The merge commit additionally marks where the phase started and
ended, so:

```sh
git log --oneline --merges       # one line per phase
git log --oneline main..HEAD     # the phase's own commits, during the work
```

That is the `1 phase = 1 branch = 1 PR` isomorphism written into history. Rebase flattens
the boundary away — still fine, just less legible a year later.

## Rule of thumb

| | |
|---|---|
| Merging a phase PR | **Create a merge commit** |
| Never | Squash and merge |
| Acceptable if you dislike merge commits | Rebase and merge |

## The branch protection that backs this up

`main` is protected. Current settings:

| Setting | State | Why |
|---|---|---|
| Require a pull request before merging | on | direct pushes to `main` are rejected — including from an agent with your `gh` credentials |
| Required approvals | 0 | GitHub will not let you approve your own PR, so any non-zero value deadlocks a solo repo. Unchecking "Require approvals" *is* zero; the dropdown has no 0. |
| Allow force pushes | off | `CLAUDE.md` says never force-push `main`; this makes it mechanical rather than a promise |
| Allow deletions | off | |

The review gate is you reading the diff. No GitHub setting can enforce that one.
