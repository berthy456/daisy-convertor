// Which part of the cover a crop keeps: the three bands, a moved frame, a zoomed one.

import assert from "node:assert/strict";
import { test } from "node:test";

import { DRAWS, FILLS, TEXTS, StubCanvas, installCanvasStub } from "./canvas-stub.mjs";

installCanvasStub();

const { CAPTION_MIN_SIZE, CROP_MODES, IMAGE_SIZE, captionFit, captionLines, captionPng, coverPng, linePitch } =
  await import("../../web/cover.js");

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

test("a card writes its chapter title as a line of its own", async () => {
  const { cardPng } = await import("../../web/cover.js");
  const { fontSize } = await import("./canvas-stub.mjs");
  TEXTS.length = 0;
  await cardPng("Chapter 1/6", "Once upon a time", null, {}, 640, 480, "colour", false, "The invisible dog");
  const [number, title] = TEXTS;
  assert.equal(number[0], "Chapter 1/6");
  assert.equal(title[0], "The invisible dog", "the title is written on its own, not after the number");
  assert.equal(fontSize(number[5]), 44, "the number keeps the size a bare one has");
  assert.equal(fontSize(title[5]), 28, "the title is lettered at the size of the card's own words");
});

test("a title the card has no room for is left off", async () => {
  const { cardPng } = await import("../../web/cover.js");
  TEXTS.length = 0;
  await cardPng("Chapter 1/6", "", null, {}, 640, 480, "colour", false, "the invisible dog ".repeat(14));
  assert.equal(TEXTS.length, 1, "the card keeps its number alone");
});

test("the two card lines take two different places", async () => {
  const { cardPlaces } = await import("../../web/cover.js");
  assert.deepEqual(cardPlaces("bottom_left", "top_right"), ["bottom_left", "top_right"]);
  assert.deepEqual(cardPlaces("top_right", "top_right"), ["top_right", "top_left"]);
  assert.deepEqual(cardPlaces("top_left", "top_left"), ["top_left", "top_centre"]);
});

test("a card letters its number where it is told", async () => {
  const { cardPng } = await import("../../web/cover.js");
  TEXTS.length = 0;
  await cardPng("1/6", "", null, {}, 640, 480, "colour", false, "", 1, "bottom_right");
  const [number] = TEXTS;
  assert.equal(number[1], 640 - 24, "the number stands at the card's right edge");
  assert.equal(number[2], 480 - 24, "and at its bottom");
});

test("two lines on one band keep their own room", async () => {
  const { cardPng } = await import("../../web/cover.js");
  const { fontSize } = await import("./canvas-stub.mjs");
  TEXTS.length = 0;
  await cardPng("3/12", "", null, {}, 640, 480, "colour", false, "THE INVISIBLE DOG", 1, "top_centre", "top_left");
  const [number, title] = TEXTS;
  assert.equal(number[1], 320, "the number stands in the middle of the band");
  const numberLeft = number[1] - ([...number[0]].length * 0.5 * fontSize(number[5])) / 2;
  const titleRight = title[1] + [...title[0]].length * 0.5 * fontSize(title[5]);
  assert.ok(titleRight < numberLeft, "the title is laid out in what the number leaves it");
});

test("a band with no room left letters no title", async () => {
  const { cardPng } = await import("../../web/cover.js");
  TEXTS.length = 0;
  await cardPng(
    "3/12",
    "",
    null,
    {},
    320,
    240,
    "colour",
    false,
    "THE INVISIBLE DOG OF THE UNKNOWN LANDS",
    1,
    "top_centre",
    "top_left",
  );
  assert.deepEqual(
    TEXTS.map((line) => line[0]),
    ["3/12"],
    "a title that cannot be read at that size is not written at all",
  );
});

test("a card with no number writes its title alone", async () => {
  const { cardPng } = await import("../../web/cover.js");
  TEXTS.length = 0;
  await cardPng("", "Once upon a time", null, {}, 640, 480, "colour", false, "The invisible dog");
  assert.equal(TEXTS[0][0], "The invisible dog", "the title takes the corner by itself");
});

test("a card with no number and no title writes nothing of its own", async () => {
  const { cardPng } = await import("../../web/cover.js");
  TEXTS.length = 0;
  await cardPng("", "Once upon a time", null, {}, 640, 480);
  assert.deepEqual(
    TEXTS.map((line) => line[0]),
    ["Once upon a time"],
    "only the part's own opening words are written",
  );
});

const SHORT = "one two";
const LONG = "you will need to assess the unique set of potential hazards in your area and prepare for them accordingly";

test("a caption is lettered at the largest size the screen holds", () => {
  const ctx = new StubCanvas(640, 480).getContext();
  assert.equal(captionFit(ctx, SHORT, 640, 480).size, 96, "two words fill the screen at its largest lettering");
  const long = captionFit(ctx, LONG, 640, 480);
  assert.ok(long.size < 96 && long.lines.length > 1, "a whole sentence gives way to its own length");
  assert.ok(
    captionFit(ctx, LONG, 320, 212).size < long.size,
    "and a smaller screen makes it give way sooner",
  );
});

test("a caption keeps every word of the sentence", () => {
  const ctx = new StubCanvas(640, 480).getContext();
  assert.equal(captionLines(ctx, LONG, 640, 480).join(" "), LONG);
  assert.equal(captionLines(ctx, LONG, 320, 212).join(" "), LONG);
});

test("a sentence too tall for the screen keeps the lines it has room for", () => {
  const ctx = new StubCanvas(640, 480).getContext();
  const tooTall = "word ".repeat(1200).trim();
  const { size, lines } = captionFit(ctx, tooTall, 640, 480);
  assert.equal(size, CAPTION_MIN_SIZE, "the lettering stops at the smallest it goes");
  assert.notEqual(lines.join(" "), tooTall, "and the sentence is cut short");
  assert.ok(lines.length > 1 && (lines.length - 1) * linePitch(size) + size <= 432, "the lines stop at the screen");
});

test("a part with no words gets no caption picture", async () => {
  const ctx = new StubCanvas(640, 480).getContext();
  assert.deepEqual(captionLines(ctx, "   ", 640, 480), []);
  assert.equal(await captionPng("", 640, 480), null);
});

test("a caption is the words on the card's own ground, and nothing else", async () => {
  FILLS.length = 0;
  TEXTS.length = 0;
  const bytes = await captionPng(LONG, 640, 480, "colour");
  assert.ok(bytes.length > 0);
  assert.deepEqual(FILLS, [[0, 0, 640, 480, 640, 480]], "one fill of the whole screen: no cover behind the words");
  assert.ok(TEXTS.length > 1, "and the sentence is written in more than one line");
});

test("the air between two caption lines grows with the lettering, but has a floor", () => {
  assert.equal(linePitch(96), 96 + Math.round(96 / 7));
  assert.equal(linePitch(8), 8 + 4);
  assert.ok(linePitch(14) - 14 >= 4, "small lettering still keeps its lines apart");
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

test("the crop preview carries the card's own lettering", async () => {
  const { drawPreview } = await import("../../web/cover.js");
  const frame = (width, height) => new StubCanvas(width, height).getContext();

  TEXTS.length = 0;
  drawPreview(frame(640, 480), cover, 640, 480, { crop: "center" }, "colour", false, 1, "Chapter 1/6");
  assert.deepEqual(TEXTS.map((line) => line[0]), ["Chapter 1/6"], "the preview writes the card's words");
  assert.equal(TEXTS[0][1], 640 - 24, "in the corner the card keeps them");

  TEXTS.length = 0;
  drawPreview(frame(640, 480), cover, 640, 480, { crop: "center" }, "colour");
  assert.deepEqual(TEXTS, [], "with no label the preview is the cover alone");

  TEXTS.length = 0;
  FILLS.length = 0;
  drawPreview(frame(320, 240), cover, 320, 240, { crop: "center" }, "bw", false, 1, "3");
  assert.deepEqual(TEXTS.map(([line, x, y]) => [line, x, y]), [["3", 160, 120]], "a Lunii preview centres its number");
  const strips = FILLS.filter(([, , width, height, card]) => card === 320 && width === 320 && height < 240);
  assert.equal(strips.length, 1, "on the strip the card draws behind it");
});
