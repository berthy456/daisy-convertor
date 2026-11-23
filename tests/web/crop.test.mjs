// Which part of the cover a crop keeps: the three bands, a moved frame, a zoomed one.

import assert from "node:assert/strict";
import { test } from "node:test";

import { DRAWS, FILLS, TEXTS, installCanvasStub } from "./canvas-stub.mjs";

installCanvasStub();

const { CROP_MODES, IMAGE_SIZE, coverPng } = await import("../../web/cover.js");

// A tall cover: a centre-crop cuts its title off, which is the whole point of the choice.
const cover = { width: 1000, height: 1500, close: () => {} };

// drawImage(cover, x, y, width, height): the band is the y argument.
function drawn(view) {
  DRAWS.length = 0;
  return coverPng(cover, ...IMAGE_SIZE, view).then(() => DRAWS[0].args);
}

test("the three bands land where the cover says they do", async () => {
  // 640x480 from 1000x1500 scales by 0.64: 640 wide, 960 tall, so 480 px overflow.
  assert.equal((await drawn({ crop: "top" }))[2], 0);
  assert.equal((await drawn({ crop: "center" }))[2], -240);
  assert.equal((await drawn({ crop: "bottom" }))[2], -480);
  for (const mode of CROP_MODES) assert.equal((await drawn({ crop: mode }))[1], 0);
});

test("the default crop is the middle band", async () => {
  DRAWS.length = 0;
  await coverPng(cover, ...IMAGE_SIZE);
  assert.equal(DRAWS[0].args[2], -240);
});

test("a moved frame slides along the overflow it has", async () => {
  // The tall cover overflows a 640x480 frame by 480 px and not at all sideways.
  assert.equal((await drawn({ focus: [0, 0.25] }))[2], -120);
  assert.equal((await drawn({ focus: [1, 1] }))[2], -480, "the bottom edge of the cover");
  for (const x of [0, 1]) assert.equal((await drawn({ focus: [x, 0.5] }))[1], 0, "no sideways room to move in");
});

test("zooming enlarges the cover and opens room to move", async () => {
  const args = await drawn({ zoom: 2 });
  assert.equal(args[3], 1280, "twice the 640 px a plain crop needs");
  assert.equal(args[4], 1920);
  assert.equal(args[2], -720, "still centred, so twice the overflow");
  assert.equal(args[1], -320, "and room to move sideways, which a plain crop has none of");
  assert.equal((await drawn({ zoom: 2, focus: [0, 0] }))[1], 0, "the sideways move lands on the cover's left edge");
});

test("no zoom shows the whole cover, and the card fills the room it leaves", async () => {
  FILLS.length = 0;
  const args = await drawn({ zoom: 0 });
  // 640x480 from 1000x1500 fits by 0.32: 320 wide and 480 tall, so the sides stay empty.
  assert.equal(args[3], 320);
  assert.equal(args[4], 480);
  assert.equal(args[1], 160, "centred, so the cover starts inside the frame's left edge");
  assert.equal(args[2], 0);
  assert.deepEqual(FILLS.at(-1).slice(0, 4), [0, 0, 640, 480], "the empty room is filled before the cover lands");
});

test("a cover that fills the frame leaves no room to fill", async () => {
  FILLS.length = 0;
  await drawn({ zoom: 1 });
  assert.equal(FILLS.length, 0, "a plain crop draws the cover and nothing behind it");
});

test("the slider stops at its two ends", async () => {
  assert.equal((await drawn({ zoom: -1 }))[3], 320, "below the whole cover there is nowhere to go");
  assert.equal((await drawn({ zoom: 99 }))[3], 2560, "and past four times the frame the cover stops growing");
});

test("a colour card carries the whole cover, on the same band", async () => {
  const { cardPng } = await import("../../web/cover.js");
  DRAWS.length = 0;
  await cardPng("Chapter 1/6", "Once upon a time", cover, { crop: "bottom" });
  const card = DRAWS.find((draw) => draw.args[3] === 640).args;
  // 640x480 from 1000x1500 scales by 0.64: 640 wide, 960 tall, so the cover runs past the card.
  assert.equal(card[2], 480 - 960);
});

test("a smaller card shrinks its margins with its screen", async () => {
  const { cardPng } = await import("../../web/cover.js");
  TEXTS.length = 0;
  await cardPng("1/6", "Once upon a time", null, { crop: "center" }, 320, 240);
  const [number, words] = TEXTS;
  assert.equal(number[3], 320, "the number is drawn on the Lunii card");
  assert.equal(number[1], 320 - 12, "its margin shrinks with the card");
  assert.equal(words[1], 12);
});

test("a four-grey card fills its screen with the cover and carries its number alone", async () => {
  const { cardPng } = await import("../../web/cover.js");
  DRAWS.length = 0;
  TEXTS.length = 0;
  FILLS.length = 0;
  await cardPng("23", "Once upon a time", cover, { crop: "center" }, 320, 240, "bw");

  const full = DRAWS.find((draw) => draw.args[3] === 320);
  assert.ok(full, "the cover is drawn across the whole card, not in a corner");
  assert.ok(full.args[4] >= 240, "and it reaches the bottom of it");

  assert.equal(TEXTS.length, 1, "the card carries its number, and nothing else");
  const [number] = TEXTS;
  assert.equal(number[1], 160, "the number is centred across the screen");
  assert.ok(number[2] > 100 && number[2] < 140, "and down the middle of it");

  const strips = FILLS.filter(([, , width, height, card]) => card === 320 && width === 320 && height < 240);
  assert.equal(strips.length, 1, "one strip behind the number");
  assert.ok(strips[0][1] > 0, "which leaves the cover at the top of the screen");
  assert.ok(strips[0][1] + strips[0][3] < 240, "and at the bottom of it");
});

test("a Lunii number is sized as a three-digit one", async () => {
  const { cardPng } = await import("../../web/cover.js");
  const { fontSize } = await import("./canvas-stub.mjs");
  const sizeOf = async (label) => {
    TEXTS.length = 0;
    await cardPng(label, "", null, {}, 320, 240, "bw");
    return fontSize(TEXTS[0][5]);
  };

  const one = await sizeOf("7");
  const three = await sizeOf("888");
  assert.equal(one, three, "every card of a story carries a number of the same height");
  assert.ok(three > 150, "and three digits fill the width of the screen");
  assert.ok((await sizeOf("8888")) < three, "a fourth digit makes it give way");
});

test("a colour card keeps its corner number, its thumbnail and no strips", async () => {
  const { cardPng } = await import("../../web/cover.js");
  TEXTS.length = 0;
  FILLS.length = 0;
  await cardPng("1/6", "Once upon a time", cover, { crop: "center" }, 320, 240, "colour");
  assert.equal(TEXTS[0][1], 320 - 12, "the number stays in the corner");
  assert.equal(
    FILLS.filter(([, , width, height, card]) => card === 320 && width === 320 && height < 240).length,
    0,
    "a colour card draws no strip behind its lettering",
  );
});
