<!--
PR-Titel im Conventional-Commits-Format: `type(scope): subject`, z. B. `fix: parse missing platform field`.
Erlaubte Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert; Breaking Change: `feat!: …`.
pr-title-lint.yml erzwingt das, und beim Squash-Merge wird der Titel zur Commit-Message, aus der
release.yml den Changelog baut.
-->

## Summary

<!-- Was ändert dieser PR und warum? -->

## Checklist

- [ ] PR-Titel im Conventional-Commits-Format
- [ ] `uv run pytest tests/ -q` grün (neue Features mit Tests gegen gemockte HTTP-Responses)
- [ ] `uv run ruff check .` sauber
