"""Offline checks of scripts/verify_listing.py against synthetic Pages artifacts.

Run: SOURCE=<full clone of Rionquosue/riryunityworkflow with origin/main> python3 tests/test_verify_listing.py
Release data for v0.7.0 is recorded below, so no network is needed.
"""
import contextlib
import io
import json
import os
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import verify_listing as vl  # noqa: E402

RELEASE_070 = json.loads(r'''{"assets":[{"digest":"sha256:c024bbf84dc504d985fff967bbaa35e97ada4bf9b263d9656f3025e5eca0a25a","name":"com.riry.rirytools-0.7.0.zip","uploader":{"login":"github-actions[bot]"}},{"digest":"sha256:4dcf25c3a42a66dc72e719a2529a8c84f6807946d5a4b6cde88f5efb53f5894c","name":"package.json","uploader":{"login":"github-actions[bot]"}}],"author":{"login":"github-actions[bot]"},"body":"Built from Rionquosue/riryunityworkflow@c8009c0.","draft":false,"prerelease":false,"tag_name":"v0.7.0","target_commitish":"main"}''')
SOURCE = os.environ.get("SOURCE")
if not SOURCE:
    sys.exit("Set SOURCE to a full clone of Rionquosue/riryunityworkflow.")
vl.release = lambda tag: RELEASE_070 if tag == "v0.7.0" else None
vl.deployed_listing = lambda: None
src = json.load(open(vl.SOURCE_JSON, encoding="utf-8"))
SHA = "c024bbf84dc504d985fff967bbaa35e97ada4bf9b263d9656f3025e5eca0a25a"
URL = "https://github.com/Rionquosue/rirytools_vpm/releases/download/v0.7.0/com.riry.rirytools-0.7.0.zip"
work = tempfile.mkdtemp()

# The rebuilt package.json for 0.7.0, which the listing entry must match.
_, PACKAGE = vl.rebuild(SOURCE, RELEASE_070, "com.riry.rirytools-0.7.0.zip")


def render(text):
    return text.replace("{{ listingInfo.Url }}", src["url"]).replace("{{listingInfo.Url}}", src["url"])


def listing(entry=None, **changes):
    version = dict(PACKAGE, url=URL, zipSHA256=SHA)
    version.update(entry or {})
    data = {"name": src["name"], "id": src["id"], "url": src["url"], "author": src["author"]["name"],
            "packages": {"com.riry.rirytools": {"versions": {"0.7.0": version}}}}
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
]
results = []
for name, expected, files in CASES:
    ok = verify(files) == expected
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name)
print("\n%d/%d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
