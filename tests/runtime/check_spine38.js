// Loads a generated skeleton with the official spine-ts 3.8 runtime and samples
// slot visibility/alpha over time. Prints JSON to stdout.
//
//   node check_spine38.js <spine-core.js> <skeleton.json|skeleton.skel> [--anim=<name>] [--full] <time> ...
//
// A .skel file is read with the runtime's SkeletonBinary (4.0 cores). --full adds every slot's
// colors and attachment and each bone's world matrix to the samples. --atlas=<file.atlas> loads
// attachments through the runtime's TextureAtlas and AtlasAttachmentLoader (no textures; page
// sizes come from the atlas) and reports every default-skin attachment's offsets and UVs.
// --mix=<from>:<to>:<switch>:<duration>[:loop] plays <from>, applies it at <switch> seconds, sets
// <to> with that mix duration on the same track, and samples at each time (loop: both loop).
// --skin=<name> sets that skin before sampling.
"use strict";
const fs = require("fs");
const vm = require("vm");

// Optional "--anim=<name>" after the JSON path picks the animation (default: the first one).
let [corePath, jsonPath, ...times] = process.argv.slice(2);
let animName = null;
let full = false, atlasPath = null, mix = null, skinName = null;
while (times.length && times[0].startsWith("--")) {
  const flag = times.shift();
  if (flag.startsWith("--anim=")) animName = flag.slice(7);
  else if (flag.startsWith("--mix=")) {
    const [from, to, at, duration, loop] = flag.slice(6).split(":");
    mix = { from, to, at: Number(at), duration: Number(duration), loop: loop === "loop" };
  }
  else if (flag === "--full") full = true;
  else if (flag.startsWith("--skin=")) skinName = flag.slice(7);
  else if (flag.startsWith("--atlas=")) atlasPath = flag.slice(8);
  else throw new Error("unknown flag " + flag);
}
const sandbox = { console, Math, Float32Array, Int16Array, Uint16Array, Uint8Array, Int32Array, ArrayBuffer, DataView,
                  Array, Object, Error, Number, String };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(corePath, "utf8") + "\nthis.spine = spine;", sandbox);
const spine = sandbox.spine;

// Atlas-free loader: real region attachments backed by a full-size fake region.
const fakeRegion = () => ({ u: 0, v: 0, u2: 1, v2: 1, rotate: false, degrees: 0, offsetX: 0, offsetY: 0,
  width: 1, height: 1, originalWidth: 1, originalHeight: 1, renderObject: null });
let loader = {
  newRegionAttachment(skin, name, path) {
    const att = new spine.RegionAttachment(name);
    att.path = path;
    att.setRegion(fakeRegion());
    return att;
  },
  newMeshAttachment(skin, name, path) {
    const att = new spine.MeshAttachment(name);
    att.path = path;
    att.region = fakeRegion();
    return att;
  },
  newBoundingBoxAttachment(skin, name) { return new spine.BoundingBoxAttachment(name); },
  newPathAttachment(skin, name) { return new spine.PathAttachment(name); },
  newPointAttachment(skin, name) { return new spine.PointAttachment(name); },
  newClippingAttachment(skin, name) { return new spine.ClippingAttachment(name); },
};

// Official 3.8 runtimes refuse data stamped exactly "3.8.75" (a known-bad editor build).
// Restamp in memory only, so the rest of the data is still checked by the real reader.
let atlas = null;
if (atlasPath) {
  atlas = new spine.TextureAtlas(fs.readFileSync(atlasPath, "utf8"));
  for (const page of atlas.pages) {
    const size = { width: page.width, height: page.height };
    page.texture = { getImage: () => size };
  }
  loader = new spine.AtlasAttachmentLoader(atlas);
}
let data, originalVersion, restamped = false;
if (jsonPath.endsWith(".skel")) {
  const bytes = fs.readFileSync(jsonPath);
  // A copy, not a view: small files can sit at a non-zero offset inside Node's pooled buffer, and the
  // runtime's BinaryInput reads data.buffer from its start.
  data = new spine.SkeletonBinary(loader).readSkeletonData(Uint8Array.from(bytes));
  originalVersion = data.version;
} else {
  const json = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
  originalVersion = json.skeleton && json.skeleton.spine;
  restamped = originalVersion === "3.8.75";
  if (restamped) json.skeleton.spine = "3.8.99";
  data = new spine.SkeletonJson(loader).readSkeletonData(json);
}
const result = {
  version: originalVersion,
  restamped_for_runtime_check: restamped,
  bones: data.bones.length,
  slots: data.slots.length,
  animations: data.animations.map(a => ({ name: a.name, duration: a.duration })),
  skins: data.skins.map(s => ({ name: s.name, bones: s.bones.length, constraints: s.constraints.length,
    attachments: s.getAttachments().map(e => e.slotIndex + ":" + e.name + ":" + e.attachment.name).sort() })),
  events: data.events.map(e => [e.name, e.intValue, e.floatValue, e.stringValue, e.audioPath, e.volume, e.balance]),
  samples: [],
};

if (atlas) {
  result.pages = atlas.pages.map(p => ({ name: p.name, width: p.width, height: p.height, pma: !!p.pma }));
  result.attachments = data.defaultSkin.getAttachments().map(e => {
    const att = e.attachment, out = { slot: data.slots[e.slotIndex].name, name: e.name };
    if (att instanceof spine.RegionAttachment) Object.assign(out, { type: "region", page: att.region.page.name,
      offset: Array.from(att.offset), uvs: Array.from(att.uvs) });
    else if (att instanceof spine.MeshAttachment) Object.assign(out, { type: "mesh", page: att.region.page.name,
      regionUVs: Array.from(att.regionUVs), uvs: Array.from(att.uvs) });
    return out;
  });
}
const anim = animName ? data.findAnimation(animName) : data.animations[0];
if (!anim && (animName || times.length)) throw new Error("animation not found: " + animName);
for (const t of times.map(Number)) {
  const skeleton = new spine.Skeleton(data);
  if (skinName) skeleton.setSkinByName(skinName);  // also activates the skin's bones and constraints
  skeleton.setToSetupPose();
  const state = new spine.AnimationState(new spine.AnimationStateData(data));
  if (mix) {
    state.setAnimation(0, mix.from, mix.loop);
    state.update(Math.min(t, mix.at));
    state.apply(skeleton);
    if (t >= mix.at) {  // the from entry was applied once, so the runtime mixes from it
      state.setAnimation(0, mix.to, mix.loop).mixDuration = mix.duration;
      state.update(t - mix.at);
      state.apply(skeleton);
    }
  } else {
    state.setAnimation(0, anim.name, false);
    state.update(t);
    state.apply(skeleton);
  }
  skeleton.updateWorldTransform();
  const visible = skeleton.drawOrder
    .filter(s => s.getAttachment() !== null && s.bone.active)  // renderers skip inactive (skin) bones
    .map(s => {
      const entry = { slot: s.data.name, alpha: Math.round(s.color.a * 1000) / 1000,
                      x: Math.round(s.bone.worldX * 10) / 10, y: Math.round(s.bone.worldY * 10) / 10 };
      const att = s.getAttachment();
      if (att instanceof spine.MeshAttachment) {
        const out = new Array(att.worldVerticesLength);
        att.computeWorldVertices(s, 0, att.worldVerticesLength, out, 0, 2);
        entry.attachment = att.name;
        entry.mesh = out.map(v => Math.round(v * 100) / 100);
      }
      return entry;
    });
  const bones = {};
  const r = (v, k) => Math.round(v * k) / k;
  for (const b of skeleton.bones) {
    bones[b.data.name] = [r(b.worldX, 100), r(b.worldY, 100)];
    if (full) bones[b.data.name].push(r(b.a, 1e4), r(b.b, 1e4), r(b.c, 1e4), r(b.d, 1e4));
  }
  const sample = { time: t, visible, bones };
  if (full) {
    sample.slots = {};
    for (const s of skeleton.slots) {
      const att = s.getAttachment();
      const c = s.color, d = s.darkColor;
      sample.slots[s.data.name] = { attachment: att ? att.name : null,
        color: [r(c.r, 1e3), r(c.g, 1e3), r(c.b, 1e3), r(c.a, 1e3)],
        dark: d ? [r(d.r, 1e3), r(d.g, 1e3), r(d.b, 1e3)] : null };
    }
    sample.drawOrder = skeleton.drawOrder.map(s => s.data.name);
    sample.ik = skeleton.ikConstraints.map(c => [r(c.mix, 1e4), r(c.softness, 100), c.bendDirection, c.compress, c.stretch]);
  }
  result.samples.push(sample);
}
process.stdout.write(JSON.stringify(result));
