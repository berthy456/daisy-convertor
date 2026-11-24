// The Merlin story-teller's SD card: playlist.bin and the files it names.
// Port of src/daisy2telmi/merlin.py.
//
// The card's root holds playlist.bin beside flat .mp3 and .jpg files: there is no
// folder tree, and a record's name field is the file without its extension. The
// file is a plain sequence of 152-byte records — no header, no count, no checksum
// — so it is written directly.
//
// djokeur/merlinator's write_merlin_playlist and gawen/pinpin's playlist.go agree
// on every byte; the kind values come from pinpin.

import { concatMp3 } from "./mp3.js";

export const RECORD_SIZE = 152;
export const NAME_FIELD = 64;
export const TITLE_FIELD = 66;

// The firmware reads a file name into a 59-byte buffer; a longer one overflows it
// and the device reboots, even though the record itself holds 64 bytes.
export const MAX_NAME = 57;

export const KIND_ROOT = 0x0001;
export const KIND_FOLDER = 0x0002;
export const KIND_AUDIO = 0x0004;
export const KIND_FOLDER_FAVOURITE = 0x000a;

export class MerlinError extends Error {}

export const IMPORT_NOTE = `Merlin story-teller

Copy every .mp3 and .jpg of this archive, and playlist.bin, into the root of the
micro-SD card. The card holds one flat folder: it has no sub-folders, and the
story's own folder is a record in playlist.bin, not a directory.

playlist.bin holds only this story, so the card's own content is no longer
listed. Keep the card's original playlist.bin to put it back.

Never delete sleep.cfg, wifi.cfg or the \`default\` folder: the device made them.

Each part begins with its title spoken, because the Merlin has no screen and a
menu's title is never read aloud: the record's title field is only ever seen by
the editor.

The sound keeps the rate the DAISY pack was recorded at. The card's own spec is
44100 Hz stereo at 128 kbit/s; a pack recorded elsewhere plays at its own rate,
which the desktop app re-encodes.
`;

/**
 * The title as the record can hold it.
 *
 * A DAISY or epub heading runs past the 66 bytes the card's title field has — a WIPO
 * article name or a subtitle does it on its own — and the card shows only what fits
 * anyway. The words past that are dropped here, at a character, with the ellipsis the
 * screen would have put there.
 */
export function fitTitle(title) {
  const text = title.normalize("NFC");
  const raw = new TextEncoder().encode(text);
  if (raw.length <= TITLE_FIELD) return text;
  // A cut in the middle of a character decodes to a replacement sign: drop it.
  const kept = new TextDecoder().decode(raw.subarray(0, TITLE_FIELD - 3)).replace(/�+$/, "");
  return `${kept.trimEnd()}…`;
}

function field(text, size, form, what) {
  const raw = new TextEncoder().encode(text.normalize(form));
  if (raw.length > size) {
    throw new MerlinError(`the ${what} ${JSON.stringify(text)} does not fit in ${size} bytes`);
  }
  const block = new Uint8Array(size + 1);
  block[0] = raw.length;
  block.set(raw, 1);
  return block;
}

/**
 * One record: an entry in the card's tree, and the file it names.
 * Names are NFD and titles NFC because that is how the card's FAT32 and the
 * device's font each happen to store them.
 */
export function pack(item) {
  const record = new Uint8Array(RECORD_SIZE);
  const view = new DataView(record.buffer);
  view.setUint16(0, item.id, true);
  view.setUint16(2, item.parent, true);
  view.setUint16(4, item.order, true);
  view.setUint16(6, item.children ?? 0, true);
  view.setUint16(8, item.favOrder ?? 0, true);
  view.setUint16(10, item.kind, true);
  view.setUint32(12, item.limitTime ?? 0, true);
  view.setUint32(16, item.addTime ?? 0, true);
  record.set(field(item.name ?? "", NAME_FIELD, "NFD", "file name"), 20);
  record.set(field(item.title ?? "", TITLE_FIELD, "NFC", "title"), 20 + NAME_FIELD + 1);
  return record;
}

export function unpack(record) {
  if (record.length !== RECORD_SIZE) {
    throw new MerlinError(`a record is ${RECORD_SIZE} bytes, not ${record.length}`);
  }
  const view = new DataView(record.buffer, record.byteOffset ?? 0, record.byteLength);
  return {
    id: view.getUint16(0, true),
    parent: view.getUint16(2, true),
    order: view.getUint16(4, true),
    children: view.getUint16(6, true),
    favOrder: view.getUint16(8, true),
    kind: view.getUint16(10, true),
    limitTime: view.getUint32(12, true),
    addTime: view.getUint32(16, true),
    name: readField(record, 20, NAME_FIELD, "file name"),
    title: readField(record, 20 + NAME_FIELD + 1, TITLE_FIELD, "title"),
  };
}

function readField(record, at, size, what) {
  const length = record[at];
  if (length > size) throw new MerlinError(`the ${what} claims ${length} bytes in a ${size} byte field`);
  return new TextDecoder().decode(record.subarray(at + 1, at + 1 + length));
}

export function readPlaylist(bytes) {
  if (bytes.length % RECORD_SIZE) {
    throw new MerlinError(`a playlist holds ${bytes.length} bytes, which is not a whole number of records`);
  }
  return Array.from({ length: bytes.length / RECORD_SIZE }, (_, index) =>
    unpack(bytes.subarray(index * RECORD_SIZE, (index + 1) * RECORD_SIZE)),
  );
}

export function writePlaylist(items) {
  const bytes = new Uint8Array(items.length * RECORD_SIZE);
  items.forEach((item, index) => bytes.set(pack(item), index * RECORD_SIZE));
  return bytes;
}

/**
 * A card file name: the factory's own shape, never the title.
 * The title belongs to the record; naming a file after it would put the book's
 * name on the card and burn it into every later edit.
 */
export function fileName() {
  return crypto.randomUUID();
}

/** Records being built, with the ids handed out as they are added. */
export class Playlist {
  constructor(items = []) {
    this.items = [...items];
    this.next = Math.max(0, ...this.items.map((item) => item.id)) + 1;
  }

  root() {
    return this.items.find((item) => item.kind === KIND_ROOT) ?? null;
  }

  add(parent, kind, title, name = "") {
    const item = {
      id: this.next,
      parent,
      order: this.items.filter((other) => other.parent === parent).length,
      kind,
      name,
      title: fitTitle(title),
      children: 0,
      favOrder: 0,
      limitTime: 0,
      addTime: 0,
    };
    this.next += 1;
    this.items.push(item);
    return item;
  }

  // A folder is named like a file as well: the card looks its picture up by that
  // name, and merlinator gives a new menu a uuid for the same reason.
  folder(parent, title) {
    return this.add(parent, KIND_FOLDER, title, fileName());
  }

  audio(parent, title, name) {
    if (name.length > MAX_NAME) throw new MerlinError(`the file name ${JSON.stringify(name)} is over ${MAX_NAME} characters`);
    return this.add(parent, KIND_AUDIO, title, name);
  }

  // The card walks a folder by reading `children` entries after it, so the count
  // is written last, once the whole tree is there.
  bytes() {
    for (const item of this.items) item.children = this.items.filter((other) => other.parent === item.id).length;
    return writePlaylist(this.items);
  }
}

/**
 * The story's parts in the order the player walks them: each card stage with the
 * scene stage its OK leads to.
 */
export function storyParts(nodes) {
  const stages = nodes.stages ?? {};
  const found = [];
  for (let index = 0; index < Object.keys(stages).length; index++) {
    const card = stages[`m${index}`];
    if (!card) break;
    const scene = stages[(card.ok ?? {}).action ?? ""];
    found.push([card, scene && scene.audio ? scene : null]);
  }
  return found;
}

/**
 * One finished Telmi story as the files a Merlin card holds.
 *
 * story: what buildStoryFiles returns — {dirName, files, metadata, nodes, book}.
 * titles: names each part, in order; the record carries it, and the spoken card
 * at the front of the audio is the only place the device can say it.
 * picture(bytes): a story PNG as the card's 128 px square JPEG.
 */
export async function buildMerlinFiles(story, { titles, picture, addTime = Math.floor(Date.now() / 1000) } = {}) {
  const { dirName, metadata, nodes, book } = story;
  const parts = storyParts(nodes);
  const names = titles ?? (book?.chapters ?? []).map((chapter) => chapter.title);
  if (parts.length !== names.length) {
    throw new MerlinError(`the story has ${parts.length} part(s), but ${names.length} title(s) were given`);
  }

  const media = new Map();
  for (const file of story.files) {
    if (file.name.startsWith(`${dirName}/`)) media.set(file.name.slice(dirName.length + 1), file.data);
  }

  const card = new Playlist();
  const root = card.add(0, KIND_ROOT, "Root");
  const bookFolder = card.folder(root.id, metadata.title || dirName);
  bookFolder.addTime = addTime;

  const entries = [];
  const cover = media.get(metadata.image || "cover.png");
  if (cover) entries.push({ name: `${bookFolder.name}.jpg`, data: await picture(cover) });

  for (const [index, [cardStage, sceneStage]] of parts.entries()) {
    const name = fileName();
    const clips = [cardStage, sceneStage]
      .filter((stage) => stage?.audio)
      .map((stage) => media.get(`audios/${stage.audio}`));
    entries.push({ name: `${name}.mp3`, data: concatMp3(clips) });
    const image = cardStage.image && media.get(`images/${cardStage.image}`);
    if (image) entries.push({ name: `${name}.jpg`, data: await picture(image) });
    card.audio(bookFolder.id, String(names[index]), name).addTime = addTime;
  }

  entries.push({ name: "playlist.bin", data: card.bytes() });
  entries.push({ name: "IMPORT.txt", data: new TextEncoder().encode(IMPORT_NOTE) });
  return entries;
}
