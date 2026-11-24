// The emulator: the three archives this tool writes read back as one story, and the
// machine's buttons walk it.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));

const { readZip } = await import("../../web/zip.js");
const { buildStoryFiles, buildStoryZip } = await import("../../web/convert.js");
const { buildStudioZip, buildPlainPk } = await import("../../web/lunii.js");
const { rle4Bitmap } = await import("../../web/cover.js");
const { chapterStep, decodeBitmap, formatOf, readStory, walk } = await import("../../web/emulator.js");
const { installCanvasStub } = await import("./canvas-stub.mjs");

installCanvasStub();

const SILENCE = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
const book = async () => readZip(new Uint8Array(await readFile(join(ROOT, "gon-ruby-mp3.zip"))));

// The card's chapters are spoken, as the desktop run that writes a card does.
const spoken = { cardAudio: async () => SILENCE };

const converted = async (options = {}) => buildStoryFiles(await book(), SILENCE, () => {}, { cut: "chapter", ...options });
const storyOf = async (bytes) => readStory(await readZip(bytes));
const telmiStory = async () => storyOf((await buildStoryZip(await book(), SILENCE, () => {}, { cut: "chapter" })).zip);
const luniiStory = async () => storyOf(await buildStudioZip(await converted()));
const cardStory = async () => storyOf(await buildPlainPk(await converted(spoken), { picture: rle4Bitmap }));

/** The stage of a story by the name the converter gave it. */
const named = (story, name) => [...story.stages.values()].find((stage) => stage.name === name);

test("each archive says which machine it was written for", async () => {
  const telmi = (await buildStoryZip(await book(), SILENCE, () => {}, { cut: "chapter" })).zip;
  assert.equal(formatOf(await readZip(telmi)), "telmi");
  assert.equal(formatOf(await readZip(await buildStudioZip(await converted()))), "lunii");
  assert.equal(formatOf(await readZip(await buildPlainPk(await converted(spoken), { picture: rle4Bitmap }))), "pk");
});

test("an archive this tool did not write is refused", async () => {
  await assert.rejects(async () => readStory(await book()), /no nodes\.json, story\.json or ni/);
});

test("the Telmi story walks chapter by chapter", async () => {
  const story = await telmiStory();

  assert.equal(story.title, "ごんぎつね");
  assert.equal(story.machine, "telmi");
  assert.equal(story.stages.size, 12, "six chapter cards and six chapters of audio");
  assert.equal(story.start, "m0");

  assert.equal(walk(story, "m0", "ok"), "s0", "OK on a chapter card starts the chapter");
  assert.equal(walk(story, "s0", "home"), "m0", "home goes back to the card the chapter started on");
  assert.equal(walk(story, "s5", "next"), null, "the last chapter's audio has nowhere to go on to");
});

test("the Telmi's audio carries itself on, and its buttons do nothing else", async () => {
  const story = await telmiStory();

  assert.equal(story.stages.get("s0").controls.autoplay, true);
  assert.equal(walk(story, "s0", "ok"), null, "the OK button is switched off while the chapter plays");
  assert.equal(walk(story, "s0", "next"), "m1", "the end of the clip takes the OK transition anyway");
  assert.equal(walk(story, "m0", "home"), null, "the first card has nowhere behind it");
});

test("the Telmi's side keys step from one chapter to the next", async () => {
  const story = await telmiStory();

  assert.deepEqual(story.chapters, ["m0", "m1", "m2", "m3", "m4", "m5"], "the cards, in the order the book tells them");
  assert.deepEqual(story.clips, ["s0", "s1", "s2", "s3", "s4", "s5"], "and each card's own clip");

  assert.equal(walk(story, "m0", "down"), "m1", "a card steps to the next card");
  assert.equal(walk(story, "m2", "up"), "m1", "and back to the one before");
  assert.equal(walk(story, "s2", "down"), "s3", "a chapter steps to the next chapter's clip");
  assert.equal(walk(story, "s2", "up"), "s1");
  assert.equal(walk(story, "m0", "up"), null, "the first chapter has none behind it");
  assert.equal(walk(story, "m5", "down"), null, "and the last has none ahead");
  assert.equal(chapterStep(story, "m3", 1), "m4");
  assert.equal(chapterStep(story, "m3", -1), "m2");
});

test("the Lunii story starts on its cover and the wheel scrolls its chapter cards", async () => {
  const story = await luniiStory();

  assert.equal(story.machine, "lunii");
  assert.equal(story.stages.size, 13, "the cover, then a card and a chapter for each part");
  assert.equal(story.stages.get(story.start).name, "ごんぎつね", "the cover is the story's own title");
  assert.equal(story.stages.get(story.start).image.byteLength > 0, true, "the cover shows a picture");

  const cards = ["m0", "m1", "m2"].map((name) => named(story, name));
  assert.equal(walk(story, story.start, "ok"), cards[0].id, "OK on the cover opens the first chapter card");
  assert.equal(walk(story, cards[0].id, "down"), cards[1].id);
  assert.equal(walk(story, cards[1].id, "down"), cards[2].id);
  assert.equal(walk(story, cards[0].id, "up"), null, "the wheel stops at the first card");

  const last = named(story, "m5");
  assert.equal(walk(story, last.id, "down"), null, "and at the last one");
  assert.equal(walk(story, last.id, "up"), named(story, "m4").id);
});

test("the Lunii story's audio leaves the wheel still and answers home", async () => {
  const story = await luniiStory();
  const card = named(story, "m2");
  const chapter = named(story, "s2");

  assert.equal(walk(story, card.id, "ok"), chapter.id);
  assert.equal(chapter.controls.wheel, false, "a chapter's audio is not one of the choices to scroll");
  assert.equal(walk(story, chapter.id, "down"), null);
  assert.equal(walk(story, chapter.id, "home"), card.id);
});

test("the card's own records read back as the same walk", async () => {
  const story = await cardStory();

  assert.equal(story.machine, "lunii");
  assert.equal(story.title, "ごんぎつね");
  assert.equal(story.stages.size, 13);
  assert.equal(story.start, "0", "the card starts on its first record");

  assert.equal(walk(story, "0", "ok"), "1", "the cover opens the first chapter card");
  assert.equal(walk(story, "1", "ok"), "2", "the card starts its chapter");
  assert.equal(walk(story, "2", "next"), "3", "and the chapter ends on the next card");
  assert.equal(walk(story, "2", "home"), "1", "home goes back to the card");
  assert.equal(walk(story, "1", "down"), "3", "the wheel moves to the next chapter card");
  assert.equal(walk(story, "11", "down"), null, "the last chapter card is the end of the wheel");

  const pictures = [...story.stages.values()].filter((stage) => stage.image !== null);
  const sounds = [...story.stages.values()].filter((stage) => stage.audio !== null);
  assert.equal(pictures.length, 7, "the cover and the six chapter cards carry a picture");
  assert.equal(sounds.length, 13, "and the cover, the six cards and the six chapters carry audio");
});

test("the card's picture decodes to the screen it is drawn on", async () => {
  const story = await cardStory();
  const picture = decodeBitmap([...story.stages.values()].find((stage) => stage.image !== null).image);

  assert.deepEqual({ width: picture.width, height: picture.height }, { width: 320, height: 240 });
  assert.equal(picture.rgba.length, 320 * 240 * 4);
  assert.ok(picture.rgba.every((_, at) => at % 4 !== 3 || picture.rgba[at] === 255), "every pixel is painted");
});

/** A four-bits-a-pixel bitmap of the card's own shape, holding one known run each. */
function bitmap(rows) {
  const data = new Uint8Array(rows.flat());
  const file = new Uint8Array(118 + data.length);
  const head = new DataView(file.buffer);
  file.set([0x42, 0x4d]);
  head.setUint32(2, file.length, true);
  head.setUint32(10, 118, true);
  head.setUint32(14, 40, true);
  head.setInt32(18, 4, true);
  head.setInt32(22, 2, true);
  head.setUint16(26, 1, true);
  head.setUint16(28, 4, true);
  head.setUint32(30, 2, true);
  // Black, red, lime, blue: the palette is stored blue first.
  for (const [index, rgb] of [[0, [0, 0, 0]], [1, [0, 0, 255]], [2, [0, 255, 0]], [3, [255, 0, 0]]]) {
    head.setUint8(54 + 4 * index, rgb[2]);
    head.setUint8(55 + 4 * index, rgb[1]);
    head.setUint8(56 + 4 * index, rgb[0]);
  }
  file.set(data, 118);
  return file;
}

test("the run-length codes decode pixel by pixel", () => {
  const picture = decodeBitmap(
    bitmap([
      // Four pixels from one byte: the high nibble first.
      [0x04, 0x12, 0x00, 0x00],
      // Skip two pixels, draw two, and leave the row.
      [0x00, 0x02, 0x02, 0x00, 0x02, 0x30, 0x00, 0x01],
    ]),
  );

  assert.deepEqual({ width: picture.width, height: picture.height }, { width: 4, height: 2 });
  const pixel = (x, y) => [...picture.rgba.subarray((y * 4 + x) * 4, (y * 4 + x) * 4 + 4)];
  assert.deepEqual(pixel(0, 0), [0, 0, 255, 255]);
  assert.deepEqual(pixel(1, 0), [0, 255, 0, 255]);
  assert.deepEqual(pixel(2, 0), [0, 0, 255, 255]);
  assert.deepEqual(pixel(3, 0), [0, 255, 0, 255]);
  assert.deepEqual(pixel(0, 1), [0, 0, 0, 0], "the skipped pixels stay empty");
  assert.deepEqual(pixel(2, 1), [255, 0, 0, 255]);
  assert.deepEqual(pixel(3, 1), [0, 0, 0, 255]);
});
