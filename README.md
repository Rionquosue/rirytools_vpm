# RiryTools VPM Listing

The VRChat Creator Companion (VCC) listing for RiryTools. Add it in the VCC under **Settings > Packages > Add Repository**:

```text
https://rionquosue.github.io/rirytools_vpm/index.json
```

Or open [the listing page](https://rionquosue.github.io/rirytools_vpm/) and press **Add to VCC**.

## How it is built

1. A push to `main` or `beta` of [Rionquosue/riryunityworkflow](https://github.com/Rionquosue/riryunityworkflow) (the source) runs its `notify-vpm.yml`, which starts [`build-package.yml`](.github/workflows/build-package.yml) here with the channel (`stable` for `main`, `beta` for `beta`) and the commit.

2. [`scripts/build_vpm.py`](scripts/build_vpm.py) zips the source's `com.riry.rirytools` folder (without dot files) (`.meta` files included) with [`package.json`](package.json) from here, the version and url filled in.

3. Versions come only from this repository's releases. A stable version is the previous stable release plus one patch (`0.7.2` to `0.7.3`; `0.0.1` if there is none). A beta is the next stable version with `-beta.N` (`0.7.3-beta.1`, `0.7.3-beta.2`) and is released as a prerelease. For a minor or major version, run **Build Package** by hand with the `version` input.

4. If the zip, built with the previous release's version, is identical to that release's zip, the run fails with "No change": nothing in the package changed.

5. The release `v<version>` gets the zip and `package.json`, then [`build-listing.yml`](.github/workflows/build-listing.yml) runs VRChat's [package-list-action](https://github.com/vrchat-community/package-list-action) (pinned to a commit) to build `index.json` and the landing page. [`scripts/verify_listing.py`](scripts/verify_listing.py) checks the artifact: only the files in `Website/` plus `index.json`, static files unchanged, pages loading only the template's scripts and linking only to known addresses, listing fields matching `source.json`, only `com.riry.rirytools`, and each version's url pointing at its release here.

6. The deploy job then publishes it to GitHub Pages without waiting; merging to the source's `main` is the approval. Betas show in the VCC only for users who turn on pre-release packages.

## Setup

1. **Settings > Pages > Build and deployment > Source:** GitHub Actions.

2. **Settings > Environments > github-pages:** leave **Required reviewers** empty, and under **Deployment branches and tags** allow only `main`.

3. **Settings > Secrets and variables > Actions > Variables:** `TRIPWIRE_HASHES`, the comma-separated tripwire hashes for the issue check. Until it is set, the issue check fails and no issue is marked as passed.

4. In `Rionquosue/riryunityworkflow`, add the secret `VPM_DISPATCH_TOKEN`: a fine-grained personal access token with access to `Rionquosue/rirytools_vpm` only and the single permission **Actions: Read and write**. It can start workflows here and nothing else.

5. Create a `beta` branch in both repositories from `main`. The `beta` branch here is only for trying changes to these scripts; builds always run from `main`.

To publish by hand, run **Build Package** from the Actions tab. If a release exists but its listing never deployed, run **Build Repo Listing** by hand.

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
