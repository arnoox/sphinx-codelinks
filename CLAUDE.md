# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This repository already has a detailed **AGENTS.md** at the repo root — read it first for
full architecture diagrams, event-handler tables, commit/PR conventions, and common-pattern
recipes (adding a language, a marker type, a CLI command, a config option). This file only
covers what's needed to get moving quickly.

## What this project is

sphinx-codelinks is a Sphinx extension providing fast source-code traceability for
Sphinx-Needs: it scans source files (C++, Python, C#, Rust, TypeScript, JavaScript, Go, Bash, YAML, JSON) for
marker comments via tree-sitter, and generates Sphinx-Needs items / RST that link
documentation back to exact source locations.

## Commands

All commands run through `tox` (uses `tox-uv`).

```bash
# Run default test env (py312-sphinx8-needs5)
tox

# List all test env combinations (py{312,313,314}-sphinx{7,8,9}-needs{5,6,7,8})
tox -a

# Run a specific env / file / test
tox -e py312-sphinx8-needs5
tox -e py312-sphinx8-needs5 -- tests/test_analyse.py
tox -e py312-sphinx8-needs5 -- tests/test_analyse.py::test_function_name

# Update syrupy snapshots
tox -e py312-sphinx8-needs5 -- --snapshot-update

# Type check / lint / format
tox -e mypy
tox -e ruff-check
tox -e ruff-fmt
pre-commit run --all-files

# Docs
tox -e docs-clean
tox -e docs-update
BUILDER=linkcheck tox -e docs-clean
tox -e docs-live

# End-to-end demo (analyse -> write RST -> build docs)
tox -e demo
```

The CLI itself is installed as `codelinks` (`codelinks analyse <config.toml>`,
`codelinks write rst <input.json> --outpath <file>`).

## Architecture

Pipeline: **Source Files → Discovery → Parsing → Analysis → Results (JSON) → RST Generation**

- `source_discover/` — finds source files by include/exclude patterns, respects `.gitignore`.
  `COMMENT_FILETYPE` dict in `source_discover/config.py` maps comment types to file extensions.
- `analyse/utils.py` — per-language setup via `init_tree_sitter(comment_type)`, an if/elif chain
  that pairs each comment type with a tree-sitter grammar (`tree_sitter_cpp`, `tree_sitter_python`,
  etc.) and a per-language `*_QUERY` constant. Scope detection uses the `SCOPE_NODE_TYPES` dict
  to map comment types to their scope node types (functions, classes, etc.).
- `analyse/oneline_parser.py` — tree-sitter based parser extracting comment marker nodes.
- `analyse/analyse.py` — orchestrates discovery + parsing + analysis into `analyse/models.py`
  Pydantic result models.
- `needextend_write.py` — turns analysis JSON into RST with Sphinx-Needs `needextend`
  directives.
- `config.py` — Pydantic v2 config models (`AnalyseConfig` etc.), loadable from TOML.
- `sphinx_extension/source_tracing.py` — the Sphinx extension `setup()`; wires into Sphinx
  build events (`config-inited`, `builder-inited`, `env-before-read-docs`,
  `html-collect-pages`, `html-page-context`, `build-finished`) to register sphinx-needs extra
  options/types, generate standalone traced-source HTML pages, and inject CSS
  (`sphinx_extension/ub_sct.css`). See AGENTS.md for the full event table and mermaid diagram.

Adding a new language, marker type, CLI command, or config option each follow a
short recipe documented in AGENTS.md under "Common Patterns" — follow those rather than
inventing a new approach. To add a language: update `COMMENT_FILETYPE`, add a case to
`init_tree_sitter()` wiring the tree-sitter grammar, add scope types to `SCOPE_NODE_TYPES`,
define a `*_QUERY` constant, and add test data.

## Code style

- Ruff for lint/format (strict rule set incl. `S`, `PL`, `PTH`, `SIM`, `SLF`; see
  `pyproject.toml` for per-file ignores).
- Mypy strict mode (`disallow_any_*`, `disallow_untyped_*`); relaxed for `tests/*` and
  `sphinx_codelinks.*` via overrides in `pyproject.toml`.
- Full type annotations everywhere; Pydantic models (frozen where possible) for config/data.
- Sphinx-style docstrings (`:param:`, `:return:`, `:raises:`), no types in docstrings.
- Prefer pure functions and immutable data structures.

## Testing

- `pytest` with fixtures in `tests/conftest.py`; test data in `tests/data/`; Sphinx
  integration tests use real minimal Sphinx projects in `tests/doc_test/`.
- `syrupy` for snapshot testing of complex outputs (JSON, doctrees) — use
  `snapshot.assert_match()` and re-run with `--snapshot-update` when output intentionally
  changes.
- Use `@pytest.mark.parametrize` for multi-language / multi-scenario tests.
