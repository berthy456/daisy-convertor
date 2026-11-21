// The Lunii export in the browser: the Telmi story walked back into a STUdio archive.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));

const { crc32, readZip } = await import("../../web/zip.js");
const { buildStoryFiles } = await import("../../web/convert.js");
const { rle4Bitmap } = await import("../../web/cover.js");
const { buildPlainPk, buildStudioFiles, buildStudioZip, packName } = await import("../../web/lunii.js");
const { installCanvasStub } = await import("./canvas-stub.mjs");

installCanvasStub();

const ASSET_NAME = /^[0-9A-F]{8}\.(png|mp3)$/;

const view = (bytes) => new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

/** `ri`/`si` as their twelve-character `000\XXXXXXXX` names. */
const records = (block) => new TextDecoder().decode(block).match(/.{12}/g) ?? [];

/** How many pixels an RLE4 bitmap's runs actually draw. */
function rle4Pixels(bmp) {
  let at = view(bmp).getUint32(10, true);
  let drawn = 0;
  while (at + 1 < bmp.length) {
    const count = bmp[at];
    const pair = bmp[at + 1];
    at += 2;
    if (count) drawn += count;
    else if (pair === 2) at += 2;
    else if (pair === 1) break;
    else if (pair >= 3) {
      // Absolute mode: `pair` pixels, stored two to the byte, padded to even.
      drawn += pair;
      const span = Math.floor((pair + 1) / 2);
      at += span + (span % 2);
    }
  }
  return drawn;
}

async function packEntries(book) {
  return new Map((await readZip(await buildPlainPk(book, { picture: rle4Bitmap }))).map((entry) => [entry.name, entry.data]));
}

const SILENCE = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));

async function story(cut = "chapter", options = {}) {
  const entries = await readZip(new Uint8Array(await readFile(join(ROOT, "gon-ruby-mp3.zip"))));
  return buildStoryFiles(entries, SILENCE, () => {}, { cut, ...options });
}

test("the archive unfolds the Telmi story into stages and actions", async () => {
  const book = await story();
  const { storyJson, assets } = await buildStudioFiles(book);

  assert.equal(storyJson.format, "v1");
  assert.equal(storyJson.title, "ごんぎつね");
  assert.equal(storyJson.stageNodes[0].type, "cover");
  assert.equal(storyJson.stageNodes[0].squareOne, true);
  assert.equal(storyJson.stageNodes.length, 1 + 2 * book.book.chapters.length);
  // Six chapters: one menu action per chapter plus one play action each.
  assert.equal(storyJson.actionNodes.length, 1 + book.book.chapters.length);

  const media = new Set(
    book.files
      .map((file) => file.name.replace(`${book.dirName}/`, ""))
      .filter((name) => name.startsWith("audios/") || name.startsWith("images/") || name === "title.mp3" || name === "title.png"),
  );
  assert.equal(assets.length, media.size);
  assert.ok(assets.every((asset) => ASSET_NAME.test(asset.name)), "every asset is named with eight hex characters");
});

test("the ids and asset names match the desktop converter", async () => {
  const book = await story();
  const { storyJson, assets } = await buildStudioFiles(book);
  const byName = new Map(storyJson.stageNodes.map((node) => [node.name, node]));

  // Computed from the Python module over the same story uuid.
  assert.equal(storyJson.stageNodes[0].uuid, "fe3ea2d2-4b0d-55eb-b18a-9a4d143ae318");
  assert.equal(byName.get("m0").uuid, "e8bf7734-b82a-5308-9773-c0f2f394790c");
  assert.equal(byName.get("s5").uuid, "9937b974-e586-5613-af97-a8c5f6607c31");

  const named = new Map(assets.map((asset) => [asset.name.slice(0, 8), asset.name]));
  assert.ok(named.has("059CBA91"), "audios/s0.mp3 keeps the name the desktop converter gives it");
  assert.ok(named.has("B6F7B7A8"), "images/m0.png likewise");
  assert.ok(named.has("D8BAA9AC"), "title.mp3 likewise");
});

test("a card stage keeps home as the way out of the story", async () => {
  const book = await story();
  const { storyJson } = await buildStudioFiles(book);
  const cards = storyJson.stageNodes.filter((node) => node.name.startsWith("m"));
  assert.ok(cards.every((node) => node.controlSettings.home && node.homeTransition === null));
  assert.ok(cards.every((node) => node.controlSettings.ok && node.okTransition !== null));
});

test("the wheel turns where a picture shows, and the pause where only sound plays", async () => {
  const book = await story();
  const { storyJson } = await buildStudioFiles(book);

  // Read off a published card: the wheel is switched on on the stages that show a
  // picture, so the chapter choices scroll, and pause on the stages that are
  // nothing but sound, so a chapter can be stopped.
  const cards = storyJson.stageNodes.filter((node) => node.name.startsWith("m"));
  const audio = storyJson.stageNodes.filter((node) => node.name.startsWith("s"));
  assert.ok(cards.length >= 2, "the story offers more than one chapter to scroll between");
  assert.ok(
    cards.every((node) => node.image !== null && node.controlSettings.wheel && !node.controlSettings.pause),
    "a chapter card scrolls to the next, and is not a clip to stop"
  );
  assert.ok(
    audio.every((node) => node.image === null && !node.controlSettings.wheel && node.controlSettings.pause),
    "a chapter's audio leaves the wheel still, and can be stopped"
  );
});

test("converting the same story twice writes the same archive", async () => {
  const book = await story();
  const first = await buildStudioZip(book);
  const second = await buildStudioZip(book);
  assert.equal(first.length, second.length);

  const entries = await readZip(first);
  assert.equal(entries[0].name, "story.json");
  assert.equal(entries[1].name, "thumbnail.png");
  assert.ok(entries.every((entry) => entry.name === "story.json" || entry.name === "thumbnail.png" || entry.name.startsWith("assets/")));
  const storyJson = JSON.parse(new TextDecoder().decode(entries[0].data));
  // The flag only says the story can be followed without touching the machine,
  // and a converted audiobook never asks for anything but the next chapter.
  assert.equal(storyJson.nightModeAvailable, true);
});

test("a story cut to sentences still folds into stages", async () => {
  const book = await story("sentence");
  const { storyJson, assets } = await buildStudioFiles(book);
  const parts = book.book.chapters.length;

  assert.equal(storyJson.stageNodes.length, 1 + 2 * parts);
  assert.equal(storyJson.actionNodes.length, 1 + parts);
  // A short part's card points at the part itself, so the archive holds it once.
  const audios = assets.filter((asset) => asset.name.endsWith(".mp3")).length;
  assert.ok(audios < 1 + 2 * parts);
});

// The `.plain.pk`: the same story in the layout the story card keeps on itself.
// The cards are spoken here, as the desktop run that wrote the pack below did.
const spoken = () => story("chapter", { cardAudio: async () => SILENCE });

test("the .pk holds the layout the card reads", async () => {
  const entries = await readZip(await buildPlainPk(await spoken(), { picture: rle4Bitmap }));
  const names = entries.map((entry) => entry.name);
  const table = (name) => records(entries.find((entry) => entry.name === name).data);

  assert.deepEqual(names.slice(0, 5), ["nm", "li.plain", "ni", "ri.plain", "si.plain"]);
  for (const [record, folder, extension] of [[table("ri.plain"), "rf", "bmp"], [table("si.plain"), "sf", "mp3"]]) {
    assert.ok(record.every((name) => /^000\\[0-9A-F]{8}$/.test(name)), `${folder} records are 000\\ plus eight hex`);
    assert.deepEqual(
      names.filter((name) => name.startsWith(`${folder}/`)),
      record.map((name) => `${folder}/000/${name.slice(4)}.${extension}`),
    );
  }
});

test("the .pk's tables are the desktop converter's", async () => {
  const entries = await packEntries(await spoken());

  // Read off the `.plain.pk` the desktop converter writes for this same pack.
  for (const [name, [length, sum]] of [
    ["ni", [1084, 0xc5b30aca]],
    ["li.plain", [48, 0x96d6a013]],
    ["ri.plain", [84, 0xcee966cb]],
    ["si.plain", [156, 0x5def39b4]],
  ]) {
    assert.equal(entries.get(name).length, length, `${name} length`);
    assert.equal(crc32(entries.get(name)), sum, name);
  }
});

test("the .pk's cards are the bitmaps the device draws", async () => {
  const entries = await readZip(await buildPlainPk(await spoken(), { picture: rle4Bitmap }));
  const cards = entries.filter((entry) => entry.name.startsWith("rf/"));
  assert.ok(cards.length >= 7);

  for (const card of cards) {
    const bmp = card.data;
    const header = view(bmp);
    assert.equal(String.fromCharCode(bmp[0], bmp[1]), "BM", card.name);
    assert.equal(header.getUint32(10, true), 118, `${card.name} starts its pixels after the palette`);
    assert.equal(header.getInt32(18, true), 320, card.name);
    assert.equal(header.getInt32(22, true), 240, card.name);
    assert.equal(header.getUint16(28, true), 4, `${card.name} is four bits a pixel`);
    assert.equal(header.getUint32(30, true), 2, `${card.name} is run-length encoded`);
    assert.equal(rle4Pixels(bmp), 320 * 240, `${card.name}'s runs draw the whole screen`);
  }
});

test("the .pk is named and listed as the published packs are", async () => {
  const book = await spoken();
  assert.equal(packName(book.metadata.title, book.metadata.uuid), "ごんぎつね.143AE318.plain.pk");

  const entries = await packEntries(book);
  assert.equal(entries.get("uuid.bin").length, 16);
  const listed = JSON.parse(new TextDecoder().decode(entries.get("_metadata.json")));
  assert.equal(listed.uuid, "FE3EA2D2-4B0D-55EB-B18A-9A4D143AE318");
  assert.equal(listed.title, "ごんぎつね");
  assert.ok(entries.has("_thumbnail.png"));
});
