// Loads a Spine 4.x JSON skeleton with an official spine-core runtime (4.0 to 4.3) and samples
// every animation's pose, so the same rig can be compared across runtime versions. Prints JSON.
//
//   node check_spine4x.js <spine-core.js> <skeleton.json> <samples per animation>
//
// Attachments are loaded through the runtime's own TextureAtlas and AtlasAttachmentLoader, from an
// atlas generated here with a 1x1 region for every image path the skeleton uses. For each skin
// (the default skin alone is sampled when there are no others) and each animation, the pose is
// sampled at evenly spaced times: each bone's world transform, each slot's colors, attachment and
// deform, and the draw order.
"use strict";
const fs = require("fs");
const vm = require("vm");

const [corePath, jsonPath, countArg] = process.argv.slice(2);
const count = Math.max(1, Number(countArg || 8));
const sandbox = { console, Math, Float32Array, Int16Array, Uint16Array, Uint8Array, Int32Array, ArrayBuffer, DataView,
                  Array, Object, Error, Number, String, Map, Set, Symbol, JSON };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(corePath, "utf8") + "\nthis.spine = spine;", sandbox);
const spine = sandbox.spine;

const json = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
const paths = new Set();
for (const skin of json.skins || []) {
  for (const slot of Object.values(skin.attachments || {})) {
    for (const [key, att] of Object.entries(slot)) {
      const type = att.type || "region";
      if (type === "region" || type === "mesh" || type === "linkedmesh") paths.add(att.path || att.name || key);
    }
  }
}
let atlasText = "fake.png\nsize: 2048,2048\nfilter: Linear,Linear\n";
for (const p of paths) atlasText += `${p}\nbounds: 0,0,1,1\n`;
const atlas = new spine.TextureAtlas(atlasText);
for (const page of atlas.pages) {
  const size = { width: 2048, height: 2048 };
  const texture = { getImage: () => size, setFilters() {}, setWraps() {}, dispose() {} };
  if (page.setTexture) page.setTexture(texture); else page.texture = texture;
}
const data = new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(json);

const r = (v, k) => Math.round(v * k) / k;
const applied = o => o.appliedPose || o;  // 4.3 keeps the pose apart from the bone or slot
const result = { version: data.version, skins: data.skins.map(s => s.name), animations: {} };
const skins = data.skins.length > 1 ? data.skins.filter(s => s.name !== "default") : data.skins;
for (const anim of data.animations) {
  const runs = {};
  for (const skin of skins) {
    const samples = [];
    for (let i = 0; i < count; i++) {
      const t = count === 1 ? 0 : anim.duration * i / (count - 1);
      const skeleton = new spine.Skeleton(data);
      if (skin.name !== "default") skeleton.setSkinByName(skin.name);
      if (skeleton.setupPose) skeleton.setupPose(); else skeleton.setToSetupPose();
      const state = new spine.AnimationState(new spine.AnimationStateData(data));
      state.setAnimation(0, anim.name, false);
      state.update(t);
      state.apply(skeleton);
      if (spine.Physics) skeleton.updateWorldTransform(spine.Physics.update); else skeleton.updateWorldTransform();
      const bones = {};
      for (const b of skeleton.bones) {
        const p = applied(b);
        bones[b.data.name] = [r(p.worldX, 100), r(p.worldY, 100), r(p.a, 1e4), r(p.b, 1e4), r(p.c, 1e4), r(p.d, 1e4)];
      }
      const slots = {};
      for (const s of skeleton.slots) {
        const p = applied(s);
        const att = p.getAttachment ? p.getAttachment() : s.getAttachment();
        const c = p.color, d = p.darkColor;
        slots[s.data.name] = { attachment: att ? att.name : null,
          color: [r(c.r, 1e3), r(c.g, 1e3), r(c.b, 1e3), r(c.a, 1e3)],
          dark: d ? [r(d.r, 1e3), r(d.g, 1e3), r(d.b, 1e3)] : null,
          deform: Array.from(p.deform || []).map(v => r(v, 100)) };
      }
      const order = Array.isArray(skeleton.drawOrder) ? skeleton.drawOrder : skeleton.drawOrder.appliedPose;
      samples.push({ time: r(t, 1e4), bones, slots, drawOrder: order.map(s => s.data.name) });
    }
    runs[skin.name] = samples;
  }
  result.animations[anim.name] = { duration: r(anim.duration, 1e4), skins: runs };
}
process.stdout.write(JSON.stringify(result));
