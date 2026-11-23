// The Lunii's sixteen grey levels: which shade lands where, and which pictures get flattened.

import assert from "node:assert/strict";
import { test } from "node:test";

import { DRAWS, FILLS, FLATTENS, StubCanvas, installCanvasStub } from "./canvas-stub.mjs";

const { GREY_LEVELS, cardPng, coverPng, drawPreview, greyLevel } = await import("../../web/cover.js");

// The desktop build's ffmpeg chain lands on the same sixteen: 255 in fifteen steps.
const LEVELS = Array.from({ length: GREY_LEVELS }, (_, level) => level * 17);

test("every brightness lands on one of the sixteen levels", () => {
  const levels = new Set();
  for (let value = 0; value <= 255; value++) levels.add(greyLevel(value));
  assert.deepEqual([...levels].sort((a, b) => a - b), LEVELS);
  assert.equal(greyLevel(0), 0);
  assert.equal(greyLevel(255), 255);
  // The card's white ground has to stay white: contrast 1.6 pushes it past the
  // ceiling, and the top level is that ceiling.
  assert.equal(greyLevel(240), 255);
  // The levels are set on the contrasted value, so the card's own shades move.
  assert.equal(greyLevel(128), 136);
});

test("a negative reads the other way round, on the same levels", () => {
  const levels = new Set();
  for (let value = 0; value <= 255; value++) levels.add(greyLevel(value, true));
  assert.deepEqual([...levels].sort((a, b) => a - b), LEVELS);
  assert.equal(greyLevel(255, true), 0);
  assert.equal(greyLevel(0, true), 255);
});

test("a Lunii card is flattened, a colour card is left alone", async () => {
  installCanvasStub();

  FLATTENS.length = 0;
  await cardPng("Chapitre 1/6", "il etait une fois", null, "center", 640, 480, "bw");
  assert.deepEqual(FLATTENS.map(([width, height]) => [width, height]), [[640, 480]]);
  assert.deepEqual(FLATTENS[0][2].sort((a, b) => a - b), LEVELS);
  assert.ok(FLATTENS[0][3][0] < FLATTENS[0][3][479], "the ramp starts dark and ends light");

  FLATTENS.length = 0;
  await cardPng("7", "il etait une fois", null, "center", 640, 480, "bw", true);
  assert.deepEqual(FLATTENS[0][2].sort((a, b) => a - b), LEVELS);
  assert.ok(FLATTENS[0][3][0] > FLATTENS[0][3][479], "a negative card starts light");

  FLATTENS.length = 0;
  await cardPng("Chapter 1/6", "once upon a time");
  assert.deepEqual(FLATTENS, [], "a Telmi card keeps its colour");
});

test("the crop preview is drawn in the card's own colours", async () => {
  installCanvasStub();
  const cover = { width: 600, height: 400, close: () => {} };
  const frame = (width, height) => new StubCanvas(width, height).getContext();

  FILLS.length = 0;
  DRAWS.length = 0;
  FLATTENS.length = 0;
  drawPreview(frame(160, 120), cover, 160, 120, { crop: "center" }, "colour");
  assert.deepEqual(FILLS, [[0, 0, 160, 120, 160, 120]], "the frame is painted over before the cover goes on it");
  assert.equal(DRAWS.length, 1, "and the cover is drawn on top of it");
  assert.deepEqual(FLATTENS, [], "a Telmi preview keeps the cover's colours");

  FLATTENS.length = 0;
  drawPreview(frame(160, 120), cover, 160, 120, { crop: "center" }, "bw");
  assert.deepEqual(FLATTENS.map(([width, height]) => [width, height]), [[160, 120]], "the frame is flattened");
  assert.ok(FLATTENS[0][3][0] < FLATTENS[0][3][119], "and it starts dark, like the card");

  FLATTENS.length = 0;
  drawPreview(frame(160, 120), cover, 160, 120, { crop: "center" }, "bw", true);
  assert.ok(FLATTENS[0][3][0] > FLATTENS[0][3][119], "a negative preview starts light");
});

test("the Lunii cover is flattened too", async () => {
  installCanvasStub();
  const cover = { width: 1000, height: 1500, close: () => {} };

  FLATTENS.length = 0;
  await coverPng(cover, 512, 512, "top", "bw");
  assert.deepEqual(FLATTENS.map(([width, height]) => [width, height]), [[512, 512]]);

  FLATTENS.length = 0;
  await coverPng(cover, 512, 512, "top");
  assert.deepEqual(FLATTENS, []);
});
