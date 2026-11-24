// Reading a converted story back, and walking it the way the machine does.
//
// The three archives this tool writes hold the same story three ways: Telmi's
// `nodes.json`, STUdio's `story.json`, and the card's own `ni` records. Each reader
// unfolds its own into one model, so the player and its buttons know one thing only:
// a stage, its media, and where OK, home and the wheel lead.

const NO_MOVE = -1;
// A card's media table: `000\` then the eight hex characters of the file's stem.
const MEDIA_RECORD = 12;
// The five switches a card's stage record carries, in the order it carries them.
const FLAGS = [32, 34, 36, 38, 40];

function view(bytes) {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
}

function decodeText(bytes) {
  return new TextDecoder("utf-8").decode(bytes);
}

function jsonOf(entries, name) {
  const entry = entries.find((file) => file.name === name);
  if (!entry) throw new Error(`the story has no ${name}`);
  return JSON.parse(decodeText(entry.data));
}

function bytesOf(byPath, path) {
  if (!path) return null;
  const found = byPath.get(path);
  if (found === undefined) throw new Error(`the story points at ${path}, which it does not carry`);
  return found;
}

/** Telmi nests the story folder one level deep; STUdio and the card do not. */
function unwrap(entries) {
  const first = entries.find((entry) => entry.name.includes("/"));
  if (!first || entries.some((entry) => entry.name === "story.json" || entry.name === "ni")) return entries;
  const root = first.name.slice(0, first.name.indexOf("/"));
  if (!entries.every((entry) => entry.name.startsWith(`${root}/`))) return entries;
  return entries.map((entry) => ({ ...entry, name: entry.name.slice(root.length + 1) }));
}

export function formatOf(raw) {
  const entries = unwrap(raw);
  if (entries.some((entry) => entry.name === "ni")) return "pk";
  if (entries.some((entry) => entry.name === "story.json")) return "lunii";
  if (entries.some((entry) => entry.name === "nodes.json")) return "telmi";
  throw new Error("no nodes.json, story.json or ni inside");
}

/**
 * The story, however it arrived: {title, machine, start, stages, chapters, clips},
 * each stage {id, image, audio, ok, home, wheel, controls}.
 *
 * `wheel` is the list the wheel scrolls through, which is the options of the action
 * the stage belongs to, and holds the stage itself. `chapters` is the same run seen
 * as the book sees it — the chapter cards in the order they are told — and `clips`
 * each card's own audio. The Telmi has no wheel, so its side buttons answer from
 * those two lists.
 */
export async function readStory(entries) {
  const files = unwrap(entries);
  const byPath = new Map(files.map((entry) => [entry.name, entry.data]));
  let story;
  switch (formatOf(files)) {
    case "pk":
      story = readCard(files, byPath);
      break;
    case "lunii":
      story = readStudio(jsonOf(files, "story.json"), byPath);
      break;
    default:
      story = readTelmi(jsonOf(files, "nodes.json"), byPath);
  }
  // STUdio's wheel run is already the list of stages the side buttons scroll.
  return { chapters: [], clips: [], ...story };
}

/** Telmi's graph: named stages, and actions that hold a run of them. */
function readTelmi(nodes, byPath) {
  const runs = {};
  for (const [action, options] of Object.entries(nodes.actions || {})) {
    runs[action] = options.map((option) => option.stage);
  }
  const target = (move) => (move && runs[move.action] ? runs[move.action][move.index ?? 0] : null);

  const stages = new Map();
  for (const [id, stage] of Object.entries(nodes.stages || {})) {
    const controls = stage.control || {};
    stages.set(id, {
      id,
      image: bytesOf(byPath, stage.image && `images/${stage.image}`),
      audio: bytesOf(byPath, stage.audio && `audios/${stage.audio}`),
      ok: target(stage.ok),
      home: target(stage.home),
      wheel: [],
      controls: {
        wheel: false,
        ok: controls.ok !== false,
        home: controls.home !== false,
        pause: false,
        autoplay: Boolean(controls.autoplay),
      },
    });
  }

  const start = target(nodes.startAction);
  if (!start) throw new Error("nodes.json says where to start, and this one says nowhere");
  const listed = byPath.get("metadata.json");
  const run = Object.values(runs).find((options) => options.includes(start)) || [start];
  const chapters = run.filter((id) => stages.has(id));
  return {
    title: (listed ? JSON.parse(decodeText(listed)).title : "") || "",
    machine: "telmi",
    start,
    stages,
    chapters,
    clips: chapters.map((id) => stages.get(id).ok),
  };
}

/** STUdio's graph: UUID'd stages, and action nodes holding their options. */
function readStudio(story, byPath) {
  const runs = {};
  for (const action of story.actionNodes || []) runs[action.id] = action.options;
  const target = (move) => (move && runs[move.actionNode] ? runs[move.actionNode][move.optionIndex ?? 0] : null);

  const stages = new Map();
  for (const node of story.stageNodes || []) {
    const controls = node.controlSettings || {};
    const wheel = runs[Object.keys(runs).find((action) => runs[action].includes(node.uuid))] || [node.uuid];
    stages.set(node.uuid, {
      id: node.uuid,
      name: node.name,
      image: bytesOf(byPath, node.image && `assets/${node.image}`),
      audio: bytesOf(byPath, node.audio && `assets/${node.audio}`),
      ok: target(node.okTransition),
      home: target(node.homeTransition),
      wheel,
      controls: {
        wheel: Boolean(controls.wheel),
        ok: Boolean(controls.ok),
        home: Boolean(controls.home),
        pause: Boolean(controls.pause),
        autoplay: Boolean(controls.autoplay),
      },
    });
  }

  const cover = (story.stageNodes || []).find((node) => node.squareOne) || (story.stageNodes || [])[0];
  if (!cover) throw new Error("story.json carries no stage");
  return { title: story.title || "", machine: "lunii", start: cover.uuid, stages };
}

/** The card's own records: fixed-size, with everything an index into a table. */
function readCard(files, byPath) {
  const ni = byPath.get("ni");
  const head = view(ni);
  const headerSize = head.getUint32(4, true);
  const nodeSize = head.getUint32(8, true);
  const count = head.getUint32(12, true);

  const stems = (name) => {
    const table = byPath.get(name);
    if (!table) return [];
    const text = decodeText(table);
    const out = [];
    for (let at = 0; at + MEDIA_RECORD <= text.length; at += MEDIA_RECORD) {
      out.push(text.slice(at + 4, at + MEDIA_RECORD));
    }
    return out;
  };
  const images = stems("ri.plain");
  const sounds = stems("si.plain");

  // The card names its media in upper case and stores it in lower.
  const lower = new Map([...byPath].map(([name, data]) => [name.toLowerCase(), data]));
  const media = (folder, extension, list, index) =>
    index === NO_MOVE ? null : lower.get(`${folder}/${list[index].toLowerCase()}.${extension}`) ?? null;

  const li = new Int32Array(view(byPath.get("li.plain")).byteLength / 4);
  const liView = view(byPath.get("li.plain"));
  for (let at = 0; at < li.length; at++) li[at] = liView.getInt32(4 * at, true);

  const records = [];
  for (let n = 0; n < count; n++) {
    const at = headerSize + n * nodeSize;
    const move = (offset) => {
      const base = head.getInt32(at + offset, true);
      const length = head.getInt32(at + offset + 4, true);
      const pick = head.getInt32(at + offset + 8, true);
      return base === NO_MOVE || li[base + pick] === undefined ? null : String(li[base + pick]);
    };
    records.push({
      image: head.getInt32(at, true),
      audio: head.getInt32(at + 4, true),
      ok: move(8),
      home: move(20),
      flags: FLAGS.map((offset) => head.getUint16(at + offset, true) !== 0),
    });
  }

  // A wheel scrolls one action's run of stages. The card does not name the run a
  // stage belongs to, but every run is the (base, length) pair some OK or home
  // points at, and the stage sits in one of them.
  const runs = new Map();
  const pairs = new Set();
  for (let n = 0; n < count; n++) {
    const at = headerSize + n * nodeSize;
    for (const offset of [8, 20]) {
      const base = head.getInt32(at + offset, true);
      const length = head.getInt32(at + offset + 4, true);
      if (base !== NO_MOVE && length > 0) pairs.add(`${base},${length}`);
    }
  }
  for (const pair of pairs) {
    const [base, length] = pair.split(",").map(Number);
    const run = [...li.slice(base, base + length)].map(String);
    for (const id of run) if (!runs.has(id)) runs.set(id, run);
  }

  const stages = new Map();
  records.forEach((record, n) => {
    const id = String(n);
    const [wheel, ok, home, pause, autoplay] = record.flags;
    stages.set(id, {
      id,
      image: media("rf/000", "bmp", images, record.image),
      audio: media("sf/000", "mp3", sounds, record.audio),
      ok: record.ok,
      home: record.home,
      wheel: runs.get(id) || [id],
      controls: { wheel, ok, home, pause, autoplay },
    });
  });

  if (!stages.size) throw new Error("the card holds no stage");
  const title = byPath.get("_metadata.json") ? JSON.parse(decodeText(byPath.get("_metadata.json"))).title : "";
  return { title: title || "", machine: "lunii", start: "0", stages };
}

/**
 * The neighbouring chapter, of the kind the player is on: a card steps to a card, a
 * chapter's clip to the next chapter's clip.
 *
 * The teller's graph has no wheel to scroll a run of stages, so its side buttons
 * need the book's own order to answer "which chapter is next".
 */
export function chapterStep(story, id, step) {
  const listed = story.chapters.includes(id) ? story.chapters : story.clips;
  const at = listed.indexOf(id);
  if (at < 0) return null;
  const next = at + step;
  return next >= 0 && next < listed.length ? listed[next] : null;
}

/**
 * Where a button leads. The machine refuses a button its stage switched off, and the
 * wheel stops at the ends of its run rather than wrapping round.
 *
 * `next` is not a button: it is the end of an autoplayed clip, which forces the
 * stage's OK transition whether or not the OK button itself is switched on.
 */
export function walk(story, id, button) {
  const stage = story.stages.get(id);
  if (!stage) return null;
  if (button === "next") return stage.ok;
  if (button === "ok") return stage.controls.ok ? stage.ok : null;
  if (button === "home") return stage.controls.home ? stage.home : null;
  if (button === "up" || button === "down") {
    const step = button === "down" ? 1 : -1;
    if (story.chapters.length) return chapterStep(story, id, step);
    if (!stage.controls.wheel) return null;
    const at = stage.wheel.indexOf(id);
    const next = at + step;
    return next >= 0 && next < stage.wheel.length ? stage.wheel[next] : null;
  }
  return null;
}

/**
 * The card's picture: a four-bits-per-pixel BMP. The packs write their rows top
 * first under a positive height, which is the other way round from what the BMP
 * header says; the card draws them that way, so the emulator does too.
 */
export function decodeBitmap(bytes) {
  const head = view(bytes);
  if (decodeText(bytes.subarray(0, 2)) !== "BM") throw new Error("not a bitmap");
  const offset = head.getUint32(10, true);
  const width = head.getInt32(18, true);
  const height = head.getInt32(22, true);
  const depth = head.getUint16(28, true);
  const compression = head.getUint32(30, true);
  const listed = head.getUint32(46, true);
  const colours = listed || 1 << depth;

  const palette = [];
  for (let n = 0; n < colours; n++) {
    const at = 14 + head.getUint32(14, true) + 4 * n;
    palette.push([bytes[at + 2], bytes[at + 1], bytes[at]]);
  }

  const rgba = new Uint8ClampedArray(width * height * 4);
  const put = (x, y, index) => {
    if (x < 0 || x >= width || y < 0 || y >= height) return;
    const [red, green, blue] = palette[index] || [0, 0, 0];
    const at = (y * width + x) * 4;
    rgba[at] = red;
    rgba[at + 1] = green;
    rgba[at + 2] = blue;
    rgba[at + 3] = 255;
  };

  if (compression === 2) {
    let at = offset;
    let x = 0;
    let y = 0;
    while (at + 1 < bytes.length) {
      const first = bytes[at++];
      const second = bytes[at++];
      if (first) {
        for (let n = 0; n < first; n++) put(x++, y, n % 2 ? second & 0x0f : second >> 4);
        continue;
      }
      if (second === 0) {
        x = 0;
        y += 1;
      } else if (second === 1) {
        break;
      } else if (second === 2) {
        x += bytes[at++];
        y += bytes[at++];
      } else if (second === 3) {
        break;
      } else {
        const whole = Math.ceil(second / 2);
        for (let n = 0; n < second; n++) {
          const byte = bytes[at + (n >> 1)];
          put(x++, y, n % 2 ? byte & 0x0f : byte >> 4);
        }
        at += whole + (whole % 2);
      }
    }
  } else if (compression === 0) {
    const perRow = Math.ceil((width * depth) / 8 / 4) * 4;
    for (let y = 0; y < height; y++) {
      const row = offset + y * perRow;
      for (let x = 0; x < width; x++) {
        if (depth === 24) {
          // No palette to look up: the pixel is already a colour, stored blue first.
          const at = row + 3 * x;
          const pixel = (y * width + x) * 4;
          rgba[pixel] = bytes[at + 2];
          rgba[pixel + 1] = bytes[at + 1];
          rgba[pixel + 2] = bytes[at];
          rgba[pixel + 3] = 255;
        } else if (depth === 8) {
          put(x, y, bytes[row + x]);
        } else if (depth === 4) {
          const byte = bytes[row + (x >> 1)];
          put(x, y, x % 2 ? byte & 0x0f : byte >> 4);
        } else {
          throw new Error(`a ${depth}-bit card picture is not one the emulator reads`);
        }
      }
    }
  } else {
    throw new Error(`bitmap compression ${compression} is not one the emulator reads`);
  }

  return { width, height, rgba };
}
