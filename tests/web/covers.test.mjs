// Which number the page asks a cover service with, and what it does with the answer.
// Cropping the picture is cover.test.mjs's business, not this file's.

import assert from "node:assert/strict";
import { test } from "node:test";

const { appleBooks, findCover, googleBooks, isbnOf, openlibraryIsbn, openlibraryTitle, term } =
  await import("../../web/covers.js");

// Values from the Rachel pack: dc:identifier is the DAISY edition's own number,
// dc:source is the print book's ISBN.
const DAISY_ID = "FR-ADV-1310715633";
const PRINT_ISBN = "979-10-363-6487-7";
const APPLE_ART = "https://is1-ssl.mzstatic.com/image/thumb/x/9791036383786-001-x.jpeg/100x100bb.jpg";
const GOOGLE_THUMB =
  "https://books.google.com/books/content?id=abc123&printsec=frontcover&img=1&zoom=1&source=gbs_api";
// A cover, as far as these tests care: anything wider than one pixel.
const COVER = "jpeg";

const book = (over = {}) => ({ title: "Wesh la fée", identifier: DAISY_ID, source: PRINT_ISBN, ...over });

// What each service answers for a book it does not have, so a test only spells
// out the part it is about. Open Library's "nothing" is a 1x1 GIF.
const NOTHING = {
  "-L.jpg": "tiny",
  "googleapis.com": { totalItems: 0 },
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
    ["covers.openlibrary.org", "www.googleapis.com", "itunes.apple.com", "openlibrary.org"],
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
    ["covers.openlibrary.org", "www.googleapis.com", "itunes.apple.com", "is1-ssl.mzstatic.com"],
    "both ISBN answers came back empty, so Apple was asked, and its answer stopped the asking",
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

test("the ISBN is asked of Google when Open Library has no cover", async () => {
  // `L'Aiguille creuse`: Open Library holds no picture for the ISBN the pack
  // carries, and Google Books is the catalogue that has that French edition.
  const urls = asked({
    "googleapis.com": { totalItems: 1, items: [{ volumeInfo: { imageLinks: { thumbnail: GOOGLE_THUMB } } }] },
    "zoom=3": COVER,
  });
  const found = await findCover(book(), "https://x/y/");
  assert.equal(found.source, "Google Books");
  assert.deepEqual(query(urls, "googleapis.com").getAll("q"), ["isbn:9791036364877"]);
  assert.ok(urls.at(-1).includes("zoom=3"), "the largest picture Google gives, not its 128 px one");
});

test("Google answering its daily quota is no cover", async () => {
  // A machine that has spent its day of keyless asks gets an error object back.
  // That is a service with nothing for this book: the next one is still asked.
  asked({
    "googleapis.com": { error: { code: 429, message: "Quota exceeded" } },
    "search.json": { docs: [{ cover_i: 7 }] },
    "b/id/7-L.jpg": COVER,
  });
  const found = await findCover(book(), "https://x/y/");
  assert.equal(found.source, "Open Library search");
});

test("a producer's filler is not searched for", async () => {
  // Dolphin EasyProducer writes `dc:creator=Unknown` when it has no author.
  const urls = asked({});
  await findCover(book({ source: null, author: "Unknown" }), "https://x/y/");
  assert.deepEqual(query(urls, "itunes.apple.com").getAll("term"), ["Wesh la fée"]);
  assert.deepEqual(query(urls, "search.json").getAll("title"), ["Wesh la fée"]);
  assert.equal(query(urls, "search.json").has("author"), false);
});

test("a title running words together is asked with its spaces back", async () => {
  // `MaupassantUneVie` is the name the producer gave the folder it made the pack
  // from. The words inside it are the book's own, and they are what answers.
  const urls = asked({ "search.json": { docs: [{ cover_i: 3140357 }] }, "b/id/3140357-L.jpg": COVER });
  const found = await findCover(book({ source: null, title: "MaupassantUneVie" }), "https://x/y/");
  assert.ok(found.bitmap, "the folder's own words found the book");
  assert.deepEqual(query(urls, "search.json").getAll("title"), ["Maupassant Une Vie"]);
});

test("the words the user types are asked like a pack's", async () => {
  // The Author and Title boxes are a book of their own: they carry no ISBN, so
  // only the catalogues that answer by words are asked.
  const urls = asked({ "search.json": { docs: [{ cover_i: 6866195 }] }, "b/id/6866195-L.jpg": COVER });
  const found = await findCover({ title: "Une vie", author: "Guy de Maupassant" }, "https://x/y/");
  assert.equal(found.source, "Open Library search");
  assert.deepEqual(query(urls, "search.json").getAll("title"), ["Une vie"]);
  assert.deepEqual(query(urls, "search.json").getAll("author"), ["Guy de Maupassant"]);
  assert.equal(urls.some((url) => url.includes("b/isbn")), false, "typed words carry no ISBN");
});

test("what is worth asking a catalogue for", () => {
  assert.equal(term("Unknown"), "");
  assert.equal(term("inconnu"), "");
  assert.equal(term("  Une vie  "), "Une vie");
  assert.equal(term("Maupassant Une Vie"), "Maupassant Une Vie");
  assert.equal(term("MaupassantUneVie"), "Maupassant Une Vie");
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
