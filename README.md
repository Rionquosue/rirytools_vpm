# RiryTools VPM Listing

The VRChat Creator Companion (VCC) listing for RiryTools (`com.riry.rirytools`) and Riry's Animator Templates (`com.riry.animatortemplates`). Add it in the VCC under **Settings > Packages > Add Repository**:

```text
https://rionquosue.github.io/rirytools_vpm/index.json
```

Or open [the listing page](https://rionquosue.github.io/rirytools_vpm/) and press **Add to VCC**.

| Package | Source | Fields file | Tags |
|---|---|---|---|
| `com.riry.rirytools` | [Rionquosue/riryunityworkflow](https://github.com/Rionquosue/riryunityworkflow) | [`package.json`](package.json) | `v<version>` |
| `com.riry.animatortemplates` | [Rionquosue/riryanimatortemplates](https://github.com/Rionquosue/riryanimatortemplates) | [`animatortemplates.package.json`](animatortemplates.package.json) | `animatortemplates-v<version>` |

#### Releases on this are automatically built from commits on the main tool repositories.

## Features

- **Core Features**
  - Automated GUID repair tool.
      - Searches GIT commit history
      - Caches GUID's for scene/prefab object references
      - Allows the user to redirect the repair if automatic re-linking fails
      - Allows the user to change all GUID's in a directory so they can manually repair a scene if required without losing work or overwriting existing work when importing an updated avatar file.
  - Migration tool
      - Replicates a lot of the behaviour present in Pumkin's avatar copier, but specifically shifts tooling to migrate from an old build to a new build.
      - Fallbacks and repathing failsafes, scale detection, etc implemented to minimize human effort between release revisions.
      - Repairs unpacked prefabs by re-linking source and applying scene overrides where able, preventing exploded mesh due to bone index errors.
  - Icon capture tool
      - Gives a repeatable, quick-action icon generator.
      - Mostly useful for beta releases or personal avatars.
      - Jobs can be repeated to re-capture each image and apply the effect stack automatically, moves previous captures to a _OLD<n> folder and links menus/prefabs by re-using the old GUID. Moved captures get a fresh GUID.
      - Calls GestureManager to assemble a demo menu item to show the user how the icon will look applied to a menu.
  - Import presets
      - Allows the user to define every file within a folder with a Unity preset that will be applied automatically on import.
      - Gives the ability to call tools like better FBX from .blend files.
      - Automatic animation extraction before humanoid import rig is applied.
  - Animation clean-up tools
      - Imported animations extracted from a .blend or .fbx file often key every bone.
      - Removes all blank keys within a floating point tolerance of rest position ignoring non-zero or non-rest, adding a whitelist or manual remove function for things that are intended to be there.
      - Allows for remapping or retargeting by calling HFCRed's animation repath tool.
  - Menu layout tool
      - Uses GestureManager to create an interactive menu in edit mode.
      - Right click to move or rename menu paths.
      - Automatically determines if VRCFury Move Menu or hard repath is required.
      - Can assign icons by dragging and dropping them onto the menu item.
  - Menu mockup tool
      - Uses Flexbox style layout editing to arrange blocks.
      - Allows the user to mock-up a menu quickly.
      - Can be used to provide better direction in a feature request, or provide proper direction to clanker slave on how to lay it out (they suck at UI)
      - It is preferable to include one of the mockup yml files when requesting a feature.

# Disclosure:
Much of the content of these tool repositories is vibe coded. I do review it, but I am not an amazing programmer. Use at your own discretion, I do not claim ownership of any of the machine generated code. You can copy, reuse, retool, or do whatever as you please. I only ask that you properly state that LLM generated code was used in the creation of whatever product you create out of this code.

## My stance:
I do not believe that using a tool that has AI assistance in creating it makes the product as a result AI generated. The idea of this is to assist in artists or HUMANS specifically creating what they want to create with less limitations on repetitive action. To that end I have used my unity knowledge to implement tools that will save time on these repetitive actions that are primarily a limitation of Unity's poor workflow to begin with.

# I BELIEVE HUMANS SHOULD DO THE ARTISTIC PART
### But,
AI isn't going anywhere. It has innovated itself out of relevance at the large scales, you can host at home something that only frontier models could do six months ago on ancient hardware like a V100 and get 90% + the features and result quality. Especially cybersecurity will be affected by this (see GLM Flash models being run on small servers, they can be used for brute force reverse engineering with relative ease in a harness like Hermes) and it will trickle to you. Get educated, speak with knowledge, know the limits, know how to keep it out of your life if you want. The important part is that by educating yourself, you take back control over that choice.

Why did I use Claude for this? I'm lazy, built in harness is good enough. It's cheap right now. I expect it won't be in six months time so I'm burning all the tokens on tooling before I have to maintain this code myself. (I have bought many books and intend to do that long term.)


## Contact

For anything that should reach the maintainer directly rather than through an issue, include the reference `y8wi-1eeu-u9xu`.
