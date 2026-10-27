#!/usr/bin/env python3
"""Checks the generated listing before it is deployed to GitHub Pages.

Usage: python3 scripts/verify_listing.py Website/index.json

package-list-action lists every zip in every release of the repositories in
source.json. This allows only this repository's own package, and each version
only if its release was made by the build workflow (see verify_release.py) and
its zip matches the listed SHA-256. It writes a summary of what will be served,
marking versions that are not in the currently deployed listing, for the owner
to read before approving the deployment.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_release import problems  # noqa: E402

REPOSITORY = "Rionquosue/rirytools_vpm"
PACKAGES = {"com.riry.rirytools"}
DEPLOYED = "https://rionquosue.github.io/rirytools_vpm/index.json"


def deployed_versions():
    try:
        with urllib.request.urlopen(DEPLOYED, timeout=20) as response:
            listing = json.load(response)
        return {(n, v) for n, p in listing.get("packages", {}).items() for v in p.get("versions", {})}
    except Exception:
        return None


def release(tag):
    result = subprocess.run(["gh", "api", "repos/%s/releases/tags/%s" % (REPOSITORY, tag)], capture_output=True, text=True)
    return json.loads(result.stdout) if result.returncode == 0 else None


def main():
    with open(sys.argv[1], encoding="utf-8") as handle:
        listing = json.load(handle)
    deployed = deployed_versions()
    found = []
    rows = []
    for name, package in sorted(listing.get("packages", {}).items()):
        if name not in PACKAGES:
            found.append("%s is not one of this listing's packages" % name)
            continue
        for version, manifest in sorted(package.get("versions", {}).items()):
            zip_name = "%s-%s.zip" % (name, version)
            expected = "https://github.com/%s/releases/download/v%s/%s" % (REPOSITORY, version, zip_name)
            sha = manifest.get("zipSHA256") or ""
            issues = []
            if manifest.get("url") != expected:
                issues.append("url is %s, expected %s" % (manifest.get("url"), expected))
            if manifest.get("name") != name or manifest.get("version") != version:
                issues.append("the manifest inside the zip names %s %s" % (manifest.get("name"), manifest.get("version")))
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                issues.append("zipSHA256 is missing or malformed")
            data = release("v" + version)
            if data is None:
                issues.append("release v%s not found" % version)
            else:
                issues += problems(data, zip_name, "sha256:" + sha)
            found += ["%s %s: %s" % (name, version, issue) for issue in issues]
            status = "unknown" if deployed is None else "listed" if (name, version) in deployed else "NEW"
            rows.append("| %s | %s | %s | `%s` | %s |" % (name, version, status, sha[:16], "OK" if not issues else "REJECTED"))
    summary = ["## Listing to deploy", "", "| Package | Version | Status | SHA-256 | Check |", "|---|---|---|---|---|"] + rows
    if found:
        summary += ["", "Rejected:"] + ["- " + f for f in found]
    print("\n".join(summary))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write("\n".join(summary) + "\n")
    for f in found:
        print("::error::" + f)
    return 1 if found or not rows else 0


if __name__ == "__main__":
    sys.exit(main())
