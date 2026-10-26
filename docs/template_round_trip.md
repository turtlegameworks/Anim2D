# Editing presets in Spine (template round trip)

Spine 3.8 has no plugin API, so presets are edited in Spine through files:

```text
Builder ──Generate──▶ package (.json + images) ──Import Data──▶ Spine 3.8.75
   ▲                                                              │ edit
   └──── Import template from Spine… ◀── Export → JSON ◀──────────┘
```

## Steps

1. **Generate** any preset in the builder (or open any Spine 3.8 project of your own).
2. In Spine: **Import Data** → the package's `.json` → New project. See the package's `IMPORT_IN_SPINE.md`.
3. Edit freely: move or add bones and slots, swap or add images, reshape meshes, change keys,
   curves, timing, colours, events and draw order. Save your `.spine` project as usual.
4. In Spine: **Export** → **JSON**, tick **Nonessential data**, export next to your images folder.
5. In the builder: **Import template from Spine…** → pick the exported `.json`.
   * The images folder is found from the export; choose it by hand if not.
   * If the skeleton has several animations, choose the one the template plays.
   * Check the **tint roles**: which slots follow *Primary colour*, which follow *Secondary colour*,
     and which **keep** the colours you animated in Spine.
6. **Register template**. It appears in the browser marked "template" and can be generated like any
   preset, as many times as you need, each with its own instance name.

To update a template, export again and register with the same id and **Replace existing template**
ticked. The version goes up and the old version is moved to `_backup` in the templates folder.

## What the builder changes in a template

| Control | Effect on your Spine data |
| --- | --- |
| Instance name | Prefix for every bone, slot, attachment, event, animation and image folder |
| Primary / secondary colour | Replaces the RGB of slots with that tint role (alpha is kept) |
| Opacity | Multiplies every slot alpha |
| Scale, rotation, position, mirror | Applied to the root bone's setup pose |
| Duration | Retimes every key, event and draw-order change proportionally |
| Start delay | Shifts everything later and keeps slots hidden until it starts |

Everything else, including bone hierarchy, art, meshes, curves and key values, stays exactly as you made it.
Effect-specific controls such as ray count belong to built-in generators and are not available on
templates.

## Supported in templates

Bones; region, mesh, bounding box, point and clipping attachments; rotate, translate, scale and shear
keys; slot colour, two-colour and attachment keys; deform keys; draw order keys; events without audio.

**Not supported yet:** IK, transform and path constraints; linked meshes; skins other than
`default`; audio events. The import dialog names the exact item to remove.

## Where templates live

`Documents\Anim2D\FX Templates\<template id>\` (open it with **Open templates folder**). Each template
folder holds `preset.json`, `template.json` (your Spine data with the instance prefix removed),
`template_meta.json` (tint roles, source file) and `images\`.

## Notes

* The preview does not draw meshes, clipping, shear or draw-order changes; it says so above the
  canvas. Check those in Spine.
* Slots you leave visible at the end of the animation stay visible; the builder warns but does not
  change your authoring.
* Regenerating creates a new package. It never edits a `.spine` project.
