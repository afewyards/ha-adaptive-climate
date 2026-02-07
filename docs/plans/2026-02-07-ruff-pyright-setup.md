# Ruff + Pyright Setup Implementation Plan — DONE

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Ruff (linting + formatting) and pyright (strict type checking) with pre-commit hooks, fixing all existing violations.

**Architecture:** Ruff handles style/imports/bugs, pyright handles types. Both configured in `pyproject.toml`. Pre-commit gates every commit. Pyright strict on our code, `# type: ignore` at HA import boundaries. Tests get Ruff but only pyright basic mode.

**Tech Stack:** Ruff, pyright, pre-commit

---

### Task 1: Add Ruff + Pyright config to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add Ruff config**

Append to `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py311"
line-length = 120
exclude = [".worktrees"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "N", "RUF"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["N802", "N803", "N806"]
```

**Step 2: Add pyright config**

Append to `pyproject.toml`:

```toml
[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "strict"
exclude = [".worktrees", "tests"]
reportMissingTypeStubs = false
reportUnknownMemberType = false
reportUnknownArgumentType = false
reportUnknownVariableType = false
reportUnknownParameterType = false
reportMissingParameterType = false

[tool.pyright.defineConstant]
TYPE_CHECKING = true
```

Note: We disable `reportUnknown*` and `reportMissingTypeStubs` because HA core is untyped. This keeps strict mode for our logic (return types, assignments, protocols) without drowning in HA boundary noise.

**Step 3: Verify config parses**

Run: `ruff check --config pyproject.toml custom_components/ --statistics 2>&1 | head -20`
Run: `pyright --version`

**Step 4: Commit**

```
chore: add ruff and pyright configuration
```

---

### Task 2: Create .pre-commit-config.yaml

**Files:**
- Create: `.pre-commit-config.yaml`

**Step 1: Create config**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.9.6
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/RobertCraiworktrees/pyright-python
    rev: v1.1.394
    hooks:
      - id: pyright
```

**Step 2: Verify config**

Run: `pre-commit run --all-files 2>&1 | tail -20` (will fail — that's expected, we haven't fixed violations yet)

**Step 3: Commit**

```
chore: add pre-commit config for ruff and pyright
```

---

### Task 3: Run ruff format on entire codebase

**Files:**
- Modify: all `.py` files in `custom_components/` and `tests/`

**Step 1: Run formatter**

Run: `ruff format custom_components/ tests/`

**Step 2: Verify tests still pass**

Run: `pytest tests/ -x -q`

**Step 3: Commit**

```
style: apply ruff formatting to entire codebase
```

---

### Task 4: Run ruff check --fix for auto-fixable violations

**Files:**
- Modify: various `.py` files

**Step 1: Run auto-fix**

Run: `ruff check --fix custom_components/ tests/`

**Step 2: Check what's left**

Run: `ruff check custom_components/ tests/ --statistics`

**Step 3: Verify tests still pass**

Run: `pytest tests/ -x -q`

**Step 4: Commit**

```
style: auto-fix ruff lint violations
```

---

### Task 5: Manually fix remaining ruff violations

**Files:**
- Modify: files reported by `ruff check`

**Step 1: Get violation list**

Run: `ruff check custom_components/ tests/`

**Step 2: Fix each violation**

Common fixes:
- `UP` rules: replace `Dict`/`List`/`Tuple` imports with builtins (known in `solar/solar_gain.py` and some managers)
- `B` rules: mutable default arguments, except clauses
- `SIM` rules: simplifiable if/else, ternary opportunities
- `N` rules: naming convention violations (skip in tests via per-file-ignores)

**Step 3: Verify clean**

Run: `ruff check custom_components/ tests/`
Expected: 0 violations

Run: `pytest tests/ -x -q`

**Step 4: Commit**

```
style: fix remaining ruff lint violations
```

---

### Task 6: Fix pyright type errors

**Files:**
- Modify: files in `custom_components/adaptive_climate/`

**Step 1: Get baseline error count**

Run: `pyright custom_components/adaptive_climate/ 2>&1 | tail -5`

**Step 2: Fix errors by category**

Priority order:
1. Missing return type annotations on public methods
2. Missing parameter type annotations
3. Incompatible types in assignments
4. Protocol implementation mismatches
5. `hass.data` access patterns — add `# type: ignore[index]` with comment

For HA boundary code, use specific ignore codes:
```python
self._hass.data[DOMAIN]["coordinator"]  # type: ignore[index]
```

**Step 3: Verify clean**

Run: `pyright custom_components/adaptive_climate/`
Expected: 0 errors

Run: `pytest tests/ -x -q`

**Step 4: Commit**

```
refactor: fix pyright strict type errors
```

Note: This task is the biggest. If error count is >200, split into sub-commits by directory (e.g., `adaptive/`, `managers/`, core files).

---

### Task 7: Install pre-commit hooks and verify

**Step 1: Install hooks**

Run: `pre-commit install`

**Step 2: Verify hooks work**

Run: `pre-commit run --all-files`
Expected: all checks pass (ruff, ruff-format, pyright)

**Step 3: Commit**

```
chore: verify pre-commit hooks pass
```

---

### Task 8: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add lint commands to Commands section**

```markdown
ruff check custom_components/ tests/       # lint
ruff format custom_components/ tests/       # format
pyright custom_components/adaptive_climate/ # type check
pre-commit run --all-files                  # all checks
```

**Step 2: Update Code Style section**

Add:
- Ruff enforces style (line-length 120, import sorting, pyupgrade)
- Pyright strict on source, basic on tests
- Use `# type: ignore[specific-code]` with comment for HA boundaries — never bare `# type: ignore`

**Step 3: Commit**

```
docs: add ruff and pyright to CLAUDE.md
```
