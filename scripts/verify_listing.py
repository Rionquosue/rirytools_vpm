#!/usr/bin/env python3
"""Checks the Pages artifact built by build-listing.yml before it is deployed.

Usage: python3 scripts/verify_listing.py --artifact <artifact.tar>

The artifact must be a plain tar of regular files with no duplicates, holding
exactly the files in Website/ plus index.json. Static files must be
byte-identical to Website/, and the rendered pages must load only the
template's scripts and link only to known addresses. The listing's name, id,
url and author must match source.json, its only package must be
com.riry.rirytools, and each version's url must point at this repository's
release for that version. It writes a summary of what will be served to the
run page.
"""
import json
import os
import re
import sys
import tarfile

REPOSITORY = "Rionquosue/rirytools_vpm"
PACKAGES = {"com.riry.rirytools"}
HERE = os.path.dirname(os.path.abspath(__file__))
WEBSITE = os.path.join(HERE, "..", "Website")
SOURCE_JSON = os.path.join(HERE, "..", "source.json")
TEMPLATED = {"index.html", "app.js"}
ADDRESS = re.compile(r"""(?:https?|vcc)://[^\s"'<>)\]}`]+""")
SCRIPT_TAG = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
# An import statement where one can start (line start, or after ; { } or a tag's >), or a dynamic
# import(), so the word "import" in rendered package text ("import presets") is not counted.
IMPORT = re.compile(r"""(?m)(?:^|[;{}>])\s*import\b[^;'"]*['"][^'"]+['"]|\bimport\s*\(\s*['"][^'"]+['"]""")


def read_artifact(path):
    """The Pages artifact's files as {name: bytes}, and a list of problems."""
    files, found = {}, []
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            name = re.sub(r"^(\./)+", "", member.name)
            if member.isdir() and name in ("", "."):
                continue
            if not member.isfile():
                found.append("artifact entry %s is not a regular file" % member.name)
                continue
            if name in files or "/" in name:
                found.append("artifact entry %s is duplicated or in a folder" % member.name)
                continue
            files[name] = archive.extractfile(member).read()
    return files, found


def addresses(text):
    return {a.rstrip(".,;") for a in ADDRESS.findall(text)}


def site_problems(files, listing, source_info, allowed_extra, template=None):
    """Problems with the rendered site against the Website/ template ({name: bytes})."""
    found = []
    if template is None:
        template = {name: open(os.path.join(WEBSITE, name), "rb").read() for name in os.listdir(WEBSITE)
                    if os.path.isfile(os.path.join(WEBSITE, name))}
    expected = set(template) | {"index.json"}
    if set(files) != expected:
        found.append("the artifact holds %s, expected %s" % (sorted(files), sorted(expected)))
    allowed = {source_info.get("url"), (source_info.get("infoLink") or {}).get("url"),
               (source_info.get("author") or {}).get("url")} | allowed_extra
    for name, data in template.items():
        if name not in files:
            continue
        if name not in TEMPLATED:
            if files[name] != data:
                found.append("%s differs from Website/%s" % (name, name))
            continue
        rendered, original = files[name].decode("utf-8", "replace"), data.decode("utf-8", "replace")
        allowed |= {a for a in addresses(original) if "{{" not in a}
        if sorted(SCRIPT_TAG.findall(rendered)) != sorted(SCRIPT_TAG.findall(original)):
            found.append("%s loads different scripts from the template" % name)
        if sorted(IMPORT.findall(rendered)) != sorted(IMPORT.findall(original)):
            found.append("%s imports different modules from the template" % name)
        unknown = sorted(a for a in addresses(rendered) if a not in allowed and not a.startswith("vcc://vpm/addRepo"))
        if unknown:
            found.append("%s links to unexpected addresses: %s" % (name, ", ".join(unknown[:5])))
        if "vcc://" in rendered and source_info.get("url") and source_info["url"] not in rendered:
            found.append("%s does not offer this listing's URL to the VCC" % name)
    for key in ("name", "id", "url"):
        if listing.get(key) != source_info.get(key):
            found.append("listing %s is %r, source.json has %r" % (key, listing.get(key), source_info.get(key)))
    if listing.get("author") not in ((source_info.get("author") or {}).get("name"), source_info.get("author")):
        found.append("listing author differs from source.json")
    return found


def listing_problems(listing):
    """Problems with the listing's packages, the summary rows, and the release URLs."""
    found, rows, urls = [], [], set()
    for name, package in sorted(listing.get("packages", {}).items()):
        if name not in PACKAGES:
            found.append("%s is not one of this listing's packages" % name)
            continue
        for version, entry in sorted(package.get("versions", {}).items()):
            expected = "https://github.com/%s/releases/download/v%s/%s-%s.zip" % (REPOSITORY, version, name, version)
            urls.add(expected)
            ok = entry.get("url") == expected
            if not ok:
                found.append("%s %s: url is %s, expected %s" % (name, version, entry.get("url"), expected))
            rows.append("| %s | %s | `%s` | %s |" % (name, version, (entry.get("zipSHA256") or "")[:16], "OK" if ok else "REJECTED"))
    return found, rows, urls


def main():
    arguments = sys.argv[1:]
    if len(arguments) != 2 or arguments[0] != "--artifact":
        sys.exit(__doc__)
    with open(SOURCE_JSON, encoding="utf-8") as handle:
        source_info = json.load(handle)
    files, found = read_artifact(arguments[1])
    if "index.json" not in files:
        print("::error::The artifact has no index.json")
        return 1
    listing = json.loads(files["index.json"].decode("utf-8"))
    problems, rows, urls = listing_problems(listing)
    found += problems + site_problems(files, listing, source_info, urls)
    summary = ["## Listing to deploy", "", "| Package | Version | SHA-256 | Check |", "|---|---|---|---|"] + rows
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
