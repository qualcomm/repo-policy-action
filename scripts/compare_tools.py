#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause

"""Compare repo-policy-action vs repolinter across Qualcomm orgs.

Usage:
    python3 scripts/compare_tools.py [--workers N] [--output results.json]

Enumerates public, non-fork repos across quic, qualcomm, qualcomm-linux,
qualcomm-qrb-ros, audioreach, qualcomm-zephyr (excluding linux-kernel and
linux-kernel-topics), clones each into a temp dir, runs both tools, and
reports discrepancies.

Highlights any case where RPA emits an ERROR that repolinter does not.

Environment:
    GITHUB_TOKEN   GitHub.com PAT with read:org + public_repo scopes
    REQUESTS_CA_BUNDLE  Path to CA bundle for Python requests SSL
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add repo-policy-action src to path
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from config import load_config
from language_detector import detect_languages
from reporter import Reporter
from rules import run_all_rules

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

CONFIG_URL = (
    "https://raw.githubusercontent.com/qualcomm/.github/main/repolint.json"
)

ORGS = [
    "quic",
    "qualcomm",
    "qualcomm-linux",
    "qualcomm-qrb-ros",
    "audioreach",
    "qualcomm-zephyr",
]
SKIP_REPOS = {"linux-kernel", "linux-kernel-topics"}

# Rules repolinter supports but RPA intentionally skips
SKIP_RULE_TYPES = {"license-detectable-by-licensee"}

_TOKEN = os.environ.get("GITHUB_TOKEN", "")
_CA_BUNDLE = os.environ.get(
    "REQUESTS_CA_BUNDLE",
    str(Path.home() / "certs" / "combined-ca-bundle.pem"),
)
# Point requests at the corporate CA bundle
os.environ["REQUESTS_CA_BUNDLE"] = _CA_BUNDLE


def _gh_get(url: str) -> Any:
    """GET a GitHub API URL with auth, returning parsed JSON."""
    import urllib.request, urllib.error
    req = urllib.request.Request(url)
    if _TOKEN:
        req.add_header("Authorization", f"token {_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    # Use curl so the corporate cert bundle is used transparently
    result = subprocess.run(
        ["curl", "-sf", "--cacert", _CA_BUNDLE,
         "-H", f"Authorization: token {_TOKEN}",
         "-H", "Accept: application/vnd.github+json",
         url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed for {url}: {result.stderr[:200]}")
    return json.loads(result.stdout)


def get_repos(org: str) -> list[dict]:
    """List public, non-fork repos for a github.com org via the API."""
    repos: list[dict] = []
    page = 1
    while True:
        batch = _gh_get(
            f"https://api.github.com/orgs/{org}/repos"
            f"?type=public&per_page=100&page={page}"
        )
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return [
        r for r in repos
        if not r.get("fork")
        and r.get("name") not in SKIP_REPOS
    ]


def clone_repo(org: str, name: str, dest: Path) -> bool:
    """Shallow-clone a public repo into dest. Returns True on success.

    The token is deliberately NOT embedded in the URL: git would persist it
    in plaintext into the clone's .git/config (and process args). All target
    repos are public, so anonymous cloning is sufficient.
    """
    url = f"https://github.com/{org}/{name}.git"
    result = subprocess.run(
        ["git", "clone", "--depth=1", "--quiet", url, str(dest)],
        capture_output=True, text=True,
        env={**os.environ, "GIT_SSL_CAINFO": _CA_BUNDLE},
    )
    return result.returncode == 0


def run_rpa(repo_path: str, config: dict) -> dict[str, dict]:
    """Run RPA and return {rule_name: {passed, level, message}}."""
    reporter = Reporter()
    languages = detect_languages(repo_path)
    results = run_all_rules(
        repo_path=repo_path,
        config=config,
        languages=languages,
        reporter=reporter,
    )
    return {
        r.rule_name: {
            "passed": r.passed,
            "level": r.level,
            "message": r.message,
        }
        for r in results
    }


def run_repolinter(repo_path: str) -> tuple[dict[str, dict], dict]:
    """Run repolinter on repo_path.

    Returns ``(condensed, raw)`` where ``condensed`` maps rule name to
    ``{passed, level}`` for rules that were actually evaluated, and ``raw``
    is repolinter's full parsed JSON (empty dict if it could not be parsed).
    The raw payload is preserved so a future run can be diffed in detail
    rather than only against the condensed pass/fail summary.
    """
    result = subprocess.run(
        ["npx", "repolinter", "lint", repo_path,
         "--format", "json",
         "--rulesetUrl", CONFIG_URL],
        capture_output=True, text=True,
        cwd=repo_path,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("repolinter produced non-JSON output: %s", result.stdout[:200])
        return {}, {}

    # repolinter returns results as a list of rule result objects
    raw_results = data.get("results", [])
    if isinstance(raw_results, dict):
        # Shouldn't happen but guard anyway
        raw_results = list(raw_results.values())
    if not isinstance(raw_results, list):
        return {}, data

    output = {}
    for rule_result in raw_results:
        info = rule_result.get("ruleInfo", {})
        rule_name = info.get("name", "")
        rule_type = info.get("ruleType", "")
        status = rule_result.get("status", "")

        if not rule_name:
            continue
        if rule_type in SKIP_RULE_TYPES:
            continue
        # IGNORED means the axiom condition wasn't met — treat as not evaluated
        if status == "IGNORED":
            continue

        passed = status == "PASSED"
        level = info.get("level", "warning")
        output[rule_name] = {"passed": passed, "level": level}
    return output, data


def compare_repo(
    org: str, name: str, config: dict, raw_dir: Path | None = None
) -> dict[str, Any]:
    """Clone, run both tools, return comparison dict.

    When raw_dir is given, each tool's full per-rule output is also written
    there as ``<org>__<repo>.{rpa,repolinter}.json`` so this run can serve as
    a baseline for detailed future diffs.
    """
    full_name = f"{org}/{name}"
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp) / name
        if not clone_repo(org, name, repo_path):
            return {"repo": full_name, "error": "clone_failed"}

        rpa_failed = False
        try:
            rpa = run_rpa(str(repo_path), config)
        except Exception as e:
            rpa = {}
            rpa_failed = True
            logger.warning("%s: RPA error: %s", full_name, e)

        rl_failed = False
        rl_raw: dict = {}
        try:
            rl, rl_raw = run_repolinter(str(repo_path))
        except Exception as e:
            rl = {}
            rl_failed = True
            logger.warning("%s: repolinter error: %s", full_name, e)

        if raw_dir is not None:
            stem = f"{org}__{name}"
            try:
                with open(raw_dir / f"{stem}.rpa.json", "w") as f:
                    json.dump({"repo": full_name, "results": rpa}, f, indent=2)
                with open(raw_dir / f"{stem}.repolinter.json", "w") as f:
                    json.dump(rl_raw, f, indent=2)
            except OSError as e:
                logger.warning("%s: could not write raw output: %s", full_name, e)

        # repolinter gates its language/license rules behind external helpers
        # (github-linguist, licensee). If those aren't installed it silently
        # marks every gated rule IGNORED, so those rules end up UNCOMPARED
        # rather than agreeing. Record it so the report can say so.
        axioms = (rl_raw.get("targets") or {}) if rl_raw else {}
        missing_axioms = sorted(
            k for k, v in axioms.items()
            if "not installed" in str((v or {}).get("message", ""))
        )

        discrepancies = []
        all_rules = set(rpa) | set(rl)
        for rule in sorted(all_rules):
            rpa_r = rpa.get(rule)
            rl_r = rl.get(rule)
            if rpa_r is None or rl_r is None:
                continue
            if rpa_r["passed"] != rl_r["passed"]:
                discrepancies.append({
                    "rule": rule,
                    "rpa_passed": rpa_r["passed"],
                    "rl_passed": rl_r["passed"],
                    "rpa_level": rpa_r.get("level", "?"),
                    "rl_level": rl_r.get("level", "?"),
                    "rpa_message": rpa_r.get("message", ""),
                    # Flag cases where RPA raises an ERROR but RL does not fail
                    "rpa_error_not_in_rl": (
                        not rpa_r["passed"]
                        and rpa_r.get("level") == "error"
                        and rl_r["passed"]
                    ),
                })

        return {
            "repo": full_name,
            "discrepancies": discrepancies,
            "rpa_rules": len(rpa),
            "rl_rules": len(rl),
            # Distinguish "tool crashed" from "tool found nothing" — both
            # otherwise present as an empty rule set.
            "rpa_failed": rpa_failed,
            "rl_failed": rl_failed,
            "rpa_results": rpa,
            "rl_results": rl,
            # repolinter axioms that couldn't run (missing linguist/licensee);
            # rules gated on these are UNCOMPARED, not agreed.
            "rl_missing_axioms": missing_axioms,
        }


def _run_provenance() -> dict[str, Any]:
    """Capture what produced this run, so future diffs are interpretable.

    Without the repolinter version in particular, a later comparison cannot
    tell an RPA behaviour change apart from a repolinter one.
    """
    def _cmd(argv: list[str]) -> str:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, cwd=_REPO_ROOT)
            return r.stdout.strip() or "unknown"
        except OSError:
            return "unknown"

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "rpa_git_sha": _cmd(["git", "rev-parse", "HEAD"]),
        "rpa_git_branch": _cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "rpa_git_dirty": bool(_cmd(["git", "status", "--porcelain"]) not in ("", "unknown")),
        "repolinter_version": _cmd(["npx", "repolinter", "--version"]),
        "config_url": CONFIG_URL,
        "skipped_rule_types": sorted(SKIP_RULE_TYPES),
        "skipped_repos": sorted(SKIP_REPOS),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", default="/tmp/rpa_comparison_results.json")
    parser.add_argument("--orgs", nargs="+", metavar="ORG")
    parser.add_argument("--repos", nargs="+", metavar="REPO")
    parser.add_argument(
        "--raw-dir", metavar="DIR",
        help="Directory to write each tool's full per-repo JSON output into, "
             "for detailed comparison against future runs.",
    )
    args = parser.parse_args()

    raw_dir: Path | None = None
    if args.raw_dir:
        raw_dir = Path(args.raw_dir).expanduser()
        raw_dir.mkdir(parents=True, exist_ok=True)

    if args.orgs:
        invalid_orgs = [org for org in args.orgs if org not in ORGS]
        if invalid_orgs:
            print(
                f"ERROR: unknown org(s): {', '.join(invalid_orgs)}",
                file=sys.stderr,
            )
            print(f"Valid orgs: {', '.join(ORGS)}", file=sys.stderr)
            sys.exit(1)
    selected_orgs = args.orgs or ORGS

    print("Fetching repo lists...", flush=True)
    repos: list[tuple[str, str]] = []
    matched_repo_filters: set[str] = set()
    org_counts: dict[str, int] = {}
    for org in selected_orgs:
        try:
            org_repos = get_repos(org)
            if args.repos:
                filtered = []
                for r in org_repos:
                    hits = {
                        f for f in args.repos
                        if f == r["name"] or f == f"{org}/{r['name']}"
                    }
                    if hits:
                        matched_repo_filters |= hits
                        filtered.append(r)
                print(f"  {org}: {len(filtered)}/{len(org_repos)} repos")
                org_repos = filtered
            else:
                suffix = "  (no public repos)" if not org_repos else ""
                print(f"  {org}: {len(org_repos)} repos{suffix}")
            org_counts[org] = len(org_repos)
            for r in org_repos:
                repos.append((org, r["name"]))
        except subprocess.CalledProcessError as e:
            print(f"  {org}: failed to list repos: {e}", file=sys.stderr)

    if args.repos:
        unmatched = set(args.repos) - matched_repo_filters
        if unmatched:
            print(
                f"ERROR: --repos filter(s) matched no repo: {', '.join(sorted(unmatched))}",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"\nScanning {len(repos)} repos with {args.workers} workers...")
    if raw_dir is not None:
        print(f"Raw per-repo output -> {raw_dir}")

    provenance = _run_provenance()
    print(
        f"RPA {provenance['rpa_git_sha'][:8]}"
        f"{' (dirty)' if provenance['rpa_git_dirty'] else ''}"
        f" vs repolinter {provenance['repolinter_version']}"
    )

    # Load config once
    reporter = Reporter()
    with tempfile.TemporaryDirectory() as tmp:
        config = load_config(
            repo_path=tmp,
            config_file=None,
            config_url=CONFIG_URL,
            reporter=reporter,
        )
    if config is None:
        print("ERROR: failed to load config", file=sys.stderr)
        sys.exit(1)

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(compare_repo, org, name, config, raw_dir): (org, name)
            for org, name in repos
        }
        for future in as_completed(futures):
            org, name = futures[future]
            done += 1
            try:
                result = future.result()
            except Exception as e:
                result = {"repo": f"{org}/{name}", "error": str(e)}
            results.append(result)
            disc = len(result.get("discrepancies", []))
            errors = sum(
                1 for d in result.get("discrepancies", [])
                if d.get("rpa_error_not_in_rl")
            )
            flag = " *** RPA ERROR NOT IN RL ***" if errors else ""
            print(
                f"  [{done:3d}/{len(repos)}] {result['repo']}: "
                f"{disc} discrepancies{flag}",
                flush=True,
            )

    # Write results with provenance so this run is usable as a baseline
    payload = {
        "provenance": {
            **provenance,
            "orgs_scanned": selected_orgs,
            "repo_counts_by_org": org_counts,
            "repos_scanned": len(repos),
            "workers": args.workers,
            "raw_dir": str(raw_dir) if raw_dir else None,
        },
        "results": results,
    }
    with open(args.output, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nResults written to {args.output}")

    # --- Run integrity check ---
    # Both tools' failures are caught per-repo, so a broken invocation looks
    # like "no discrepancies" rather than an error. Surface that explicitly
    # before any agreement in this report can be believed.
    scanned = [r for r in results if "error" not in r]
    rl_empty = [r for r in scanned if not r.get("rl_rules")]
    rpa_empty = [r for r in scanned if not r.get("rpa_rules")]
    rl_crashed = [r for r in scanned if r.get("rl_failed")]
    rpa_crashed = [r for r in scanned if r.get("rpa_failed")]

    print("\n" + "=" * 70)
    print("RUN INTEGRITY")
    print("=" * 70)
    print(f"  repolinter produced no rules: {len(rl_empty)}/{len(scanned)}")
    print(f"  RPA produced no rules:        {len(rpa_empty)}/{len(scanned)}")
    print(f"  repolinter raised:            {len(rl_crashed)}")
    print(f"  RPA raised:                   {len(rpa_crashed)}")
    empty_orgs = [o for o, n in org_counts.items() if n == 0]
    if empty_orgs:
        print(f"  orgs with 0 repos:            {', '.join(empty_orgs)}")
    if scanned and len(rl_empty) > len(scanned) * 0.1:
        print(
            "\n  WARNING: repolinter returned nothing for >10% of repos.\n"
            "  Agreement below is NOT trustworthy — check that `npx` is on PATH\n"
            "  and re-run before drawing conclusions.",
        )

    # Rules gated on a repolinter axiom that couldn't run are UNCOMPARED.
    axiom_gaps: Counter = Counter()
    for r in scanned:
        for a in r.get("rl_missing_axioms") or []:
            axiom_gaps[a] += 1
    if axiom_gaps:
        print(
            "\n  WARNING: repolinter axioms unavailable: "
            + ", ".join(f"{a} ({n} repos)" for a, n in sorted(axiom_gaps.items()))
        )
        print(
            "  Rules gated on these were IGNORED by repolinter and are therefore\n"
            "  UNCOMPARED, not in agreement (linguist gates the *-package-metadata\n"
            "  rules; licensee gates license-detectable-by-licensee). Install the\n"
            "  github-linguist and licensee gems to close this gap."
        )

    # --- Summary report ---
    print("\n" + "=" * 70)
    print("COMPARISON REPORT")
    print("=" * 70)

    total_repos = len(results)
    errored = [r for r in results if "error" in r]
    clean = [r for r in results if "error" not in r]
    repos_with_disc = [r for r in clean if r.get("discrepancies")]

    print(f"\nRepos scanned:        {total_repos}")
    print(f"  Scan errors:        {len(errored)}")
    print(f"  Clean (no disc):    {len(clean) - len(repos_with_disc)}")
    print(f"  With discrepancies: {len(repos_with_disc)}")

    # Collect all discrepancies
    all_disc = [
        (r["repo"], d)
        for r in clean
        for d in r.get("discrepancies", [])
    ]
    rpa_errors_not_in_rl = [
        (repo, d) for repo, d in all_disc if d.get("rpa_error_not_in_rl")
    ]

    # --- HIGHLIGHT: RPA errors not in repolinter ---
    if rpa_errors_not_in_rl:
        print(f"\n{'!' * 70}")
        print(f"RPA ERRORS NOT IN REPOLINTER ({len(rpa_errors_not_in_rl)} cases)")
        print(f"{'!' * 70}")
        for repo, d in sorted(rpa_errors_not_in_rl):
            print(f"  {repo}  rule={d['rule']}")
            print(f"    RPA:  FAIL (error)  — {d['rpa_message']}")
            print(f"    RL:   PASS")
    else:
        print("\n✓ No cases where RPA raises an ERROR that repolinter does not.")

    # --- Breakdown by rule ---
    by_rule: dict[str, Counter] = {}
    for repo, d in all_disc:
        rule = d["rule"]
        if rule not in by_rule:
            by_rule[rule] = Counter()
        if d["rpa_passed"] and not d["rl_passed"]:
            by_rule[rule]["rl_fail_rpa_pass"] += 1
        elif not d["rpa_passed"] and d["rl_passed"]:
            by_rule[rule]["rpa_fail_rl_pass"] += 1

    if by_rule:
        print(f"\nDiscrepancies by rule:")
        print(f"  {'Rule':<45} {'RL fail/RPA pass':>16} {'RPA fail/RL pass':>16}")
        print(f"  {'-'*45} {'-'*16} {'-'*16}")
        for rule in sorted(by_rule):
            c = by_rule[rule]
            rpa_flag = " ***" if any(
                d["rule"] == rule and d.get("rpa_error_not_in_rl")
                for _, d in all_disc
            ) else ""
            print(
                f"  {rule:<45} "
                f"{c['rl_fail_rpa_pass']:>16} "
                f"{c['rpa_fail_rl_pass']:>16}"
                f"{rpa_flag}"
            )

    # --- Full list of RPA-fail/RL-pass (non-error) ---
    rpa_warn_not_in_rl = [
        (repo, d)
        for repo, d in all_disc
        if not d["rpa_passed"] and d["rl_passed"] and not d.get("rpa_error_not_in_rl")
    ]
    if rpa_warn_not_in_rl:
        print(f"\nRPA WARNINGS not in repolinter ({len(rpa_warn_not_in_rl)} cases):")
        by_rule_warn: dict[str, list] = {}
        for repo, d in rpa_warn_not_in_rl:
            by_rule_warn.setdefault(d["rule"], []).append((repo, d))
        for rule, cases in sorted(by_rule_warn.items()):
            print(f"  {rule} ({len(cases)} repos):")
            for repo, d in cases[:5]:
                print(f"    {repo}: {d['rpa_message']}")
            if len(cases) > 5:
                print(f"    ... and {len(cases)-5} more")

    # --- Leniency gaps: repolinter fails where RPA passes ---
    rl_fail_rpa_pass = [
        (repo, d) for repo, d in all_disc if d["rpa_passed"] and not d["rl_passed"]
    ]
    if rl_fail_rpa_pass:
        print(
            f"\nrepolinter FAILS that RPA passes "
            f"({len(rl_fail_rpa_pass)} cases) — RPA more lenient:"
        )
        by_rule_lenient: dict[str, list] = {}
        for repo, d in rl_fail_rpa_pass:
            by_rule_lenient.setdefault(d["rule"], []).append(repo)
        for rule, repo_list in sorted(by_rule_lenient.items()):
            print(f"  {rule} ({len(repo_list)} repos): {', '.join(repo_list[:5])}"
                  f"{' ...' if len(repo_list) > 5 else ''}")

    if errored:
        print(f"\nScan errors ({len(errored)}):")
        for r in errored:
            print(f"  {r['repo']}: {r['error']}")


if __name__ == "__main__":
    main()
