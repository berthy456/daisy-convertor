// The cover a DAISY audio pack does not carry.
//
// A 2.02 pack has no image, but its metadata names the print book it was read
// from: `dc:source` holds that book's ISBN. No one service has every book, so
// the page asks three of them in order and keeps the first real picture. Each
// answers with no key and with `Access-Control-Allow-Origin: *`, which is what
// a browser is allowed to ask in the first place.
//
// isbndb.com carries covers the others miss but needs a paid key, and Google
// Books' keyless API is one shared quota, spent most days before anyone asks.

import { loadCoverUrl } from "./cover.js";

// Apple serves its covers at a few sizes; 600 is the largest without a key, and
// the Telmi screen is 480 across.
const APPLE_ART_SIZE = "600x600bb";

/**
 * The print book's ISBN. `dc:identifier` is the DAISY edition's own number, and
 * one such as `FR-ADV-1310715633` still leaves ten digits when its letters are
 * stripped, so the shape is checked before anything is stripped: digits, spaces
 * and hyphens, with an X only as the last character.
 */
export function isbnOf(book) {
  for (const value of [book.source, book.identifier]) {
    const text = (value ?? "").trim().toUpperCase().replace(/^URN:ISBN:/, "");
    if (!/^[0-9][0-9 -]*[0-9X]$/.test(text)) continue;
    const digits = text.replace(/[ -]/g, "");
    if (/^\d{13}$/.test(digits) || /^\d{9}[0-9X]$/.test(digits)) return digits;
  }
  return null;
}

/** The exact answer: the print book's own ISBN, when the pack carries one. */
export function openlibraryIsbn(book) {
  const isbn = isbnOf(book);
  return isbn ? `https://covers.openlibrary.org/b/isbn/${isbn}-L.jpg` : null;
}

/** No ISBN, or an ISBN Open Library has never heard of: ask by title and author. */
export async function openlibraryTitle(book) {
  if (!book.title) return null;
  const params = new URLSearchParams({ title: book.title, limit: "5", fields: "cover_i" });
  if (book.author) params.set("author", book.author);
  const docs = (await getJSON(`https://openlibrary.org/search.json?${params}`))?.docs ?? [];
  for (const doc of docs) {
    if (doc.cover_i) return `https://covers.openlibrary.org/b/id/${doc.cover_i}-L.jpg`;
  }
  return null;
}

/**
 * Apple's book store, which lists titles Open Library has no record of at all.
 * It cannot be asked by ISBN, only by words, so the title carries the weight
 * and the author narrows it.
 */
export async function appleBooks(book) {
  if (!book.title) return null;
  const params = new URLSearchParams({
    term: [book.title, book.author].filter(Boolean).join(" "),
    media: "ebook",
    limit: "5",
    output: "json",
  });
  const results = (await getJSON(`https://itunes.apple.com/search?${params}`))?.results ?? [];
  const best = bestMatch(book.title, results);
  const artwork = best?.artworkUrl100;
  return artwork ? artwork.replace("100x100bb", APPLE_ART_SIZE) : null;
}

// Best guess first: an ISBN is the book, a title is only a strong hint. The
// names are the services' own, which no language translates.
export const SOURCES = [
  ["Open Library", openlibraryIsbn],
  ["Apple Books", appleBooks],
  ["Open Library search", openlibraryTitle],
];

/**
 * A title without the punctuation and case that differ between catalogues: the
 * pack says `Wesh  la fée`, Apple says `Wesh, la fée`.
 */
function normalize(value) {
  return (value ?? "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

/** Apple ranks by what sells, not by what matches, so an exact title wins. */
function bestMatch(title, results) {
  const wanted = normalize(title);
  if (wanted) {
    for (const item of results) {
      if (normalize(item.trackName) === wanted) return item;
    }
  }
  return results[0] ?? null;
}

async function getJSON(url) {
  try {
    return await (await fetch(url)).json();
  } catch {
    // A blocked or offline lookup only costs the suggestion.
    throw new Error(`could not read ${url}`);
  }
}

// A service that has stopped answering is not worth waiting for: the page has a
// manual search the user can run while this lookup is still trying, and a book
// none of them carries should be said so in seconds rather than minutes.
const TIMEOUT = 8000;

function within(trying) {
  const limit = new Promise((_, reject) => setTimeout(() => reject(new Error("no answer")), TIMEOUT));
  return Promise.race([trying, limit]);
}

/**
 * The picture and the service it came from, or `{ bitmap: null }`. A service
 * that has no cover answers with a 1x1 GIF, which is a picture of nothing, so
 * each source is tried in turn.
 */
export async function findCover(book, base) {
  for (const [name, locate] of SOURCES) {
    try {
      const url = await within(locate(book));
      if (!url) continue;
      const bitmap = await within(loadCoverUrl(url, base));
      if (bitmap !== null && bitmap.width > 1) return { bitmap, source: name };
    } catch {
      // One service refusing costs only its turn: the next is asked.
      continue;
    }
  }
  return { bitmap: null };
}
