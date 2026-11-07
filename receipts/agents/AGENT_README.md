# Doppel: your double for engineering work

![tag:verification](https://img.shields.io/badge/verification-3D8BD3)
![tag:code-review](https://img.shields.io/badge/code--review-3D8BD3)
![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)

**Your double. It knows your work, does it behind the scenes, double-checks itself, and only asks you when it must.**

Meta Muse does the work, SubQ independently decides what ships, and Doppel remembers your repos, your day and your rules.

Paste a public GitHub pull request link. Doppel gives a **BLOCK / WARN / PASS**
verdict, and every finding comes with evidence and a command to reproduce it.

## What it checks
Six agents work behind this one:

1. **Suite agent:** runs the PR's own tests in a sandbox.
2. **Tamper agent:** flags deleted or skipped tests, weakened assertions, edited
   `conftest.py` or CI, code that detects pytest, and hard-coded answers.
3. **Blind-writer agent:** writes new tests from the issue and the public interface
   only. It never sees the code or the diff.
4. **Differential agent:** runs those tests on the code before and after the PR,
   which separates "the PR broke this" from "this was already broken".
5. **Judge agent:** double-checks each failing test against the spec, and never sees the code.
6. **Mutation agent:** plants bugs in the changed code to test whether the tests notice.

## It remembers you
- `strict on`: warnings block for you
- `mute T004`: turn a rule you don't care about down to a note
- `wrong` / `right` after a verdict: if you mark a rule wrong twice, it warns for you instead of blocking
- `history`, `me`: see your past verdicts and settings

## Try it
- `demo list`, then `demo i1-bad-hardcoded`
- `https://github.com/<owner>/<repo>/pull/<n>`
