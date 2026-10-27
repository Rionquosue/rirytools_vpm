#!/usr/bin/env python3
"""Checks a release (GitHub API JSON on stdin) against a locally built zip.

The release must have been created by github-actions[bot] and hold exactly the
zip and package.json, both uploaded by github-actions[bot], with the zip's
SHA-256 equal to the local build. Exits non-zero otherwise, so a release made
or changed by anyone else is never reused.
"""
import argparse
import hashlib
import json
import os
import sys

BUILDER = "github-actions[bot]"


def problems(release, zip_name, digest):
    """What is wrong with a release that should hold zip_name with this "sha256:..." digest."""
    found = []
    if (release.get("author") or {}).get("login") != BUILDER:
        found.append("the release was not created by %s" % BUILDER)
    if release.get("draft") or release.get("prerelease"):
        found.append("the release is a draft or prerelease")
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    if set(assets) != {zip_name, "package.json"}:
        found.append("the assets are %s, expected %s and package.json" % (sorted(assets), zip_name))
    for asset in assets.values():
        if (asset.get("uploader") or {}).get("login") != BUILDER:
            found.append("%s was not uploaded by %s" % (asset["name"], BUILDER))
    if zip_name in assets and assets[zip_name].get("digest") != digest:
        found.append("%s has digest %s, expected %s" % (zip_name, assets[zip_name].get("digest"), digest))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True)
    args = parser.parse_args()
    release = json.load(sys.stdin)
    with open(args.zip, "rb") as handle:
        digest = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
    found = problems(release, os.path.basename(args.zip), digest)
    for problem in found:
        print("::error::%s: %s" % (release.get("tag_name"), problem))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
