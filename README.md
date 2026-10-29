# RiryTools VPM Listing

The VRChat Creator Companion (VCC) listing for RiryTools. Add it in the VCC under **Settings > Packages > Add Repository**:

```text
https://rionquosue.github.io/rirytools_vpm/index.json
```

Or open [the listing page](https://rionquosue.github.io/rirytools_vpm/) and press **Add to VCC**.

## How it is built

1. Every hour, [`build-package.yml`](.github/workflows/build-package.yml) builds [Rionquosue/riryunityworkflow](https://github.com/Rionquosue/riryunityworkflow)'s `main` with [`scripts/build_vpm.py`](scripts/build_vpm.py) (without `Docs~` and dot files) using the latest release's version, and compares the zip with that release. If it differs, it publishes release `v<next version>` with the zip and `package.json`. No token connects the two repositories.

   All version information is in this repository. The source repository has no `package.json`: the zip gets [`package.json`](package.json) from here, with `version` and `url` filled in. Each change to source `main` is published as the next patch version. To publish a minor or major version, raise `"version"` in this repository's `package.json` above the latest release; the next run publishes that version. The release notes name the source commit and this repository's commit, whose `package.json` was used.

2. It never reuses or overwrites a release it did not make: [`scripts/verify_release.py`](scripts/verify_release.py) requires the release and both assets to come from `github-actions[bot]` and the zip to match the build, and the run fails otherwise.

3. It then runs [`build-listing.yml`](.github/workflows/build-listing.yml), VRChat's [package-list-action](https://github.com/vrchat-community/package-list-action) (pinned to a commit), which builds `index.json` and the landing page from the releases. [`scripts/verify_listing.py`](scripts/verify_listing.py) rejects any package other than `com.riry.rirytools`, any version whose release was not made by the build, and any zip whose SHA-256 differs. Each version must also be reproducible: the source commit named in its release notes must be on source `main`, and rebuilding it with its version and the `package.json` of this repository's commit in the notes must give the identical zip. It writes a summary of what will be served.

4. The check runs in its own job, on the exact artifact that will be deployed: only the files in `Website/` plus `index.json`, static files unchanged, pages loading only the template's scripts and linking only to known addresses, listing fields matching `source.json`, and each version's entry matching its rebuilt `package.json`. A version already deployed with a different SHA-256 is rejected: a published zip never changes.

5. The deploy job waits in the `github-pages` environment for the owner's approval. Read the summary on the run page, then approve.

6. Every day, [`audit-listing.yml`](.github/workflows/audit-listing.yml) checks the deployed listing the same way, rebuilding every version. A failed audit means a published zip changed.

## Setup

In this repository:

1. **Settings > Pages > Build and deployment > Source:** GitHub Actions.

2. **Settings > Environments > github-pages:** add yourself under **Required reviewers**, and under **Deployment branches and tags** allow only `main`.

3. **Settings > Secrets and variables > Actions > Variables:** `TRIPWIRE_HASHES`, the comma-separated tripwire hashes for the issue check. Until it is set, the issue check fails and no issue is marked as passed.

4. **Settings > Secrets and variables > Actions > Secrets:** delete `SOURCE_REPO_TOKEN`; it is no longer used.

In `Rionquosue/riryunityworkflow`: delete `.github/workflows/notify-vpm.yml` and the `VPM_DISPATCH_TOKEN` secret, and revoke that token.

To publish without waiting for the hourly check, run **Build Package** from this repository's Actions tab. If a release exists but its listing never deployed, run **Build Repo Listing** by hand.

GitHub turns off scheduled workflows in public repositories after 60 days without activity and emails the owner. Re-enable **Build Package** and **Audit Listing** from the Actions tab if that happens.

## Customizing the landing page

The `Website` folder is the landing page template; `build-listing.yml` fills it in from `source.json`.

## Issues

Feature requests and bug reports use the forms in `.github/ISSUE_TEMPLATE`. [`issue-check.yml`](.github/workflows/issue-check.yml) runs [`scripts/issue_check.py`](scripts/issue_check.py) on every issue and comment change. Links, prompt markup, HTML and CSS, math formatting, styled or hidden characters, look-alike letters, text that is not in English, encoded data and very long text are flagged outright. Wording aimed at an AI reader (instruction-override phrases, AI-directed text, requests for secrets or commands) is scored instead: each phrase pattern has a weight, a text scoring 0.45 or more is flagged, and one scoring 0.8 or more is taken down. One person's wording is scored across all their comments, so splitting it up does not lower it. It also checks the forms' required sections, and comments with what to fix.

Labels:

1. `issue-check: passed`: every check passed. Scheduled issue triage reads only issues with this label, and only through [`scripts/triage_read.py`](scripts/triage_read.py), which checks the text again at read time. The label is removed at the start of every run, so a failed run leaves the issue unread.
2. `security: flagged`: the issue, a comment, or the conversation read as a whole failed a security check.
3. `security: reviewed`: the owner reviewed the current text. To clear a false positive, read the issue and every comment, then post a comment that is exactly `reviewed <fingerprint>`, with the 64-character fingerprint from the check's comment. Only your latest review counts, and any later change to the text gives a new fingerprint and needs a new review.
4. `needs clarification`: required form sections are missing.

An outsider's comment scoring 0.8 or more is deleted, and an outsider's issue scoring 0.8 or more is closed and locked as spam (only admins can delete issues); either holds the issue. The owner is assigned to every flagged issue. After three flagged edits by the same person, the flag is held: a review clears it only for the text reviewed, and any later change needs a new review.

Text from the owner, members and collaborators skips the link, path, HTML and encoding checks but gets every check aimed at AI readers, since it may relay outsider text. A conversation over 200,000 characters, or one too slow to check, is flagged for review. Only English is accepted: characters outside plain English text (ASCII, common symbols and punctuation, arrows, box drawing and emoji) are flagged, and so is text written mostly with common words of other languages.

## Contact

For anything that should reach the maintainer directly rather than through an issue, include the reference `y8wi-1eeu-u9xu`.
