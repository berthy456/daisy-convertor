// The Merlin card export in the browser. Every record is checked against the bytes
// the Python build writes, because the card's decoder reads them position by
// position and nothing in the file says where anything is.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));

const { readZip } = await import("../../web/zip.js");
const { buildStoryFiles } = await import("../../web/convert.js");
const { squareJpeg } = await import("../../web/cover.js");
const { mp3Frames } = await import("../../web/mp3.js");
const {
  KIND_AUDIO,
  KIND_FOLDER,
  KIND_ROOT,
  MAX_NAME,
  MerlinError,
  Playlist,
  RECORD_SIZE,
  TITLE_FIELD,
  buildMerlinFiles,
  fileName,
  fitTitle,
  pack,
  readPlaylist,
  storyParts,
  unpack,
} = await import("../../web/merlin.js");
const { BITMAP, installCanvasStub } = await import("./canvas-stub.mjs");

installCanvasStub();

// Written by src/daisy2telmi/merlin.py's pack(); tests/test_merlin.py checks the
// file still matches, so neither build can drift without the other noticing.
const RECORDS = JSON.parse(await readFile(join(ROOT, "tests/web/fixtures/merlin-records.json"), "utf8"));

// The fixture keeps the Python field names, because Python wrote it.
const RENAMED = { fav_order: "favOrder", add_time: "addTime", limit_time: "limitTime" };
const asItem = (entry) =>
  Object.fromEntries(
    Object.entries(entry)
      .filter(([key]) => key !== "record")
      .map(([key, value]) => [RENAMED[key] ?? key, value]),
  );

const hex = (bytes) => [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");

async function story(cut = "chapter") {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const entries = await readZip(new Uint8Array(await readFile(join(ROOT, "gon-ruby-mp3.zip"))));
  // A card that speaks: the page only writes a card's sound when it has a voice for it.
  return buildStoryFiles(entries, silent, () => {}, { cut, cardAudio: async () => silent });
}

test("a heading longer than the record is shortened the same way the app shortens it", () => {
  const bytes = (text) => new TextEncoder().encode(text).length;
  const cases = [
    "Article 9. Notice to Right Owners for Commercial Reproduction of Works",
    "é".repeat(40),
    "3.5 Haüy's meeting with Lesueur and the founding of l'Institution des jeunes sourds",
  ];
  for (const heading of cases) {
    const fitted = fitTitle(heading);
    assert.ok(bytes(fitted) <= TITLE_FIELD, `${heading}: the field holds it`);
    assert.ok(!fitted.includes("�"), `${heading}: the cut lands on a character, not inside one`);
  }
  assert.equal(fitTitle(cases[0]), "Article 9. Notice to Right Owners for Commercial Reproduction o…");
  assert.equal(fitTitle(cases[1]), `${"é".repeat(31)}…`);
  assert.equal(fitTitle("Premier chapitre"), "Premier chapitre");
});

test("a record is the Python build's, byte for byte", () => {
  for (const [what, entry] of Object.entries(RECORDS)) {
    assert.equal(entry.record.length, RECORD_SIZE * 2, `${what}: the fixture holds a whole record`);
    const record = pack(asItem(entry));
    assert.equal(record.length, RECORD_SIZE);
    assert.equal(hex(record), entry.record, what);
  }
});

test("a record survives the round trip", () => {
  const item = {
    id: 12,
    parent: 3,
    order: 4,
    children: 2,
    favOrder: 1,
    kind: KIND_AUDIO,
    name: fileName(),
    title: "Le loup et les sept chevreaux",
    limitTime: 0,
    addTime: 1_700_000_000,
  };
  assert.deepEqual(unpack(pack(item)), item);
});

test("a file name is read back the way the card's FAT32 stores it", () => {
  const read = unpack(pack({ id: 1, parent: 0, order: 0, kind: KIND_AUDIO, name: "Épée", title: "Épée" }));
  assert.equal(read.name.normalize("NFD"), read.name, "the file name is decomposed");
  assert.equal(read.title.normalize("NFC"), read.title, "the title is composed");
  assert.notEqual(read.name, read.title, "so the two are not the same bytes");
});

test("a name the firmware would choke on is refused", () => {
  const card = new Playlist();
  card.add(0, KIND_ROOT, "Root");
  assert.throws(() => card.audio(1, "Le loup", "x".repeat(MAX_NAME + 1)), MerlinError);
});

test("a title longer than its field is refused", () => {
  assert.throws(() => pack({ id: 1, parent: 0, order: 0, kind: KIND_AUDIO, title: "é".repeat(40) }), MerlinError);
});

test("a card built from scratch has a root and its story under it", () => {
  const card = new Playlist();
  const root = card.add(0, KIND_ROOT, "Root");
  const book = card.folder(root.id, "Le loup et les sept chevreaux");
  for (const chapter of ["Premier chapitre", "Second chapitre"]) card.audio(book.id, chapter, fileName());

  assert.deepEqual(card.items.map((item) => item.kind), [KIND_ROOT, KIND_FOLDER, KIND_AUDIO, KIND_AUDIO]);
  assert.deepEqual(card.items.slice(2).map((item) => item.parent), [book.id, book.id]);
  assert.deepEqual(card.items.map((item) => item.order), [0, 0, 0, 1], "the chapters keep their order");
  assert.deepEqual(
    readPlaylist(card.bytes()).map((item) => item.children),
    [1, 2, 0, 0],
    "the card walks a folder by its children count",
  );
});

test("a card file name is the factory shape", () => {
  const name = fileName();
  assert.equal(name.length, 36);
  assert.equal((name.match(/-/g) ?? []).length, 4);
});

test("the parts are the card stage and the scene its OK leads to", async () => {
  const book = await story();
  const parts = storyParts(book.nodes);
  assert.equal(parts.length, book.book.chapters.length);
  for (const [index, [card, scene]] of parts.entries()) {
    assert.equal(card.audio, `c${index}.mp3`, "the spoken number comes first");
    assert.equal(scene.audio, `s${index}.mp3`);
  }
});

test("the card holds one sound and one picture per part, and the playlist", async () => {
  BITMAP.width = 640;
  BITMAP.height = 480;
  const book = await story();
  const entries = await buildMerlinFiles(book, { picture: squareJpeg, addTime: 1_700_000_000 });

  const names = entries.map((entry) => entry.name);
  assert.equal(names.filter((name) => name.endsWith(".mp3")).length, book.book.chapters.length);
  assert.equal(
    names.filter((name) => name.endsWith(".jpg")).length,
    book.book.chapters.length + 1,
    "the story's own picture as well",
  );
  assert.ok(names.includes("playlist.bin") && names.includes("IMPORT.txt"));

  const stems = names.filter((name) => name.endsWith(".mp3")).map((name) => name.slice(0, -4));
  assert.equal(new Set(stems).size, stems.length, "no two parts share a name");
  assert.ok(stems.every((stem) => stem.length <= MAX_NAME), "every name fits the firmware's buffer");
  assert.ok(
    stems.every((stem) => names.includes(`${stem}.jpg`)),
    "a part's sound and picture carry one name, which is what the record names",
  );

  const records = readPlaylist(entries.find((entry) => entry.name === "playlist.bin").data);
  assert.deepEqual(records.map((record) => record.kind), [
    KIND_ROOT,
    KIND_FOLDER,
    ...book.book.chapters.map(() => KIND_AUDIO),
  ]);
  assert.deepEqual(records.slice(2).map((record) => record.title), book.book.chapters.map((chapter) => chapter.title));
  const files = new Set(names);
  for (const record of records.slice(2)) {
    assert.ok(files.has(`${record.name}.mp3`), "every audio record names a file the archive holds");
  }
  assert.ok(files.has(`${records[1].name}.jpg`), "the story's folder names its own picture");
  assert.ok(records.slice(1).every((record) => record.addTime === 1_700_000_000));
});

test("a part's sound is its spoken card first, then its text", async () => {
  const book = await story();
  const [entry] = (await buildMerlinFiles(book, { picture: squareJpeg })).filter((file) => file.name.endsWith(".mp3"));
  const media = new Map(book.files.map((file) => [file.name.replace(`${book.dirName}/`, ""), file.data]));
  const card = mp3Frames(media.get("audios/c0.mp3"));
  const scene = mp3Frames(media.get("audios/s0.mp3"));

  assert.equal(entry.data.length, card.length + scene.length, "joined, not re-encoded");
  assert.deepEqual([...entry.data.subarray(0, card.length)], [...card], "the card is heard before the text");
});
