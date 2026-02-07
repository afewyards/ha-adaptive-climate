# Ruff + Pyright Setup

## Tools

- **Ruff**: linting + formatting (replaces flake8, isort, pyupgrade)
- **Pyright**: strict type checking
- **Pre-commit**: gates every commit

## Config (`pyproject.toml`)

### Ruff
- Rules: E, F, W, I, UP, B, SIM, N, RUF
- Target: py311, line-length 120
- Exclude: `.worktrees/`

### Pyright
- `typeCheckingMode = "strict"`, `pythonVersion = "3.11"`
- Per-file `# type: ignore[code]` for HA framework quirks

### Pre-commit (`.pre-commit-config.yaml`)
- `ruff check --fix` → `ruff format` → `pyright`

## Execution (one commit per step)

1. Add config files
2. `ruff format` — auto-format entire codebase
3. `ruff check --fix` — auto-fix lint violations
4. `ruff check` — manually fix remaining violations
5. `pyright` — fix type errors (bulk of work)
6. `pre-commit install`
7. Update CLAUDE.md

## Scope exclusions

- No CI pipeline (pre-commit only)
- Tests: Ruff yes, pyright basic mode (not strict)
- No runtime dependency changes
