// The shape of the page itself: which fields it asks for, and in which order.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));

const html = await readFile(`${ROOT}web/index.html`, "utf8");
const js = await readFile(`${ROOT}web/app.js`, "utf8");

/** Where something first appears in the page, as a position in its text. */
function at(needle) {
  const at = html.indexOf(needle);
  assert.ok(at >= 0, `the page has no ${needle}`);
  return at;
}

test("no field of the page is asked for twice", () => {
  const ids = [...html.matchAll(/\bid="([a-z0-9-]+)"/g)].map((match) => match[1]);
  const repeated = ids.filter((id, index) => ids.indexOf(id) !== index);
  assert.deepEqual(repeated, [], `${repeated.join(", ")} appears more than once`);
});

test("the card choices are the language, then the words, then the sound", () => {
  const order = [
    "card-language",
    'data-i18n="cards_text_line"',
    "card-text-title",
    "card-text-chapter",
    "card-text-number",
    "card-text-total",
    'data-i18n="cards_say_line"',
    "card-say-word",
    "card-say-count",
    "card-audio",
    "card-seconds",
  ];
  const places = order.map(at);
  assert.deepEqual(
    places,
    [...places].sort((a, b) => a - b),
    "the card fields are out of order",
  );
});

test("the fields a pack with no pictures has nothing to answer for are put away", () => {
  const rows = [
    ["cover-section", "coverSection"],
    ["cards-text-row", "cardsTextRow"],
    ["test", "testButton"],
  ];
  for (const [id, name] of rows) {
    assert.ok(html.includes(`id="${id}"`), `the page has no #${id} to put away`);
    assert.ok(
      js.includes(`${name}.hidden = format === "mp3"`),
      `#${id} is not hidden for the playlist, which carries no pictures`,
    );
  }
});

test("the extract length is out of reach for the pack that plays no extract", () => {
  assert.ok(
    js.includes('cardSecondsInput.disabled = cards.audio !== "snippet" || format === "mp3"'),
    "the playlist's interlude is always spoken, so its seconds box must be greyed too",
  );
});

test("the cover link is fetched from its own box", () => {
  assert.equal(html.includes('id="cover-use"'), false, "the box has no separate button any more");
  assert.ok(at('id="cover-url"') < at('id="cover-clear"'), "the box comes before the clear button");
});

test("the manual search stands before the two fields it searches with", () => {
  assert.ok(at('id="cover-search-app"') < at('id="cover-author"'));
  assert.ok(at('id="cover-search-app"') < at('id="cover-title"'));
});
