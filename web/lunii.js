// The Lunii export: the same story as a STUdio archive, the format Lunii's own tools
// import. Port of src/daisy2telmi/lunii.py.
//
// A Telmi pack is a STUdio pack unfolded, so this walks it back: the cover node is
// rebuilt, the stages get UUIDs again, and the media is renamed to the eight hex
// characters STUdio uses.
//
// The other export is the `.plain.pk`, the layout a story card keeps on itself: the
// same graph flattened into fixed-size records. Port of src/daisy2telmi/lunii_pk.py.

import { hexToBytes, uuid5 } from "./telmi.js";
import { writeZip } from "./zip.js";

const COVER_CONTROLS = { wheel: true, ok: true, home: false, pause: false, autoplay: false };
const IMAGE_EXTS = [".png", ".bmp", ".jpg", ".jpeg", ".gif", ".webp"];
const AUDIO_EXTS = [".mp3", ".ogg", ".wav", ".flac", ".m4a", ".aac"];

const extOf = (name) => {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot).toLowerCase();
};
const stemOf = (name) => {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? name : name.slice(0, dot);
};

async function sha1Hex(value) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-1", new TextEncoder().encode(value)));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** STUdio asset name: eight hex characters, like the published packs use. */
async function assetName(source, extension, taken) {
  const digest = (await sha1Hex(source)).toUpperCase();
  for (let offset = 0; offset < 25; offset++) {
    const name = digest.slice(offset, offset + 8);
    if (name.length === 8 && !taken.has(name)) {
      taken.add(name);
      return name + extension;
    }
  }
  throw new Error(`cannot name the asset ${source}`);
}

/**
 * story: what buildStoryFiles returns — {dirName, files, metadata, nodes}.
 */
export async function buildStudioFiles(story) {
  const { dirName, metadata, nodes } = story;
  if (!metadata?.uuid) throw new Error(`the story has no usable uuid: ${metadata?.uuid}`);

  const stages = nodes?.stages || {};
  const actions = nodes?.actions || {};
  if (!Object.keys(stages).length || !Object.keys(actions).length) {
    throw new Error("nodes.json has no stages or actions");
  }

  const media = new Map();
  for (const file of story.files) {
    if (file.name.startsWith(`${dirName}/`)) media.set(file.name.slice(dirName.length + 1), file.data);
  }
  const find = (folder, name, exts) => {
    if (!name) return null;
    const direct = folder ? `${folder}/${name}` : name;
    if (media.has(direct)) return direct;
    for (const extension of exts) {
      const candidate = folder ? `${folder}/${stemOf(name)}${extension}` : `${stemOf(name)}${extension}`;
      if (media.has(candidate)) return candidate;
    }
    return null;
  };

  // Deterministic, so converting the same story twice writes the same archive.
  const stageUuids = {};
  for (const key of Object.keys(stages)) stageUuids[key] = await uuid5(metadata.uuid, `stage:${key}`);

  const taken = new Set();
  const assets = [];
  const named = new Map();
  const register = async (path, kind) => {
    if (path === null) return null;
    if (!named.has(path)) {
      const extension = kind === "image" ? ".png" : extOf(path);
      const name = await assetName(path, extension, taken);
      named.set(path, name);
      assets.push({ name, data: media.get(path) });
    }
    return named.get(path);
  };

  /** Telmi `{action, index}` becomes STUdio `{actionNode, optionIndex}`. */
  const transition = (move) =>
    move && actions[move.action] ? { actionNode: move.action, optionIndex: move.index ?? 0 } : null;

  const coverAudio = find(null, "title.mp3", AUDIO_EXTS);
  if (coverAudio === null) throw new Error("the story has no title.mp3, which a Lunii story starts on");

  const stageNodes = [
    {
      uuid: metadata.uuid,
      type: "cover",
      name: metadata.title || dirName,
      position: { x: 0, y: 0 },
      image: await register(find(null, "title.png", IMAGE_EXTS), "image"),
      audio: await register(coverAudio, "audio"),
      okTransition: transition(nodes.startAction),
      homeTransition: null,
      controlSettings: { ...COVER_CONTROLS },
      squareOne: true,
    },
  ];

  for (const [key, stage] of Object.entries(stages)) {
    const controls = stage.control || {};
    stageNodes.push({
      uuid: stageUuids[key],
      type: "stage",
      name: key,
      position: { x: 0, y: 0 },
      image: await register(find("images", stage.image, IMAGE_EXTS), "image"),
      audio: await register(find("audios", stage.audio, AUDIO_EXTS), "audio"),
      okTransition: transition(stage.ok),
      // A lit home with nothing behind it is the device's own way out of a story,
      // which is what Telmi's home button means too.
      homeTransition: transition(stage.home),
      controlSettings: {
        wheel: Boolean(controls.wheel),
        ok: Boolean(controls.ok),
        home: Boolean(controls.home),
        pause: Boolean(controls.pause),
        autoplay: Boolean(controls.autoplay),
      },
    });
  }

  const actionNodes = [];
  for (const [actionId, options] of Object.entries(actions)) {
    const targets = options.filter((option) => stageUuids[option.stage]).map((option) => stageUuids[option.stage]);
    if (targets.length) actionNodes.push({ id: actionId, name: actionId, position: { x: 0, y: 0 }, options: targets });
  }

  const known = new Set(actionNodes.map((action) => action.id));
  for (const node of stageNodes) {
    for (const field of ["okTransition", "homeTransition"]) {
      if (node[field] && !known.has(node[field].actionNode)) node[field] = null;
    }
  }

  const storyJson = {
    format: "v1",
    version: metadata.version || 1,
    title: metadata.title || dirName,
    description: metadata.description || "",
    // The flag only says the story can be followed without touching the machine.
    // A converted audiobook is a chain of chapters whose only transition is
    // "next", so it always can.
    nightModeAvailable: true,
    stageNodes,
    actionNodes,
  };
  if (metadata.age !== undefined && metadata.age !== null) storyJson.age = metadata.age;
  if (metadata.category) storyJson.category = metadata.category;

  const thumbnail = find(null, metadata.image || "cover.png", IMAGE_EXTS);
  return { storyJson, assets, thumbnail: thumbnail === null ? null : media.get(thumbnail) };
}

export async function buildStudioZip(story) {
  const { storyJson, assets, thumbnail } = await buildStudioFiles(story);
  const entries = [{ name: "story.json", data: new TextEncoder().encode(JSON.stringify(storyJson)) }];
  if (thumbnail !== null) entries.push({ name: "thumbnail.png", data: thumbnail });
  for (const asset of assets.sort((a, b) => (a.name < b.name ? -1 : 1))) {
    entries.push({ name: `assets/${asset.name}`, data: asset.data });
  }
  return writeZip(entries);
}

const FORMAT_VERSION = 1;
const HEADER_SIZE = 0x200;
const NODE_SIZE = 0x2c;
// The device's own way of saying "there is nowhere to go from here".
const NO_MOVE = -1;

/** `ri`/`si`: twelve characters per media file, `000\` then eight hex. */
function records(stems) {
  return new TextEncoder().encode(stems.map((stem) => `000\\${stem}`).join(""));
}

/**
 * `li`, and where each action node starts in it.
 *
 * An action node owns one unbroken run of stage indices; a stage's OK or home press
 * is then (start of that run, its length, which of its entries to take).
 */
function graph(story) {
  const position = new Map(story.stageNodes.map((node, index) => [node.uuid, index]));
  const order = new Map();
  const li = [];
  for (const action of story.actionNodes) {
    const targets = action.options.map((option) => position.get(option));
    if (!targets.length) continue;
    order.set(action.id, [li.length, targets.length]);
    li.push(...targets);
  }
  return { li, order };
}

/** The `li` the device reads: one stage index each, padded to eight bytes. */
function liBytes(li) {
  const bytes = new Uint8Array(Math.max(8, 4 * li.length));
  const view = new DataView(bytes.buffer);
  li.forEach((index, at) => view.setInt32(4 * at, index, true));
  return bytes;
}

/** The `ni` the device reads: a 0x200 header, then one 0x2C record per stage. */
function niBytes(story, ri, si, order) {
  const stages = story.stageNodes;
  const bytes = new Uint8Array(HEADER_SIZE + NODE_SIZE * stages.length);
  const view = new DataView(bytes.buffer);
  view.setUint16(0, FORMAT_VERSION, true);
  view.setUint16(2, story.version || 1, true);
  view.setUint32(4, HEADER_SIZE, true);
  view.setUint32(8, NODE_SIZE, true);
  view.setUint32(12, stages.length, true);
  view.setUint32(16, ri.size, true);
  view.setUint32(20, si.size, true);

  let at = HEADER_SIZE;
  for (const stage of stages) {
    const move = (field) => {
      const step = stage[field];
      const place = step ? order.get(step.actionNode) : undefined;
      return place ? [place[0], place[1], step.optionIndex ?? 0] : [NO_MOVE, NO_MOVE, NO_MOVE];
    };
    const index = (table, name) => (name && table.has(name) ? table.get(name) : NO_MOVE);
    const controls = stage.controlSettings || {};
    [index(ri, stage.image), index(si, stage.audio), ...move("okTransition"), ...move("homeTransition")].forEach(
      (value, word) => view.setInt32(at + 4 * word, value, true),
    );
    ["wheel", "ok", "home", "pause", "autoplay"].forEach((name, word) =>
      view.setUint16(at + 32 + 2 * word, controls[name] ? 1 : 0, true),
    );
    at += NODE_SIZE;
  }
  return bytes;
}

/** `<Title>.<8 hex>.plain.pk`, the name the published packs use. */
export function packName(title, uuid) {
  const clean = (title || "").replace(/[^\p{L}\p{N}_\- ]+/gu, "").trim();
  return `${clean || "story"}.${uuid.replace(/-/g, "").slice(24).toUpperCase()}.plain.pk`;
}

/**
 * The `.plain.pk`: the same story as the STUdio archive above, in the layout the
 * card keeps before Lunii.QT ciphers it onto one. `picture` draws each card as the
 * device stores it, which is cover.js's job.
 */
export async function buildPlainPk(story, { picture }) {
  const { storyJson, assets, thumbnail } = await buildStudioFiles(story);

  const pictures = [];
  const sounds = [];
  const ri = new Map();
  const si = new Map();
  for (const asset of assets) {
    const stem = asset.name.slice(0, asset.name.lastIndexOf(".")).toUpperCase();
    if (IMAGE_EXTS.includes(extOf(asset.name))) {
      ri.set(asset.name, pictures.length);
      pictures.push({ stem, data: asset.data });
    } else {
      si.set(asset.name, sounds.length);
      sounds.push({ stem, data: asset.data });
    }
  }

  const { li, order } = graph(storyJson);
  const entries = [];
  if (storyJson.nightModeAvailable) entries.push({ name: "nm", data: new Uint8Array(0) });
  entries.push({ name: "li.plain", data: liBytes(li) });
  entries.push({ name: "ni", data: niBytes(storyJson, ri, si, order) });
  entries.push({ name: "ri.plain", data: records(pictures.map((image) => image.stem)) });
  entries.push({ name: "si.plain", data: records(sounds.map((sound) => sound.stem)) });
  for (const image of pictures) entries.push({ name: `rf/000/${image.stem}.bmp`, data: await picture(image.data) });
  // The page has no encoder, so the pack's own MP3s go in as they are; the desktop
  // build cuts anything the device cannot play down to 44100 Hz mono first.
  for (const sound of sounds) entries.push({ name: `sf/000/${sound.stem}.mp3`, data: sound.data });
  entries.push({ name: "uuid.bin", data: hexToBytes(story.metadata.uuid) });
  entries.push({
    name: "_metadata.json",
    data: new TextEncoder().encode(
      JSON.stringify({
        uuid: story.metadata.uuid.toUpperCase(),
        title: storyJson.title,
        description: storyJson.description,
      }),
    ),
  });
  if (thumbnail !== null) entries.push({ name: "_thumbnail.png", data: thumbnail });
  return writeZip(entries);
}
