# RiryTools VPM Listing

The VRChat Creator Companion (VCC) listing for RiryTools. Add it in the VCC under **Settings > Packages > Add Repository**:

```text
https://rionquosue.github.io/rirytools_vpm/index.json
```

Or open [the listing page](https://rionquosue.github.io/rirytools_vpm/) and press **Add to VCC**.

## How it is built

1. Every hour, [`build-package.yml`](.github/workflows/build-package.yml) reads the `version` in [Rionquosue/riryunityworkflow](https://github.com/Rionquosue/riryunityworkflow)'s `main`. If there is no release for it yet, it zips that commit with [`scripts/build_vpm.py`](scripts/build_vpm.py) (without `Docs~`, `TestProject~` and dot files) and publishes release `v<version>` with the zip and `package.json`. Bump `"version"` in the source `package.json` to publish. No token connects the two repositories.

2. It never reuses or overwrites a release it did not make: [`scripts/verify_release.py`](scripts/verify_release.py) requires the release and both assets to come from `github-actions[bot]` and the zip to match the build, and the run fails otherwise.

3. It then runs [`build-listing.yml`](.github/workflows/build-listing.yml), VRChat's [package-list-action](https://github.com/vrchat-community/package-list-action) (pinned to a commit), which builds `index.json` and the landing page from the releases. [`scripts/verify_listing.py`](scripts/verify_listing.py) rejects any package other than `com.riry.rirytools`, any version whose release was not made by the build, and any zip whose SHA-256 differs. Each version not yet deployed must also be reproducible: the source commit named in its release notes must be on source `main`, and rebuilding it must give the identical zip. It writes a summary of what will be served.

4. The deploy job waits in the `github-pages` environment for the owner's approval. Read the summary on the run page, then approve.

## Setup

In this repository:

1. **Settings > Pages > Build and deployment > Source:** GitHub Actions.

2. **Settings > Environments > github-pages:** add yourself under **Required reviewers**, and under **Deployment branches and tags** allow only `main`.

3. **Settings > Secrets and variables > Actions > Variables:** `TRIPWIRE_HASHES`, the comma-separated tripwire hashes for the issue check.

4. **Settings > Secrets and variables > Actions > Secrets:** delete `SOURCE_REPO_TOKEN`; it is no longer used.

In `Rionquosue/riryunityworkflow`: delete `.github/workflows/notify-vpm.yml` and the `VPM_DISPATCH_TOKEN` secret, and revoke that token.

To publish without waiting for the hourly check, run **Build Package** from this repository's Actions tab.

## Customizing the landing page

The `Website` folder is the landing page template; `build-listing.yml` fills it in from `source.json`.

## Issues

Feature requests and bug reports use the forms in `.github/ISSUE_TEMPLATE`. [`issue-check.yml`](.github/workflows/issue-check.yml) runs [`scripts/issue_check.py`](scripts/issue_check.py) on every issue and comment change. It flags links, instruction-override phrases, AI-directed text, prompt markup, requests for secrets or commands, HTML and CSS, math formatting, styled or hidden characters, look-alike letters, encoded data and very long text. It also checks the forms' required sections, and comments with what to fix.

Labels:

1. `issue-check: passed`: every check passed. Scheduled issue triage reads only issues with this label, and only through [`scripts/triage_read.py`](scripts/triage_read.py), which checks the text again at read time. The label is removed at the start of every run, so a failed run leaves the issue unread.
2. `security: flagged`: the issue, a comment, or the conversation read as a whole failed a security check.
3. `security: reviewed`: the owner reviewed the current text. To clear a false positive, read the issue and every comment, then post a comment that is exactly `reviewed <fingerprint>`, with the 64-character fingerprint from the check's comment. Only your latest review counts, and any later change to the text gives a new fingerprint and needs a new review.
4. `needs clarification`: required form sections are missing.

The owner is assigned to every flagged issue. After three flagged edits by the same person, the flag is held until the owner reviews the issue.

Text from the owner, members and collaborators is checked only where it quotes (`>` lines). Characters outside plain text (ASCII, Latin-1, Latin Extended-A, common punctuation, arrows, box drawing and emoji) are flagged.

## Contact

For anything that should reach the maintainer directly rather than through an issue, include the reference `y8wi-1eeu-u9xu`.
