// Which number the page asks a cover service with, and what it does with the answer.
// Cropping the picture is cover.test.mjs's business, not this file's.

import assert from "node:assert/strict";
import { test } from "node:test";

const { appleBooks, findCover, isbnOf, openlibraryIsbn, openlibraryTitle } = await import(
  "../../web/covers.js"
);

// Values from the Rachel pack: dc:identifier is the DAISY edition's own number,
// dc:source is the print book's ISBN.
const DAISY_ID = "FR-ADV-1310715633";
const PRINT_ISBN = "979-10-363-6487-7";
const APPLE_ART = "https://is1-ssl.mzstatic.com/image/thumb/x/9791036383786-001-x.jpeg/100x100bb.jpg";
// A cover, as far as these tests care: anything wider than one pixel.
const COVER = "jpeg";

const book = (over = {}) => ({ title: "Wesh la fée", identifier: DAISY_ID, source: PRINT_ISBN, ...over });

// What each service answers for a book it does not have, so a test only spells
// out the part it is about. Open Library's "nothing" is a 1x1 GIF.
const NOTHING = {
  "-L.jpg": "tiny",
  "itunes.apple.com": { results: [] },
  "search.json": { docs: [] },
};

/**
 * Stand in for the network: record what was asked, reply with what a test wants.
 * A string reply is picture bytes, an object reply is JSON, and `null` is a
 * service that does not answer at all.
 */
function asked(replies, fail = false) {
  const urls = [];
  globalThis.createImageBitmap = async (body) =>
    body === "tiny" ? { width: 1, height: 1 } : { width: 405, height: 600 };
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    if (fail) throw new TypeError("failed to fetch");
    // A test's own reply wins over the standing one, however specific either is.
    for (const [part, body] of Object.entries(replies ?? {}).concat(Object.entries(NOTHING))) {
      if (!String(url).includes(part)) continue;
      if (body === null) throw new TypeError("failed to fetch");
      return typeof body === "string"
        ? { ok: true, blob: async () => body }
        : { json: async () => body };
    }
    throw new Error(`unexpected request: ${url}`);
  };
  return urls;
}

/** The query of the one request that mentions `part`. */
function query(urls, part) {
  const hits = urls.filter((url) => url.includes(part));
  assert.equal(hits.length, 1, `${part} was asked ${hits.length} times`);
  return new URL(hits[0]).searchParams;
}

test("the print book's ISBN is the one in dc:source", () => {
  assert.equal(isbnOf(book()), "9791036364877");
});

test("a DAISY identifier is not taken for an ISBN", () => {
  assert.equal(isbnOf(book({ source: null })), null);
});

test("an ISBN-10 keeps its check letter", () => {
  assert.equal(isbnOf(book({ source: "0-140-44918-0", identifier: null })), "0140449180");
  assert.equal(isbnOf(book({ source: "081269018x", identifier: null })), "081269018X");
});

test("an ISBN written as a URN is still an ISBN", () => {
  assert.equal(isbnOf(book({ source: "urn:isbn:9780140449185", identifier: null })), "9780140449185");
});

test("an ISBN points straight at the cover", () => {
  assert.equal(openlibraryIsbn(book()), "https://covers.openlibrary.org/b/isbn/9791036364877-L.jpg");
});

test("an ISBN that answers stops the asking", async () => {
  const urls = asked({ "b/isbn/9791036364877-L.jpg": COVER });
  const found = await findCover(book(), "https://berthy456.github.io/daisy2telmi/");
  assert.equal(found.source, "Open Library");
  assert.deepEqual(urls, ["https://covers.openlibrary.org/b/isbn/9791036364877-L.jpg"]);
});

test("a book no service has leaves nothing behind", async () => {
  const urls = asked({});
  const found = await findCover(book(), "https://x/y/");
  assert.equal(found.bitmap, null);
  assert.equal(found.source, undefined);
  assert.deepEqual(
    urls.map((url) => url.split("/")[2]),
    ["covers.openlibrary.org", "itunes.apple.com", "openlibrary.org"],
    "every service is asked before the answer says there is nothing",
  );
});

test("a book Open Library has never heard of is asked of Apple", async () => {
  const urls = asked({
    "itunes.apple.com": { results: [{ trackName: "Wesh, la fée", artworkUrl100: APPLE_ART }] },
    "600x600bb": COVER,
  });
  const found = await findCover(book(), "https://berthy456.github.io/daisy2telmi/");
  assert.equal(found.bitmap.width, 405);
  assert.equal(found.source, "Apple Books", "the log says which service answered");
  assert.deepEqual(
    urls.map((url) => url.split("/")[2]),
    ["covers.openlibrary.org", "itunes.apple.com", "is1-ssl.mzstatic.com"],
    "the ISBN came back empty, so Apple was asked, and its answer stopped the asking",
  );
  assert.deepEqual(query(urls, "itunes.apple.com").getAll("term"), ["Wesh la fée"]);
  assert.deepEqual(query(urls, "itunes.apple.com").getAll("media"), ["ebook"]);
});

test("Apple is asked at the largest cover it gives", async () => {
  const urls = asked({
    "itunes.apple.com": { results: [{ trackName: "Wesh la fée", artworkUrl100: APPLE_ART }] },
    "600x600bb": COVER,
  });
  await findCover(book(), "https://berthy456.github.io/daisy2telmi/");
  assert.ok(urls.at(-1).includes("600x600bb"));
});

test("an exact title beats Apple's first hit", async () => {
  const urls = asked({
    "itunes.apple.com": {
      results: [
        { trackName: "Wesh la fée : le coffret", artworkUrl100: "https://x/wrong/100x100bb.jpg" },
        { trackName: "Wesh,  la fée !", artworkUrl100: APPLE_ART },
      ],
    },
    "600x600bb": COVER,
  });
  await findCover(book(), "https://berthy456.github.io/daisy2telmi/");
  assert.ok(urls.at(-1).includes("9791036383786"), "the exact title's artwork, not the coffret's");
});

test("the author narrows the search", async () => {
  const urls = asked({});
  await findCover(book({ source: null, author: "Rachel Corenblit" }), "https://x/y/");
  assert.deepEqual(query(urls, "itunes.apple.com").getAll("term"), ["Wesh la fée Rachel Corenblit"]);
});

test("without an ISBN the title and author are asked", async () => {
  const urls = asked({ "search.json": { docs: [{ cover_i: 12123878 }] }, "b/id/12123878-L.jpg": COVER });
  const found = await findCover(book({ source: null, author: "Rachel Corenblit" }), "https://x/y/");
  assert.equal(found.source, "Open Library search");
  assert.deepEqual(query(urls, "search.json").getAll("title"), ["Wesh la fée"]);
  assert.deepEqual(query(urls, "search.json").getAll("author"), ["Rachel Corenblit"]);
  assert.equal(urls.at(-1), "https://covers.openlibrary.org/b/id/12123878-L.jpg");
});

test("a book with neither ISBN nor title asks nothing", async () => {
  const urls = asked({});
  const found = await findCover(book({ source: null, identifier: null, title: "" }), "https://x/y/");
  assert.equal(found.bitmap, null);
  assert.deepEqual(urls, []);
});

test("one service being down does not stop the others", async () => {
  const urls = asked({
    "itunes.apple.com": null,
    "search.json": { docs: [{ cover_i: 7 }] },
    "b/id/7-L.jpg": COVER,
  });
  const found = await findCover(book({ source: null }), "https://x/y/");
  assert.ok(found.bitmap);
  assert.ok(urls.some((url) => url.includes("search.json")));
});

test("an unreachable service is no error, only no cover", async () => {
  asked({}, true);
  assert.equal((await findCover(book(), "https://x/y/")).bitmap, null);
});
