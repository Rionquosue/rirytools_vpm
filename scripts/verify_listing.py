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
must never change. Each version's listing entry must match the rebuilt
package.json (so no extra legacyFolders, dependencies or links), and the
listing's own fields must match source.json.

With --artifact, the whole Pages artifact is checked: a plain tar of regular
files with no duplicates, exactly the files in Website/ plus index.json, static
files byte-identical to Website/, and the rendered pages loading only the
template's scripts and linking only to known addresses.

Usage: python3 scripts/verify_listing.py --artifact <artifact.tar> --source <full clone of the source repository>
       python3 scripts/verify_listing.py <index.json> --source ...
       python3 scripts/verify_listing.py --audit --source ...   checks the deployed listing itself
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_release import problems  # noqa: E402

REPOSITORY = "Rionquosue/rirytools_vpm"
PACKAGES = {"com.riry.rirytools"}
DEPLOYED = "https://rionquosue.github.io/rirytools_vpm/index.json"
SOURCE_REPOSITORY = "Rionquosue/riryunityworkflow"
HERE = os.path.dirname(os.path.abspath(__file__))
WEBSITE = os.path.join(HERE, "..", "Website")
SOURCE_JSON = os.path.join(HERE, "..", "source.json")
TEMPLATED = {"index.html", "app.js"}
ADDRESS = re.compile(r"""(?:https?|vcc)://[^\s"'<>)\]}`]+""")
SCRIPT_TAG = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
IMPORT = re.compile(r"""\bimport\b[^;'"]*['"][^'"]+['"]""")


def deployed_listing():
    """The deployed listing, None if there is none yet (404); any other failure stops the check,
    since without it a changed zip could not be detected."""
    try:
        with urllib.request.urlopen(DEPLOYED, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        sys.exit("::error::Could not download %s: %s" % (DEPLOYED, error))
    except (OSError, ValueError) as error:
        sys.exit("::error::Could not download %s: %s" % (DEPLOYED, error))


def hashes(listing):
    """(package, version) to zipSHA256 for a listing."""
    return {(n, v): m.get("zipSHA256") for n, p in listing.get("packages", {}).items() for v, m in p.get("versions", {}).items()}


def release(tag):
    result = subprocess.run(["gh", "api", "repos/%s/releases/tags/%s" % (REPOSITORY, tag)], capture_output=True, text=True)
    return json.loads(result.stdout) if result.returncode == 0 else None


def empty(value):
    return value in (None, "", [], {})


def entry_problems(entry, manifest):
    """Differences between a listing entry and the rebuilt package.json, ignoring url, zipSHA256
    and empty defaults the listing tool may add."""
    found = []
    for key in sorted(set(entry) | set(manifest)):
        if key in ("url", "zipSHA256"):
            continue
        if empty(entry.get(key)) and empty(manifest.get(key)):
            continue
        if entry.get(key) != manifest.get(key):
            found.append("listing field %s differs from the package.json in the zip" % key)
    return found


def rebuild(source, release_data, zip_name):
    """Problems reproducing a release's zip from the source commit named in its notes, and the
    rebuilt package.json (None when it could not be rebuilt)."""
    match = re.search(r"%s@([0-9a-f]{7,40})\b" % re.escape(SOURCE_REPOSITORY), release_data.get("body") or "")
    if not match:
        return ["the release notes name no source commit"], None

    def git(*args):
        return subprocess.run(["git", "-C", source] + list(args), capture_output=True, text=True)

    resolved = git("rev-parse", "--verify", "--quiet", match.group(1) + "^{commit}")
    if resolved.returncode != 0:
        return ["source commit %s not found" % match.group(1)], None
    sha = resolved.stdout.strip()
    if git("merge-base", "--is-ancestor", sha, "origin/main").returncode != 0:
        return ["source commit %s is not on %s main" % (sha[:12], SOURCE_REPOSITORY)], None
    work = tempfile.mkdtemp()
    try:
        tree = os.path.join(work, "tree")
        if git("worktree", "add", "--detach", tree, sha).returncode != 0:
            return ["could not check out %s" % sha[:12]], None
        dist = os.path.join(work, "dist")
        env = dict(os.environ, GITHUB_OUTPUT=os.devnull)
        subprocess.run([sys.executable, "-I", os.path.join(HERE, "build_vpm.py"), "build", "--source", tree,
                        "--repository", REPOSITORY, "--dist", dist], check=True, capture_output=True, env=env)
        with open(os.path.join(dist, zip_name), "rb") as handle:
            built = hashlib.sha256(handle.read()).hexdigest()
        with open(os.path.join(dist, "package.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        asset = next((a for a in release_data.get("assets", []) if a["name"] == zip_name), {})
        if asset.get("digest") != "sha256:" + built:
            return ["rebuilding %s gives %s, the release has %s" % (sha[:12], built[:16], asset.get("digest"))], manifest
        return [], manifest
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        return ["rebuilding %s failed: %s" % (sha[:12], error)], None
    finally:
        git("worktree", "remove", "--force", os.path.join(work, "tree"))
        shutil.rmtree(work, ignore_errors=True)


def read_artifact(path):
    """The Pages artifact's files as {name: bytes}, or a list of problems."""
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


def site_problems(files, listing, source_info, allowed_extra):
    """Problems with the rendered site against the Website/ template."""
    found = []
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


def main():
    arguments = sys.argv[1:]
    if not arguments:
        sys.exit(__doc__)
    source = artifact = None
    for flag in ("--source", "--artifact"):
        if flag in arguments:
            index = arguments.index(flag)
            if flag == "--source":
                source = arguments[index + 1]
            else:
                artifact = arguments[index + 1]
            del arguments[index:index + 2]
    with open(SOURCE_JSON, encoding="utf-8") as handle:
        source_info = json.load(handle)
    published = deployed_listing()
    found = []
    files = None
    if arguments == ["--audit"]:
        if published is None:
            print("::error::Could not download %s" % DEPLOYED)
            return 1
        listing, deployed = published, None
    elif artifact:
        files, found = read_artifact(artifact)
        if "index.json" not in files:
            print("::error::The artifact has no index.json")
            return 1
        listing = json.loads(files["index.json"].decode("utf-8"))
        deployed = hashes(published) if published is not None else None
    else:
        with open(arguments[0], encoding="utf-8") as handle:
            listing = json.load(handle)
        deployed = hashes(published) if published is not None else None
    rows = []
    version_urls = set()
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
            version_urls.add(expected)
            if data is None:
                issues.append("release v%s not found" % version)
            else:
                issues += problems(data, zip_name, "sha256:" + sha)
                # Every version is rebuilt: cheap, and it ties each entry's fields to reviewed source.
                if source:
                    rebuilt, rebuilt_manifest = rebuild(source, data, zip_name)
                    issues += rebuilt
                    if rebuilt_manifest is not None:
                        issues += entry_problems(manifest, rebuilt_manifest)
                        version_urls |= {v for v in rebuilt_manifest.values() if isinstance(v, str) and "://" in v}
                else:
                    issues.append("no source clone to rebuild from")
            found += ["%s %s: %s" % (name, version, issue) for issue in issues]
            built_from = re.search(r"@([0-9a-f]{7,40})\b", (data or {}).get("body") or "")
            rows.append("| %s | %s | %s | `%s` | %s | %s | %s |" % (
                name, version, status, sha[:16], (data or {}).get("target_commitish", "?"),
                built_from.group(1)[:12] if built_from else "?", "OK" if not issues else "REJECTED"))
    if files is not None:
        found += site_problems(files, listing, source_info, version_urls)
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
