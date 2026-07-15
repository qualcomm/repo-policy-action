# repo-policy-action

A GitHub composite action that enforces repository policy checks for Qualcomm open-source projects. It replaces the archived [`todogroup/repolinter-action`](https://github.com/todogroup/repolinter-action) and its underlying [`todogroup/repolinter`](https://github.com/todogroup/repolinter) engine, both of which were officially archived by TODOGroup in February 2026.

## Background

### Why this exists

Qualcomm's reusable workflow suite (`qualcomm/reusable-workflows`) previously used `todogroup/repolinter-action@v1` to enforce repository best-practice standards across open-source projects. That tool is now archived, carries known CVEs in its npm dependency chain, and has had no active development since 2021. This action is its purpose-built replacement.

Rather than forking and maintaining the original Node.js codebase, we implemented the subset of checks Qualcomm actually uses in Python — a language already familiar to the majority of contributors on the team. We also took the opportunity to drop checks that are already covered by other actions in the Qualcomm reusable workflow suite (see [Rule Coverage](#rule-coverage) below).

This action is deliberately scoped to **structural repository policy** — it does not duplicate the per-PR copyright diff check, SAST, or dependency scanning that other tools in the suite handle.

### Design decisions

**Python over shell scripts.** The checks are implementable in shell (`grep`, `find`, `jq`) but Python offers significantly better maintainability: real JSON parsing, testable units with `pytest`, clearer error handling, and familiar syntax for the team. `python3` is pre-installed on all GitHub-hosted runners so no setup step is required.

**Composite action wrapping a Python script.** The `action.yml` defines a composite action that simply invokes `python3 src/main.py`. This keeps the action self-contained in one repository, avoids Docker image build times, and makes local testing straightforward (`python3 src/main.py --repo-path .`).

**Backwards-compatible config format.** The action parses a strict subset of the `repolint.json` schema from the original repolinter tool. This means existing repos with a custom `repolint.json` override file at their root require zero changes at migration time. A simpler config format is planned for a future v2 release.

**No Scorecard duplication.** OpenSSF Scorecard (run weekly via `ossf/scorecard-action`) covers some of the same ground — License file detection, Binary-Artifacts detection. This action retains those checks as a per-PR gate (Scorecard only runs on a schedule and posts to the Security tab; it does not block merges). The two are complementary, not duplicative.

---

## Usage

```yaml
- uses: qualcomm/repo-policy-action@v1
  with:
    config_url: https://raw.githubusercontent.com/qualcomm/.github/main/repolint.json
```

This action is typically called via the `reusable-repolinter-check.yml` reusable workflow in `qualcomm/reusable-workflows` rather than directly.

### Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `config_file` | No | — | Path to a local `repo-policy.json` or `repolint.json` override at the repo root |
| `config_url` | No | Qualcomm default config URL | URL to fetch the default policy config from if no local config is found |
| `fail-on-error` | No | `true` | Exit non-zero when any `error`-level rule fails |
| `fail-on-warning` | No | `false` | Exit non-zero when any `warning`-level rule fails |
| `respect-gitignore` | No | `true` | Skip files and directories matched by the repository's `.gitignore` files when evaluating rules |

### `.gitignore` handling

By default the action honours the repository's `.gitignore` files (root and
nested), so locally-present-but-ignored paths — build output, coverage
reports, vendored dependencies — are excluded from every rule's scan. This
prevents false failures from files that are not part of the tracked source.
Set `respect-gitignore: false` (or pass `--no-respect-gitignore` when running
locally) to scan the full working tree regardless of `.gitignore`.

### Config resolution order

1. `repo-policy.json` at repo root (reserved for the forthcoming native format)
2. `repolint.json` at repo root (backwards-compatible override)
3. URL from `config_url` input (the Qualcomm org default config)

---

## Rule Coverage

### Rules implemented by this action

| Rule | Level | Notes |
|---|---|---|
| `license-file-exists` | error | LICENSE, COPYING, NOTICE, etc. |
| `readme-file-exists` | error | |
| `readme-references-license` | error | README body must contain "license" or "notice" |
| `source-qualcomm-license-headers-exist` | warning | Whole-repo scan of all source files |
| `contributing-file-exists` | warning | |
| `code-of-conduct-file-exists` | warning | |
| `changelog-file-exists` | warning | |
| `github-issue-template-exists` | warning | |
| `github-pull-request-template-exists` | warning | |
| `integrates-with-ci` | warning | |
| `test-directory-exists` | warning | |
| `binaries-not-present` | warning | Also covered weekly by Scorecard |
| Language metadata rules (×8) | warning/error | Conditional on detected language; Rust is `error`, others `warning` |

### Rules intentionally excluded

| Rule | Reason |
|---|---|
| `source-license-headers-exist` (changed files) | Covered by `copyright-license-checker-action` on PR diffs |
| `source-qualcomm-license-headers-exist` (changed files) | Same — covered by `copyright-license-checker-action` |
| `license-detectable-by-licensee` | Disabled (`off`) in the default Qualcomm config |

---

## Local Development

### Prerequisites

- Python 3.13+
- [libmagic](https://github.com/ahupp/python-magic#installation) (required by `python-magic`; pre-installed on `ubuntu-latest` runners, `brew install libmagic` on macOS)

### Set up a development environment

Dependencies are declared in `pyproject.toml` and locked in `uv.lock`.
With [uv](https://docs.astral.sh/uv/) (recommended), install the project plus
its dev tools into a managed environment:

```bash
uv sync
```

Or with the standard library `venv` + `pip`, using the generated
`requirements-dev.txt` (regenerated from the lock via `uv export`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Run checks locally

```bash
uv run src/main.py --repo-path /path/to/your/repo   # with uv
python3 src/main.py --repo-path /path/to/your/repo  # with an activated venv
```

### Run tests

```bash
uv run pytest tests/   # with uv
pytest tests/          # with an activated venv
```

> `requirements.txt` and `requirements-dev.txt` are generated from
> `pyproject.toml` + `uv.lock` via `uv export`. To change dependencies, edit
> `pyproject.toml`, then run `uv lock` and regenerate the exports — don't edit
> the requirements files by hand.

---

## Config Format (v1 — repolint.json subset)

The action parses a subset of the [repolinter v2 JSON schema](https://github.com/todogroup/repolinter/blob/main/docs/schema.md). Only rule types present in the Qualcomm default config are supported. Unsupported rule types are skipped with a warning log rather than failing.

**Supported rule types:**

| Type | Description |
|---|---|
| `file-existence` | Check that at least one file matching a glob pattern exists |
| `file-starts-with` | Check that matching files begin with a specific string or regex |
| `file-contents` | Check that matching files contain a specific string or regex |
| `no-file-type-exists` | Check that no files with a given extension exist |
| `directory-existence` | Check that at least one directory matching a pattern exists |

**Not supported (not in use):**

- `license-detectable-by-licensee` — requires the `licensee` Ruby gem
- `fix` blocks — repolinter-action never executed fixes; neither does this action

---

## Roadmap

### v1.x (currently under development)

- Full rule coverage for all checks in the Qualcomm default `repolint.json`
- Backwards-compatible `repolint.json` config format
- Per-PR CI gate with GitHub Annotations output
- Language detection via file-extension frequency counting

### v2.x (planned)

**Cleaner config format.** The `repolint.json` schema was designed for a much richer rule engine than this action needs. A simplified YAML-based `repo-policy.yml` config is planned with a cleaner schema, better error messages, and first-class support for Qualcomm-specific rule types. A conversion script and migration guide will ship alongside it. The default config in `qualcomm/.github` will be migrated to the new format; `repolint.json` parsing will be retained for at least one full major version after migration.

**Expanded language detection.** The current extension-counting heuristic will be replaced with a more accurate detection approach, potentially leveraging the GitHub Linguist data directly via the API, for better conditional rule triggering on mixed-language repos.

**Per-rule suppressions.** Allow individual rules to be suppressed inline (e.g. via a comment in the config) with a required justification string, rather than requiring a full config override file. This reduces the overhead for repos that have a legitimate reason to deviate from one specific rule.

**Structured JSON output.** Optionally emit a machine-readable JSON results file (in addition to GitHub Annotations) for integration with dashboards, compliance reporting tools, or downstream workflow steps.

---

## Versioning

This action follows semantic versioning. The major version tag (`v1`) is kept pointing to the latest `v1.x.x` release, consistent with the convention used by other Qualcomm reusable actions.

## License

repo-policy-action is licensed under the [BSD-3-clause License](https://spdx.org/licenses/BSD-3-Clause.html). See [LICENSE.txt](LICENSE.txt) for the full license text.

## Contributing

For information on how to contribute to repo-policy-action, please see [CONTRIBUTING.md](CONTRIBUTING.md).

## Getting in Contact

Please contact us via GitHub if you have questions, suggestions, or issues:

* [Report an Issue on GitHub](../../issues)
* [Open a Discussion on GitHub](../../discussions)
