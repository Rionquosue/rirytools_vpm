# RiryTools VPM Listing

The VRChat Creator Companion (VCC) listing for RiryTools. Add it in the VCC under **Settings > Packages > Add Repository**:

```text
https://rionquosue.github.io/rirytools_vpm/index.json
```

Or open [the listing page](https://rionquosue.github.io/rirytools_vpm/) and press **Add to VCC**.

## How it is built

1. A push to `main` in [Rionquosue/riryunityworkflow](https://github.com/Rionquosue/riryunityworkflow) runs its `notify-vpm.yml`, which dispatches `source-updated` with the commit to this repository.

2. [`build-package.yml`](.github/workflows/build-package.yml) checks out that commit, zips the package with [`scripts/build_vpm.py`](scripts/build_vpm.py) (without `Docs~`, `TestProject~` and dot files), and publishes release `v<version>` with the zip and `package.json`. A version that is already released is skipped, so bump `"version"` in the source `package.json` to publish.

3. It then runs [`build-listing.yml`](.github/workflows/build-listing.yml), VRChat's [package-list-action](https://github.com/vrchat-community/package-list-action), which builds `index.json` and the landing page from every release listed in [`source.json`](source.json) and deploys them to GitHub Pages.

## Setup

In this repository:

1. **Settings > Pages > Build and deployment > Source:** GitHub Actions.

2. **Settings > Secrets and variables > Actions > Secrets:** `SOURCE_REPO_TOKEN`, a fine-grained personal access token with **Contents: Read-only** on `Rionquosue/riryunityworkflow`.

In `Rionquosue/riryunityworkflow`:

3. **Settings > Secrets and variables > Actions > Secrets:** `VPM_DISPATCH_TOKEN`, a fine-grained personal access token with **Contents: Read and write** on `Rionquosue/rirytools_vpm`.

To publish without a push, run **Build Package** from this repository's Actions tab (it takes a source branch, tag or commit), or **Notify VPM repository** from the source repository's.

## Customizing the landing page

The `Website` folder is the landing page template; `build-listing.yml` fills it in from `source.json`.

## Issues

Feature requests and bug reports use the forms in `.github/ISSUE_TEMPLATE`. [`issue-check.yml`](.github/workflows/issue-check.yml) runs [`scripts/issue_check.py`](scripts/issue_check.py) on every issue and comment change. It flags links, instruction-override phrases, AI-directed text, prompt markup, requests for secrets or commands, HTML and CSS, math formatting, styled or hidden characters, look-alike letters, encoded data and very long text. It also checks the forms' required sections, and comments with what to fix.

Labels:

1. `issue-check: passed`: every check passed. Scheduled issue triage reads only issues with this label. It is removed at the start of every run, so a failed run leaves the issue unread.
2. `security: flagged`: the issue or a comment failed a security check.
3. `security: reviewed`: add it yourself to clear a false positive. It is removed whenever someone other than the owner changes the issue or comments.
4. `needs clarification`: required form sections are missing.

Text from the owner, members and collaborators is not security-checked.
