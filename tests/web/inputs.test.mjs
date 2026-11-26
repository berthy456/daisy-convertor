// The inputs the desktop reader added in v4: DAISY 3, a text-only pack, an EPUB.

import assert from "node:assert/strict";
import { test } from "node:test";

import { extractBook, languageSample } from "../../web/daisy.js";

const bytes = (text) => new TextEncoder().encode(text);
const entry = (name, text) => ({ name, data: bytes(text) });

const OPF = (title) => `<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2006/01/opf" unique-identifier="uid">
  <metadata>
    <dc-metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:Title>${title}</dc:Title>
      <dc:Creator>WikiHow</dc:Creator>
      <dc:Language>en</dc:Language>
    </dc-metadata>
  </metadata>
</package>
`;

const NCX = `<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <docTitle>
    <text>ARE YOU READY?</text>
    <audio clipBegin="0:00:00" clipEnd="0:00:02.029" src="front.mp3"/>
  </docTitle>
  <docAuthor>
    <text>WikiHow</text>
    <audio clipBegin="0:00:02.029" clipEnd="0:00:04.878" src="front.mp3"/>
  </docAuthor>
  <navMap>
    <navPoint class="h1" id="n1" playOrder="1">
      <navLabel><text>The whole book</text></navLabel>
      <content src="one.smil#n1"/>
      <navPoint class="h2" id="n2" playOrder="2">
        <navLabel><text>Chapter One</text></navLabel>
        <content src="two.smil#n2"/>
        <navPoint class="h3" id="n3" playOrder="3">
          <navLabel><text>Steps</text></navLabel>
          <content src="three.smil#n3"/>
        </navPoint>
      </navPoint>
      <navPoint class="h2" id="n4" playOrder="4">
        <navLabel><text>Chapter Two</text></navLabel>
        <content src="four.smil#n4"/>
      </navPoint>
    </navPoint>
  </navMap>
</ncx>
`;

const DTBOOK = `<?xml version="1.0" encoding="UTF-8"?>
<dtbook xmlns="http://www.daisy.org/z3986/2005/struct/"><book><doc>
  <p id="d1">Make a plan for your family.</p>
  <p id="d2">Pack water first.</p>
</doc></book></dtbook>
`;

// DAISY 3 SMIL trims with camelCase `clipBegin`; 2.02 writes `clip-begin`.
const smil3 = (word, audio, begin, end) =>
  `<?xml version="1.0" encoding="UTF-8"?>
<smil><body><seq>
<par id="p"><text src="book.xml#${word}"/><audio clipBegin="0:00:${begin}" clipEnd="0:00:${end}" src="${audio}"/></par>
</seq></body></smil>
`;

const mp3 = entry("front.mp3", "");

function daisy3() {
  return [
    entry("areyouready.opf", OPF("ARE YOU READY?")),
    entry("areyouready.ncx", NCX),
    entry("book.xml", DTBOOK),
    entry("one.smil", smil3("d1", "one.mp3", "00", "05")),
    entry("two.smil", smil3("d1", "two.mp3", "00", "04")),
    entry("three.smil", smil3("d2", "three.mp3", "04", "09")),
    entry("four.smil", smil3("d2", "four.mp3", "00", "07")),
    entry("one.mp3", ""),
    entry("two.mp3", ""),
    entry("three.mp3", ""),
    entry("four.mp3", ""),
    mp3,
  ];
}

test("a DAISY 3 pack is read from its NCX and its package document", () => {
  const book = extractBook(daisy3());
  assert.equal(book.title, "ARE YOU READY?");
  assert.equal(book.author, "WikiHow");
  assert.equal(book.language, "en");
});

test("one h1 wrapper does not swallow the book", () => {
  const book = extractBook(daisy3());
  assert.deepEqual(book.chapters.map((c) => c.title), ["The whole book", "Chapter One", "Chapter Two"]);
});

test("DAISY 3 clips are trimmed by their camelCase clip points", () => {
  const book = extractBook(daisy3());
  assert.deepEqual(
    book.chapters[1].segments.map((s) => [s.src, s.begin, s.end]),
    [
      ["two.mp3", 0, 4],
      ["three.mp3", 4, 9],
    ],
  );
});

test("the DAISY 3 front matter is the NCX header, merged into one range", () => {
  const book = extractBook(daisy3());
  assert.deepEqual(
    book.frontMatter.map((s) => [s.src, s.begin, s.end]),
    [["front.mp3", 0, 4.878]],
  );
});

test("a DAISY 3 card shows the DTBook body", () => {
  assert.deepEqual(extractBook(daisy3()).chapters.map((c) => c.text), [
    "Make a plan for your family.",
    "Make a plan for your family.",
    "Pack water first.",
  ]);
});

const TEXT_NCC = `<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><meta name="dc:title" content="Treaty"/></head>
  <body>
    <h1 id="c1"><a href="a.smil#t1">Article 1</a></h1>
    <h1 id="c2"><a href="b.smil#t3">Article 2</a></h1>
  </body>
</html>
`;

const WORDS = `<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <p id="s1">Le loup sortit de la foret.</p>
  <p id="s2">Il cherchait de quoi manger.</p>
  <p id="s3">Un agneau buvait a la riviere.</p>
</body></html>
`;

const textSmil = (anchors) =>
  `<?xml version="1.0" encoding="UTF-8"?>
<smil><body><seq>
${anchors.map((a) => `<par><text src="text.html#${a}"/></par>`).join("\n")}
</seq></body></smil>
`;

const textOnly = () => [
  entry("ncc.html", TEXT_NCC),
  entry("text.html", WORDS),
  entry("a.smil", textSmil(["s1", "s2"])),
  entry("b.smil", textSmil(["s3"])),
];

test("a pack with no audio still gives chapters, and keeps their words", () => {
  const book = extractBook(textOnly());
  assert.deepEqual(book.chapters.map((c) => c.title), ["Article 1", "Article 2"]);
  assert.deepEqual(book.chapters.map((c) => c.segments.length), [0, 0]);
  assert.equal(book.chapters[0].body, "Le loup sortit de la foret. Il cherchait de quoi manger.");
});

test("the language sample of a text pack is its prose", () => {
  const sample = languageSample(extractBook(textOnly()));
  assert.equal(
    sample,
    "Treaty Article 1 Le loup sortit de la foret. Il cherchait de quoi manger. Article 2 Un agneau buvait a la riviere.",
  );
});

test("the language sample stops at its own length", () => {
  const book = {
    title: "Long",
    chapters: [
      { title: "Un", body: "un mot ".repeat(2000), text: "" },
      { title: "Deux", body: "later", text: "" },
    ],
  };
  const sample = languageSample(book, 100);
  assert.equal(sample.length, 100);
  assert.ok(!sample.includes("later"));
});

const { buildStoryFiles } = await import("../../web/convert.js");
const { installCanvasStub } = await import("./canvas-stub.mjs");

installCanvasStub();

test("the page refuses a pack it has no audio to cut, instead of a silent story", async () => {
  await assert.rejects(
    () => buildStoryFiles(textOnly(), bytes(""), () => {}, {}),
    /2 part\(s\) of this pack have no audio, starting with "Article 1"/,
  );
});

const EPUB_OPF = `<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>The Wolf</dc:title>
    <dc:creator>Aesop</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
</package>
`;

const EPUB_NCX = `<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <docTitle><text>The Wolf</text></docTitle>
  <navMap>
    <navPoint class="h1" id="t1" playOrder="1">
      <navLabel><text>Chapter One</text></navLabel>
      <content src="book.xhtml#c1"/>
      <navPoint class="h2" id="t2" playOrder="2">
        <navLabel><text>A section</text></navLabel>
        <content src="book.xhtml#c2"/>
      </navPoint>
    </navPoint>
    <navPoint class="h1" id="t3" playOrder="3">
      <navLabel><text>Chapter Two</text></navLabel>
      <content src="book.xhtml#c3"/>
    </navPoint>
    <navPoint class="h1" id="t4" playOrder="4">
      <navLabel><text>Cover</text></navLabel>
      <content src="cover.jpg"/>
    </navPoint>
  </navMap>
</ncx>
`;

const EPUB_PAGE = `<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="c1">Chapter One</h1>
    <p>The wolf went out. He was looking for food.</p>
    <h2 id="c2">A section</h2>
    <p>An lamb drank at the river.</p>
    <h1 id="c3">Chapter Two</h1>
    <p>Next morning the wolf came back.</p>
  </body>
</html>
`;

const epub = () => [
  entry("mimetype", "application/epub+zip"),
  entry("OEBPS/package.opf", EPUB_OPF),
  entry("OEBPS/navigation.ncx", EPUB_NCX),
  entry("OEBPS/book.xhtml", EPUB_PAGE),
  entry("OEBPS/cover.jpg", ""),
];

test("an EPUB chapter is the text between two anchors the outline names", () => {
  const book = extractBook(epub());
  assert.deepEqual(book.chapters.map((c) => c.title), ["Chapter One", "Chapter Two"]);
  assert.equal(book.chapters[1].body, "Chapter Two Next morning the wolf came back.");
  assert.equal(book.chapters[0].text, "Chapter One The wolf went out.");
});

test("an EPUB sub-heading folds into its chapter", () => {
  const book = extractBook(epub());
  assert.equal(
    book.chapters[0].body,
    "Chapter One The wolf went out. He was looking for food. A section An lamb drank at the river.",
  );
});

test("an EPUB cut to sentences labels every card", () => {
  const book = extractBook(epub(), "sentence");
  assert.deepEqual(book.chapters.map((c) => c.text), [
    "Chapter One The wolf went out.",
    "He was looking for food.",
    "A section An lamb drank at the river.",
    "Chapter Two Next morning the wolf came back.",
  ]);
  assert.deepEqual(book.chapters.map((c) => c.title), ["Part 1", "Part 2", "Part 3", "Part 4"]);
});
