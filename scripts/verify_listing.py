#!/usr/bin/env python3
"""Checks the generated listing before it is deployed to GitHub Pages.

Usage: python3 scripts/verify_listing.py Website/index.json

package-list-action lists every zip in every release of the repositories in
source.json. This allows only this repository's own package, and each version
only if its release was made by the build workflow (see verify_release.py) and
its zip matches the listed SHA-256. Every version not in the currently deployed
listing must also be reproducible: its release notes name a source commit, that
commit must be on the source repository's main, and rebuilding it must give the
same zip. It writes a summary of what will be served for the owner to read
before approving the deployment.

A version already deployed with a different SHA-256 is rejected: a published zip
must never change.

Usage: python3 scripts/verify_listing.py <index.json> [--source <full clone of the source repository>]
       python3 scripts/verify_listing.py --audit [--source ...]   checks the deployed listing itself
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_release import problems  # noqa: E402

REPOSITORY = "Rionquosue/rirytools_vpm"
PACKAGES = {"com.riry.rirytools"}
DEPLOYED = "https://rionquosue.github.io/rirytools_vpm/index.json"
SOURCE_REPOSITORY = "Rionquosue/riryunityworkflow"
HERE = os.path.dirname(os.path.abspath(__file__))


def deployed_listing():
    try:
        with urllib.request.urlopen(DEPLOYED, timeout=20) as response:
            return json.load(response)
    except Exception:
        return None


def hashes(listing):
    """(package, version) to zipSHA256 for a listing."""
    return {(n, v): m.get("zipSHA256") for n, p in listing.get("packages", {}).items() for v, m in p.get("versions", {}).items()}


def release(tag):
    result = subprocess.run(["gh", "api", "repos/%s/releases/tags/%s" % (REPOSITORY, tag)], capture_output=True, text=True)
    return json.loads(result.stdout) if result.returncode == 0 else None


def rebuild(source, release_data, zip_name):
    """Problems reproducing a release's zip from the source commit named in its notes."""
    match = re.search(r"%s@([0-9a-f]{7,40})\b" % re.escape(SOURCE_REPOSITORY), release_data.get("body") or "")
    if not match:
        return ["the release notes name no source commit"]

    def git(*args):
        return subprocess.run(["git", "-C", source] + list(args), capture_output=True, text=True)

    resolved = git("rev-parse", "--verify", "--quiet", match.group(1) + "^{commit}")
    if resolved.returncode != 0:
        return ["source commit %s not found" % match.group(1)]
    sha = resolved.stdout.strip()
    if git("merge-base", "--is-ancestor", sha, "origin/main").returncode != 0:
        return ["source commit %s is not on %s main" % (sha[:12], SOURCE_REPOSITORY)]
    work = tempfile.mkdtemp()
    try:
        tree = os.path.join(work, "tree")
        if git("worktree", "add", "--detach", tree, sha).returncode != 0:
            return ["could not check out %s" % sha[:12]]
        dist = os.path.join(work, "dist")
        env = dict(os.environ, GITHUB_OUTPUT=os.devnull)
        subprocess.run([sys.executable, "-I", os.path.join(HERE, "build_vpm.py"), "build", "--source", tree,
                        "--repository", REPOSITORY, "--dist", dist], check=True, capture_output=True, env=env)
        with open(os.path.join(dist, zip_name), "rb") as handle:
            built = hashlib.sha256(handle.read()).hexdigest()
        asset = next((a for a in release_data.get("assets", []) if a["name"] == zip_name), {})
        if asset.get("digest") != "sha256:" + built:
            return ["rebuilding %s gives %s, the release has %s" % (sha[:12], built[:16], asset.get("digest"))]
        return []
    except (subprocess.CalledProcessError, OSError) as error:
        return ["rebuilding %s failed: %s" % (sha[:12], error)]
    finally:
        git("worktree", "remove", "--force", os.path.join(work, "tree"))
        shutil.rmtree(work, ignore_errors=True)


def main():
    arguments = sys.argv[1:]
    source = None
    if "--source" in arguments:
        index = arguments.index("--source")
        source = arguments[index + 1]
        del arguments[index:index + 2]
    published = deployed_listing()
    if arguments == ["--audit"]:
        if published is None:
            print("::error::Could not download %s" % DEPLOYED)
            return 1
        listing, deployed = published, None
    else:
        with open(arguments[0], encoding="utf-8") as handle:
            listing = json.load(handle)
        deployed = hashes(published) if published is not None else None
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
            if deployed is None:
                status = "unknown"
            elif (name, version) not in deployed:
                status = "NEW"
            elif deployed[(name, version)] == sha:
                status = "listed"
            else:
                status = "CHANGED"
                issues.append("the deployed listing has a different SHA-256 for this version; a published zip must never change")
            if data is None:
                issues.append("release v%s not found" % version)
            else:
                issues += problems(data, zip_name, "sha256:" + sha)
                if status != "listed":
                    issues += rebuild(source, data, zip_name) if source else ["no source clone to rebuild from"]
            found += ["%s %s: %s" % (name, version, issue) for issue in issues]
            built_from = re.search(r"@([0-9a-f]{7,40})\b", (data or {}).get("body") or "")
            rows.append("| %s | %s | %s | `%s` | %s | %s | %s |" % (
                name, version, status, sha[:16], (data or {}).get("target_commitish", "?"),
                built_from.group(1)[:12] if built_from else "?", "OK" if not issues else "REJECTED"))
    summary = ["## Listing to deploy", "",
               "| Package | Version | Status | SHA-256 | Release target | Source commit | Check |",
               "|---|---|---|---|---|---|---|"] + rows
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
