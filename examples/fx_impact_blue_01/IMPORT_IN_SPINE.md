# Import fx_impact_blue_01 into Spine 3.8.75

Effect: **Impact Burst** (preset `impact_basic` v1)
Animation: `fx_impact_blue_01_play`

## Open as a new project

1. Start Spine 3.8.75.
2. Click the Spine logo (top left) and choose **Import Data**.
3. Set **Input** to `fx_impact_blue_01.json` from this folder.
4. Choose **New project**, keep **Scale** at 1, and click **Import**.
5. Spine reads images from the `images` folder next to the JSON. If any image shows as
   missing, select the **Images** node in the tree and point it at this package's `images` folder.
6. Switch to **Animate** mode, select `fx_impact_blue_01_play` in the tree, and press play.
7. Save with **Save Project As** to create your own `.spine` file.

The effect is hidden before it starts and after it ends; scrub the timeline to see it.

## Editing

* Everything is normal Spine data: move bones, change slot colours, drag keys in the Dopesheet.
* Colour is a slot tint over white artwork, so recolour by changing slot colour keys.
* Regenerating this effect from the builder creates a new package; it does not keep edits you made
  in Spine. To keep your edits as a reusable preset, use the round trip below.

## Turn your edits into a reusable template

1. In Spine choose **Export → JSON** with **Nonessential data** ticked.
2. In the builder click **Import template from Spine…** and pick that JSON.
3. Your edited effect becomes a template you can generate again with new names, colours and timing.

## Adding it to a character

* **Import Data → Existing skeleton** brings in bones, slots and images but **not animations**.
* To copy the animation into a character, use the builder's character merge (planned), or
  rebuild the keys by hand in the character project.

## Exporting for the game

Export with Spine 3.8.75 and use a **3.8 runtime** (e.g. spine-unity for 3.8). 4.x runtimes cannot
load 3.8 exports. When packing an atlas, match the runtime's premultiplied-alpha setting; the source
PNGs here use straight (non-premultiplied) alpha.
