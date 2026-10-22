# Compatibility and verification record

## Anim2D target: Spine 4.0 runtimes (18 Oct 2026)

| Item | Value |
| --- | --- |
| Default export | Spine 4.0 JSON, `skeleton.spine = "4.0.64"`; or 4.0 binary `.skel`; optional `.atlas` + PNG pages |
| Also exported | Spine 3.8 JSON (`3.8.99` for runtimes, `3.8.75` for the 3.8.75 editor) |
| Imported | Spine 3.8.x and 4.0.x JSON |
| Format references | spine-runtimes branch `4.0` (commit `425ce416`): `SkeletonJson.java`, `SkeletonBinary.java`, `Animation.java`; spine-ts `@esotericsoftware/spine-core` 4.0.31 (`TextureAtlas`, `AtlasAttachmentLoader`) |

| Check | Status | How |
| --- | --- | --- |
| 4.0 JSON export plays in the official spine-ts 4.0.31 runtime like the editor shows it | ✅ automated | `tests/test_editor.py`, `tests/test_formats.py` |
| 3.8 ↔ 4.0 conversion, poses compared in both official runtimes | ✅ automated | `tests/test_formats.py` |
| `.skel` loads in the official 4.0 `SkeletonBinary` and matches the JSON in every sampled pose | ✅ automated | `tests/test_binary.py` (Esoteric's spineboy-ess/pro and raptor-pro 4.0 exports, plus a rig covering every other timeline and attachment type) |
| `.atlas` read by the official 4.0 `TextureAtlas`; UVs land on the packed pixels | ✅ automated | `tests/test_atlas.py` |
| IK, transform and path constraints, skins, skin bones/constraints, linked meshes, draw order, clipping vs the official 4.0 runtime | ✅ automated | `tests/test_constraints.py` (spineboy-pro, tank-pro, vine-pro, stretchyman-pro, goblins-pro, mix-and-match-pro, synthetic modes), `tests/test_skins.py` |
| Import → export (JSON and `.skel`) of those rigs poses identically in the runtime | ✅ automated | `tests/test_constraint_editing.py` |
| Mix preview vs `AnimationState` crossfades | ✅ automated | `tests/test_animation_core.py`, `tests/test_constraints.py` |
| spine-unity 4.0 playback in a Unity project | ⏳ pending | spine-unity is not installed yet |
| Import of an Anim2D 4.0 export into the Spine 4.0 editor | ⏳ pending | needs a Spine 4.0 licence |

## Target profile: `spine-3.8.75`

| Item | Value |
| --- | --- |
| Editor | Spine 3.8.75 Pro (data also uses only features available in Essential) |
| Data format | Spine 3.8 JSON, `skeleton.spine = "3.8.75"` |
| Features used | bones, region attachments, slot colour keys, attachment keys, bezier curves |
| Not used | meshes, deform, constraints, events, draw-order keys, skins other than `default` |
| Blend mode | normal only |

Format reference: official 3.8 runtime reader, `EsotericSoftware/spine-runtimes`, branch `3.8`,
`spine-libgdx/.../SkeletonJson.java`, plus the 3.8 `spineboy-ess.json` example export.

## Known issue: 3.8.75 exports and 3.8 runtimes

The official 3.8 runtimes (spine-libgdx, spine-ts, and spine-csharp used by spine-unity) **refuse
skeleton data whose version is exactly `3.8.75`** and ask for a newer export. Esoteric's staff advise
updating to the last 3.8 release, 3.8.99. This affects any game export made with Spine 3.8.75, not
only this tool. A licensed Spine 3.8 owner can install 3.8.99 from the Spine launcher's version setting.

If the editor is updated, change `SPINE_VERSION` and `PROFILE_ID` in
`src/anim2d/spine_adapters/spine38.py` and re-run the checks below.

## Verification status (16 Oct 2026)

| Check | Status | How |
| --- | --- | --- |
| Structure checks (references, names, timing, paths, image sizes) | ✅ automated | `pytest` |
| Read + playback by the official spine-ts 3.8 runtime | ✅ automated | `tests/runtime/check_spine38.js` at spine-runtimes commit `8b4844bd`. The `3.8.75` stamp is rewritten to `3.8.99` **in memory only** because of the known issue above |
| Hidden before delay, visible during, hidden after end | ✅ automated (runtime) | sampled with AnimationState |
| App preview vs official runtime (bone positions ±1 px, visibility, alpha ±0.03) | ✅ automated | `tests/test_session_and_preview.py`, including rotation/scale/mirror/offset |
| Desktop app: edit → validate → generate on worker thread | ✅ automated (offscreen Qt) | `tests/test_ui_smoke.py` |
| Template round trip (simulated artist edit: retime, new bones/slots, mesh + deform, shear, events, draw order) | ✅ automated, incl. official runtime load | `tests/test_templates.py` |
| Template from a real Spine 3.8 export (spineboy-ess, 3.8.55) | ✅ automated, incl. official runtime load | `tests/test_templates.py` |
| Template from a JSON exported by **your** Spine 3.8.75 | ⏳ pending | manual: export, Import template from Spine…, generate, re-import in Spine |
| Import into the Spine 3.8.75 editor | ⏳ **pending** | needs a licensed Spine install; follow the manual checklist |
| Edit keys, save and reopen `.spine` | ⏳ pending | manual |
| Spine command-line import (`.json` → `.spine`) | ⏳ pending | manual |
| Unity (spine-unity 3.8) playback | ⏳ pending | not yet verified in a Unity project |

## Manual Spine checklist

1. Import `examples/fx_impact_01/fx_impact_01.json` into a new project (see its `IMPORT_IN_SPINE.md`).
2. Confirm no missing-image or data warnings.
3. Scrub from 0 to the end: hidden → flash and rays → hidden.
4. Edit a bone, a slot colour key, an alpha key and key timing.
5. Save the project, close Spine, reopen it.
6. Replay once and repeatedly.
7. Move the package folder elsewhere and import again.
8. Export JSON/binary + atlas and check alpha on light and dark backgrounds in the runtime.

Record the date, editor version and result of each step here.
