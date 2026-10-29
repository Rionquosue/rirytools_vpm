#!/usr/bin/env python3
"""Builds the RiryTools VPM package from a checkout of the source repository.

Steps:
  plan      Read release tags on stdin and print, as GitHub outputs, the previous
            release on the channel and the version to publish.
  build     Zip the source's com.riry.rirytools folder and write the release
            package.json into --dist.

All versions come from this repository's releases or the --version input; nothing
is read from the source. Stable versions count up by patch from the previous
stable release (0.0.1 when there is none). Beta versions are the next stable
version with -beta.N, N counting up from 1.

The zip holds the package folder's contents at its root, as the VRChat Creator
Companion expects, with package.json from this repository's fields file and the
version and url filled in. The .meta files come from the source as they are.
Entries are written in a fixed order with fixed timestamps, so the same source
always gives the same zip and SHA-256.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import zipfile

PACKAGE_NAME = "com.riry.rirytools"
STABLE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
BETA = re.compile(r"(\d+)\.(\d+)\.(\d+)-beta\.(\d+)")
FIELDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "package.json")
LFS_POINTER = b"version https://git-lfs.github.com/spec/"
FIXED_TIME = (2020, 1, 1, 0, 0, 0)



def numbers(match):
    return tuple(int(part) for part in match.groups())


def plan(tags, channel, requested):
    """(previous release on the channel or None, version to publish)."""
    versions = [tag.strip()[1:] for tag in tags if tag.strip().startswith("v")]
    stable = [numbers(m) for m in (STABLE.fullmatch(v) for v in versions) if m]
    beta = [numbers(m) for m in (BETA.fullmatch(v) for v in versions) if m]
    next_stable = (max(stable)[0], max(stable)[1], max(stable)[2] + 1) if stable else (0, 0, 1)
    if channel == "stable":
        previous = "%d.%d.%d" % max(stable) if stable else None
        automatic = "%d.%d.%d" % next_stable
    else:
        previous = "%d.%d.%d-beta.%d" % max(beta) if beta else None
        count = max([b[3] for b in beta if b[:3] == next_stable], default=0)
        automatic = "%d.%d.%d-beta.%d" % (next_stable + (count + 1,))
    return previous, requested or automatic


def package_files(folder):
    """Relative paths of every file that belongs in the package, sorted: no dot entries, and not
    the folder's own package.json, which is replaced."""
    files = []
    for root, directories, names in os.walk(folder):
        relative_root = os.path.relpath(root, folder)
        directories[:] = [d for d in directories if not d.startswith(".")]
        for directory in directories:
            if os.path.islink(os.path.join(root, directory)):
                sys.exit("%s is a symbolic link; the package may not contain links." % os.path.join(relative_root, directory))
        for name in names:
            if name.startswith(".") or (relative_root == "." and name == "package.json"):
                continue
            relative = os.path.normpath(os.path.join(relative_root, name)).replace(os.sep, "/")
            path = os.path.join(folder, relative)
            # A symlink could pull in files from outside the checkout; an LFS pointer means the
            # real file was not fetched.
            if os.path.islink(path):
                sys.exit("%s is a symbolic link; the package may not contain links." % relative)
            with open(path, "rb") as handle:
                if handle.read(len(LFS_POINTER)) == LFS_POINTER:
                    sys.exit("%s is a Git LFS pointer; check out with LFS." % relative)
            files.append(relative)
    return sorted(files)


def manifest(fields_path, version, repository):
    """The release package.json: the fields file with version after name and url at the end."""
    with open(fields_path, encoding="utf-8") as handle:
        fields = json.load(handle)
    if fields.get("name") != PACKAGE_NAME:
        sys.exit("%s must have name %s." % (fields_path, PACKAGE_NAME))
    fields.pop("version", None)
    result = {"name": fields.pop("name"), "version": version}
    result.update(fields)
    result["url"] = "https://github.com/%s/releases/download/v%s/%s-%s.zip" % (repository, version, PACKAGE_NAME, version)
    return result


def write_zip(folder, files, manifest_bytes, path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(files + ["package.json"]):
            info = zipfile.ZipInfo(relative, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            if relative == "package.json":
                data = manifest_bytes
            else:
                with open(os.path.join(folder, relative), "rb") as handle:
                    data = handle.read()
            archive.writestr(info, data)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build(args):
    if not (STABLE.fullmatch(args.version) or BETA.fullmatch(args.version)):
        sys.exit("--version must be X.Y.Z or X.Y.Z-beta.N.")
    folder = os.path.join(args.source, PACKAGE_NAME)
    if not os.path.isdir(folder) or os.path.islink(folder):
        sys.exit("%s has no %s folder." % (args.source, PACKAGE_NAME))
    manifest_bytes = (json.dumps(manifest(args.fields, args.version, args.repository), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tag = "v" + args.version
    zip_name = "%s-%s.zip" % (PACKAGE_NAME, args.version)
    os.makedirs(args.dist, exist_ok=True)
    zip_path = os.path.join(args.dist, zip_name)
    files = package_files(folder)
    write_zip(folder, files, manifest_bytes, zip_path)
    with open(os.path.join(args.dist, "package.json"), "wb") as handle:
        handle.write(manifest_bytes)
    digest = sha256(zip_path)
    print("Built %s (SHA-256 %s)." % (zip_name, digest))
    outputs = os.environ.get("GITHUB_OUTPUT")
    if outputs:
        with open(outputs, "a", encoding="utf-8") as handle:
            handle.write("tag=%s\nzip=%s\nversion=%s\ndigest=sha256:%s\n" % (tag, zip_path, args.version, digest))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["plan", "build"])
    parser.add_argument("--channel", choices=["stable", "beta"], help="Release channel (plan).")
    parser.add_argument("--version", default="", help="Version to publish; empty for automatic (plan), or to build (build).")
    parser.add_argument("--source", help="Checkout of the source repository (build).")
    parser.add_argument("--fields", default=FIELDS, help="The package.json fields file (build).")
    parser.add_argument("--repository", help="owner/name of this repository (build).")
    parser.add_argument("--dist", default="dist", help="Folder for the zip and package.json (build).")
    args = parser.parse_args()
    if args.step == "plan":
        if not args.channel:
            parser.error("--channel is required for plan")
        pattern = STABLE if args.channel == "stable" else BETA
        if args.version and not pattern.fullmatch(args.version):
            parser.error("--version must be %s for the %s channel" % ("X.Y.Z" if args.channel == "stable" else "X.Y.Z-beta.N", args.channel))
        previous, version = plan(sys.stdin, args.channel, args.version)
        print("previous=%s\nversion=%s" % (previous or "", version))
        return
    if not (args.source and args.version and args.repository):
        parser.error("--source, --version and --repository are required for build")
    build(args)


if __name__ == "__main__":
    main()
