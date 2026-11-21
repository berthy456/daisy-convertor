// The recorded card announcements the page ships, and how it picks them.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const { MAX_CARD, cardAudio, clipPath } = await import("../../web/voices.js");
const { LANGUAGES } = await import("../../web/i18n.js");

test("a chapter number becomes a two digit clip name", () => {
  assert.equal(clipPath("fr", 1), "./voices/fr/01.mp3");
  assert.equal(clipPath("zh", MAX_CARD), `./voices/zh/${String(MAX_CARD).padStart(2, "0")}.mp3`);
});

test("a number outside the recorded range has no clip", () => {
  for (const bad of [0, -1, MAX_CARD + 1, 2.5, NaN, undefined, "3"]) {
    assert.equal(clipPath("en", bad), null);
  }
});

test("a clip the page cannot fetch leaves the card silent", async () => {
  assert.equal(await cardAudio("en", 3, {}, async () => null), null);
});

test("the prompt joins the question after the number", async () => {
  const silence = new Uint8Array(await readFile("web/silence.mp3"));
  const asked = [];
  const joined = await cardAudio("fr", 4, { prompt: true }, async (path) => {
    asked.push(path);
    return silence;
  });
  assert.deepEqual(asked, ["./voices/fr/04.mp3", "./voices/fr/next.mp3"]);
  assert.ok(joined.length > 0 && joined.length <= silence.length * 2);
});

test("a card that does not say the word chapter takes the number alone", async () => {
  const asked = [];
  await cardAudio("fr", 4, { word: false }, async (path) => {
    asked.push(path);
    return null;
  });
  assert.deepEqual(asked, ["./voices/fr/of-pre.mp3", "./voices/fr/n-04.mp3"]);
});

test("a language that puts its word before the number keeps it", async () => {
  const asked = [];
  await cardAudio("ar", 4, { word: false }, async (path) => {
    asked.push(path);
    return null;
  });
  assert.deepEqual(asked, ["./voices/ar/of-pre.mp3", "./voices/ar/n-04.mp3"]);
});

test("the total goes between the words the language puts around it", async () => {
  const silence = new Uint8Array(await readFile("web/silence.mp3"));
  const asked = [];
  await cardAudio("fr", 4, { count: 12 }, async (path) => {
    asked.push(path);
    return path.endsWith("of-post.mp3") ? null : silence;
  });
  assert.deepEqual(asked, [
    "./voices/fr/04.mp3",
    "./voices/fr/of-mid.mp3",
    "./voices/fr/n-12.mp3",
    "./voices/fr/of-post.mp3",
  ]);
});

test("a total beyond the recorded range is left out rather than half said", async () => {
  const asked = [];
  await cardAudio("en", 4, { count: MAX_CARD + 1 }, async (path) => {
    asked.push(path);
    return path.endsWith("04.mp3") ? new Uint8Array(await readFile("web/silence.mp3")) : null;
  });
  assert.deepEqual(asked, ["./voices/en/04.mp3"]);
});

test("every language ships its numbers, its total and its prompt", async () => {
  for (const code of LANGUAGES) {
    for (const name of [
      "01.mp3",
      "07.mp3",
      `${String(MAX_CARD).padStart(2, "0")}.mp3`,
      "n-01.mp3",
      "n-07.mp3",
      `n-${String(MAX_CARD).padStart(2, "0")}.mp3`,
      "next.mp3",
    ]) {
      const bytes = await readFile(`web/voices/${code}/${name}`);
      const tagged = bytes[0] === 0x49 && bytes[1] === 0x44 && bytes[2] === 0x33;
      const frame = (bytes[0] & 0xe0) === 0xe0;
      assert.ok(tagged || frame, `web/voices/${code}/${name} is not an MP3`);
    }
  }
});
