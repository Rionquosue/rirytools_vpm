#!/usr/bin/env python3
"""Builds one of this listing's VPM packages from a checkout of its source repository.

Steps:
  plan      Read release tags on stdin and print, as GitHub outputs, the package's
            previous release on the channel, its tag, and the version to publish.
  build     Zip the source's package folder and write the release package.json
            into --dist.

Packages (see PACKAGES):
  com.riry.rirytools          fields package.json, tags vX.Y.Z
  com.riry.animatortemplates  fields animatortemplates.package.json, tags animatortemplates-vX.Y.Z
  com.riry.ftemulator         fields ftemulator.package.json, tags ftemulator-vX.Y.Z

All versions come from this repository's releases or the --version input; nothing
is read from the source. Each package counts only its own tags, so building one
never changes another's version. Stable versions count up by patch from the
package's previous stable release (0.1.0 when there is none); after patch 99 the minor version goes
up and the patch starts again at 0 (0.1.99 to 0.2.0). Beta versions are the next
stable version with -beta.N, N counting up from 1. The beta-unity6 channel (source branch
beta-unity6, for Unity 6) publishes X.Y.Z-beta.N-unity6; it shares N with the beta channel,
so each new beta or beta-unity6 release takes the next N of either, and its package.json
says "unity": "6000.0", so VCC only offers it to Unity 6 projects.

A package in DEPENDENCIES gets vpmDependencies at build time: each listed package at ">=" its
current release on the same channel (stable, beta or beta-unity6, read from the version being
built), from the release tags given with --tags. The build stops when that package has no
release on the channel, so a release never requires a version that does not exist.

The zip holds the package folder's contents at its root, as the VRChat Creator
Companion expects, with package.json from the package's fields file and the
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

HERE = os.path.dirname(os.path.abspath(__file__))
# Package name: (fields file, release tag prefix).
PACKAGES = {
    "com.riry.rirytools": (os.path.join(HERE, "..", "package.json"), "v"),
    "com.riry.animatortemplates": (os.path.join(HERE, "..", "animatortemplates.package.json"), "animatortemplates-v"),
    "com.riry.ftemulator": (os.path.join(HERE, "..", "ftemulator.package.json"), "ftemulator-v"),
}
# Package name: packages it needs, each required at ">=" its current release on the channel being built.
DEPENDENCIES = {
    "com.riry.ftemulator": ["com.riry.rirytools"],
}
# No leading zeros, so each version has one spelling.
NUMBER = r"(0|[1-9][0-9]*)"
STABLE = re.compile(r"%s\.%s\.%s" % (NUMBER, NUMBER, NUMBER))
BETA = re.compile(r"%s\.%s\.%s-beta\.%s" % (NUMBER, NUMBER, NUMBER, NUMBER))
UNITY6_SUFFIX = "-unity6"
UNITY6 = re.compile(r"%s\.%s\.%s-beta\.%s%s" % (NUMBER, NUMBER, NUMBER, NUMBER, re.escape(UNITY6_SUFFIX)))
UNITY6_EDITOR = "6000.0"
# Channel: how its version is written, for errors.
CHANNELS = {"stable": "X.Y.Z", "beta": "X.Y.Z-beta.N", "beta-unity6": "X.Y.Z-beta.N-unity6"}
FIRST = (0, 1, 0)
MAX_PATCH = 99
LFS_POINTER = b"version https://git-lfs.github.com/spec/"
FIXED_TIME = (2020, 1, 1, 0, 0, 0)



def parse(pattern, version):
    """The version's numbers, or None when it does not match or its patch is over MAX_PATCH."""
    match = pattern.fullmatch(version)
    parts = tuple(int(part) for part in match.groups()) if match else None
    return parts if parts and parts[2] <= MAX_PATCH else None


def next_after(version):
    """The stable version after (major, minor, patch): one more patch, or the next minor after MAX_PATCH."""
    major, minor, patch = version[:3]
    return (major, minor, patch + 1) if patch < MAX_PATCH else (major, minor + 1, 0)


def channel_of(version):
    """The channel a version belongs to."""
    for channel in ("stable", "beta", "beta-unity6"):
        if parse(channel_pattern(channel), version):
            return channel
    return None


def current(tags, channel, prefix):
    """The newest release on the channel among tags that start with prefix, or None."""
    pattern = channel_pattern(channel)
    found = []
    for tag in tags:
        tag = tag.strip()
        numbers = parse(pattern, tag[len(prefix):]) if tag.startswith(prefix) else None
        if numbers:
            found.append((numbers, tag[len(prefix):]))
    return max(found)[1] if found else None


def dependencies(package, channel, tags):
    """vpmDependencies for the package on the channel, or None when it needs none. Exits when a
    needed package has no release on the channel."""
    needed = DEPENDENCIES.get(package)
    if not needed:
        return None
    result = {}
    for name in needed:
        version = current(tags, channel, PACKAGES[name][1])
        if not version:
            sys.exit("%s needs %s, which has no %s release yet." % (package, name, channel))
        result[name] = ">=" + version
    return result


def plan(tags, channel, requested, prefix):
    """(previous release on the channel or None, version to publish), counting only tags
    that start with the package's prefix."""
    versions = [tag.strip()[len(prefix):] for tag in tags if tag.strip().startswith(prefix)]
    stable = [n for n in (parse(STABLE, v) for v in versions) if n]
    beta = [n for n in (parse(BETA, v) for v in versions) if n]
    unity6 = [n for n in (parse(UNITY6, v) for v in versions) if n]
    next_stable = next_after(max(stable)) if stable else FIRST
    if channel == "stable":
        previous = "%d.%d.%d" % max(stable) if stable else None
        automatic = "%d.%d.%d" % next_stable
    else:
        own = unity6 if channel == "beta-unity6" else beta
        suffix = UNITY6_SUFFIX if channel == "beta-unity6" else ""
        previous = "%d.%d.%d-beta.%d" % max(own) + suffix if own else None
        # N is shared by beta and beta-unity6.
        count = max([b[3] for b in beta + unity6 if b[:3] == next_stable], default=0)
        automatic = "%d.%d.%d-beta.%d" % (next_stable + (count + 1,)) + suffix
    return previous, requested or automatic


def channel_pattern(channel):
    return {"stable": STABLE, "beta": BETA, "beta-unity6": UNITY6}[channel]


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


def manifest(package, fields_path, version, repository, tag, needs=None):
    """The release package.json: the fields file with version after name, vpmDependencies (when the
    package needs others) and url at the end."""
    with open(fields_path, encoding="utf-8") as handle:
        fields = json.load(handle)
    if fields.get("name") != package:
        sys.exit("%s must have name %s." % (fields_path, package))
    if "vpmDependencies" in fields:
        sys.exit("%s must not have vpmDependencies; they are set at build time (DEPENDENCIES)." % fields_path)
    fields.pop("version", None)
    result = {"name": fields.pop("name"), "version": version}
    result.update(fields)
    if version.endswith(UNITY6_SUFFIX):
        result["unity"] = UNITY6_EDITOR
    if needs:
        result["vpmDependencies"] = needs
    result["url"] = "https://github.com/%s/releases/download/%s/%s-%s.zip" % (repository, tag, package, version)
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
    if not (parse(STABLE, args.version) or parse(BETA, args.version) or parse(UNITY6, args.version)):
        sys.exit("--version must be X.Y.Z, X.Y.Z-beta.N or X.Y.Z-beta.N-unity6, with Z at most %d." % MAX_PATCH)
    fields, prefix = PACKAGES[args.package]
    folder = os.path.join(args.source, args.package)
    if not os.path.isdir(folder) or os.path.islink(folder):
        sys.exit("%s has no %s folder." % (args.source, args.package))
    tag = prefix + args.version
    needs = None
    if args.package in DEPENDENCIES:
        if not args.tags:
            sys.exit("--tags is required for %s, which needs %s." % (args.package, ", ".join(DEPENDENCIES[args.package])))
        with open(args.tags, encoding="utf-8") as handle:
            needs = dependencies(args.package, channel_of(args.version), handle.readlines())
        for name, requirement in needs.items():
            print("%s requires %s %s." % (args.package, name, requirement))
    manifest_bytes = (json.dumps(manifest(args.package, args.fields or fields, args.version, args.repository, tag, needs), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    zip_name = "%s-%s.zip" % (args.package, args.version)
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
    parser.add_argument("--package", required=True, choices=sorted(PACKAGES), help="Package to plan or build.")
    parser.add_argument("--channel", choices=sorted(CHANNELS), help="Release channel (plan).")
    parser.add_argument("--version", default="", help="Version to publish; empty for automatic (plan), or to build (build).")
    parser.add_argument("--source", help="Checkout of the source repository (build).")
    parser.add_argument("--fields", help="The package.json fields file (build); default the package's own.")
    parser.add_argument("--repository", help="owner/name of this repository (build).")
    parser.add_argument("--tags", help="File of this repository's release tags, one per line (build); needed for a package in DEPENDENCIES.")
    parser.add_argument("--dist", default="dist", help="Folder for the zip and package.json (build).")
    args = parser.parse_args()
    if args.step == "plan":
        if not args.channel:
            parser.error("--channel is required for plan")
        pattern = channel_pattern(args.channel)
        if args.version and not parse(pattern, args.version):
            parser.error("--version must be %s for the %s channel, with Z at most %d" % (CHANNELS[args.channel], args.channel, MAX_PATCH))
        prefix = PACKAGES[args.package][1]
        previous, version = plan(sys.stdin, args.channel, args.version, prefix)
        print("previous=%s\nprevious_tag=%s\nversion=%s" % (previous or "", prefix + previous if previous else "", version))
        return
    if not (args.source and args.version and args.repository):
        parser.error("--source, --version and --repository are required for build")
    build(args)


if __name__ == "__main__":
    main()
