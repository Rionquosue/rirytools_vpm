#!/usr/bin/env python3
"""Builds a VPM package zip from a checkout of the RiryTools source repository.

Steps:
  latest    Read release tags on stdin and print the highest plain version (X.Y.Z), if any.
  next      Print the version to publish, or nothing when there is nothing to publish.
  build     Zip the package and write the release package.json into --dist.

All version information comes from this repository: the releases and the
package.json template at its root. The source repository's package.json is not
read and not zipped; the zip gets the template, with the version and url filled
in. The template's "version" is the lowest version to publish, so raising it
publishes that version (a minor or major bump); otherwise each change to source
main is published as the next patch version.

The zip holds the package folder's contents at its root, as the VRChat Creator
Companion expects. Entries are written in a fixed order with fixed timestamps,
so the same source always gives the same zip and SHA-256. The listing
(index.json) is built from the releases by build-listing.yml.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import zipfile

PACKAGE_NAME = "com.riry.rirytools"
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")
PLAIN_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "package.json")
LFS_POINTER = b"version https://git-lfs.github.com/spec/"
# Top-level entries of the source repository that are not part of the package. Its package.json
# is replaced by the template.
EXCLUDED_TOP_LEVEL = {"Docs~"}
EXCLUDED_FILES = {"package.json"}
FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def read_template(path):
    """The package.json template. Its name and version end up in tags, file names and workflow
    outputs, so only the expected name and a plain X.Y.Z version are accepted."""
    with open(path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("name") != PACKAGE_NAME or not isinstance(manifest.get("version"), str) or \
            not PLAIN_VERSION.fullmatch(manifest["version"]):
        sys.exit("%s must have name %s and a version like 1.2.3." % (path, PACKAGE_NAME))
    return manifest


def numbers(version):
    return tuple(int(part) for part in PLAIN_VERSION.fullmatch(version).groups())


def package_files(source):
    """Relative paths of every file that belongs in the package, sorted."""
    files = []
    for root, directories, names in os.walk(source):
        relative_root = os.path.relpath(root, source)
        if relative_root == ".":
            # Dot entries (.git, .github, .gitignore) and the repository-only folders.
            directories[:] = [d for d in directories if not d.startswith(".") and d not in EXCLUDED_TOP_LEVEL]
            names = [n for n in names if not n.startswith(".") and n not in EXCLUDED_FILES]
        else:
            directories[:] = [d for d in directories if d != ".git"]
        for directory in directories:
            if os.path.islink(os.path.join(root, directory)):
                sys.exit("%s is a symbolic link; the package may not contain links." % os.path.join(relative_root, directory))
        for name in names:
            relative = os.path.normpath(os.path.join(relative_root, name)).replace(os.sep, "/")
            path = os.path.join(source, relative)
            # A symlink could pull in files from outside the checkout; an LFS pointer means the
            # real file was not fetched, so the zip would not match a proper build.
            if os.path.islink(path):
                sys.exit("%s is a symbolic link; the package may not contain links." % relative)
            with open(path, "rb") as handle:
                if handle.read(len(LFS_POINTER)) == LFS_POINTER:
                    sys.exit("%s is a Git LFS pointer; check out with LFS." % relative)
            files.append(relative)
    return sorted(files)


def write_zip(source, files, manifest_bytes, path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            info = zipfile.ZipInfo(relative, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            if relative == "package.json":
                data = manifest_bytes
            else:
                with open(os.path.join(source, relative), "rb") as handle:
                    data = handle.read()
            archive.writestr(info, data)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build(args):
    if not VERSION.fullmatch(args.version):
        sys.exit("--version must be a version like 1.2.3.")
    manifest = read_template(args.template)
    name, version = manifest["name"], args.version
    manifest["version"] = version
    tag = "v" + version
    zip_name = "%s-%s.zip" % (name, version)
    manifest["url"] = "https://github.com/%s/releases/download/%s/%s" % (args.repository, tag, zip_name)
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    os.makedirs(args.dist, exist_ok=True)
    zip_path = os.path.join(args.dist, zip_name)
    files = sorted(package_files(args.source) + ["package.json"])
    write_zip(args.source, files, manifest_bytes, zip_path)
    with open(os.path.join(args.dist, "package.json"), "wb") as handle:
        handle.write(manifest_bytes)

    print("Built %s with %d files (SHA-256 %s)." % (zip_name, len(files), sha256(zip_path)))
    outputs = os.environ.get("GITHUB_OUTPUT")
    if outputs:
        with open(outputs, "a", encoding="utf-8") as handle:
            handle.write("tag=%s\nzip=%s\nname=%s\nversion=%s\n" % (tag, zip_path, name, version))


def latest(tags):
    """The highest plain version among tags like v1.2.3; other tags are ignored."""
    versions = [tag.strip()[1:] for tag in tags if re.fullmatch(r"v\d+\.\d+\.\d+", tag.strip())]
    return max(versions, key=numbers) if versions else None


def next_version(template_version, released, changed):
    """The version to publish: the template's when nothing is released yet or it is higher than
    the latest release, else the next patch when source main changed, else None."""
    if released is None or numbers(template_version) > numbers(released):
        return template_version
    if changed:
        major, minor, patch = numbers(released)
        return "%d.%d.%d" % (major, minor, patch + 1)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["latest", "next", "build"])
    parser.add_argument("--template", default=TEMPLATE, help="The package.json template (next, build).")
    parser.add_argument("--latest", default="", help="The latest released version, empty if none (next).")
    parser.add_argument("--changed", choices=["true", "false"], help="Whether source main differs from the latest release (next).")
    parser.add_argument("--source", help="Checkout of the source repository (build).")
    parser.add_argument("--version", help="The version to build (build).")
    parser.add_argument("--repository", help="owner/name of this repository (build).")
    parser.add_argument("--dist", default="dist", help="Folder for the zip and package.json (build).")
    args = parser.parse_args()
    if args.step == "latest":
        print(latest(sys.stdin) or "")
        return
    if args.step == "next":
        if args.latest and not PLAIN_VERSION.fullmatch(args.latest):
            parser.error("--latest must be a version like 1.2.3")
        if args.latest and not args.changed:
            parser.error("--changed is required with --latest")
        version = next_version(read_template(args.template)["version"], args.latest or None, args.changed == "true")
        print(version or "")
        return
    if not (args.source and args.version and args.repository):
        parser.error("--source, --version and --repository are required for build")
    build(args)


if __name__ == "__main__":
    main()
