// Node test runner suite for the browser modules: node --test tests/web/

import assert from "node:assert/strict";
import { readFile, mkdtemp, access } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const PACK = join(ROOT, "gon-ruby-mp3.zip");

const { readZip, writeZip } = await import("../../web/zip.js");
const { extractBook } = await import("../../web/daisy.js");
const { buildStoryFiles, buildStoryZip, pausePoints, partTitle } = await import("../../web/convert.js");
const { buildNodes, normalizeFileName, slugify, storyDirName, storyUuid } = await import("../../web/telmi.js");
const { mp3Frames, trimFrames } = await import("../../web/mp3.js");
const { cardWords } = await import("../../web/cover.js");
const { chapterWords } = await import("../../web/i18n.js");

const { installCanvasStub } = await import("./canvas-stub.mjs");

installCanvasStub();

async function pack(name) {
  return readZip(new Uint8Array(await readFile(join(ROOT, name))));
}

// The Rachel pack is a copyrighted sample kept out of the repository, so CI
// only has the gon pack.
async function present(name) {
  try {
    await access(join(ROOT, name));
    return true;
  } catch {
    return false;
  }
}

test("folder naming reproduces a real published Telmi story", () => {
  assert.equal(
    storyDirName("La reine des pirates", "fffffd-19d14f92f57", 0, ""),
    "_00_La reine des pirates_fffffd-19d14f92f57",
  );
});

test("a title that normalizes to nothing takes the pack's own name", () => {
  assert.equal(storyDirName("ごんぎつね", "8f2c-44", 0, "", "gon-ruby-mp3"), "_00_gon-ruby-mp3_8f2c-44");
  assert.equal(storyDirName("ごんぎつね", "8f2c-44"), "_00_story_8f2c-44");
});

test("normalising and slugifying match Telmi-Sync's Strings.js", () => {
  // Values taken from the Python implementation, which was diffed against Strings.js.
  assert.equal(normalizeFileName("L'île  à   Vélo!!"), "L ile a Velo ");
  assert.equal(slugify("  Wesh  la fée 1234 "), "-wesh-la-fee-1234-");
  assert.equal(normalizeFileName("ごんぎつね"), " ");
  assert.equal(slugify("ごんぎつね"), "-");
});

test("the uuid matches the command line converter", async () => {
  const book = extractBook(await pack("gon-ruby-mp3.zip"));
  assert.equal(await storyUuid(book), "fe3ea2d2-4b0d-55eb-b18a-9a4d143ae318");
});

test("the outline splits on top-level chapters", async () => {
  const book = extractBook(await pack("gon-ruby-mp3.zip"));
  assert.equal(book.title, "ごんぎつね");
  assert.equal(book.chapters.length, 6);
  assert.deepEqual(book.chapters.map((c) => c.segments.length), [9, 5, 4, 3, 2, 2]);
  assert.equal(book.frontMatter.length, 1);
});

test("the cut levels follow the marks the pack itself carries", async () => {
  const entries = await pack("gon-ruby-mp3.zip");
  assert.equal(extractBook(entries, "chapter").chapters.length, 6);
  assert.equal(extractBook(entries, "paragraph").chapters.length, 25);
  assert.equal(extractBook(entries, "sentence").chapters.length, 213);
  assert.throws(() => extractBook(entries, "verse"), /unknown cut level/);
});

test("a part carries the words the pack read over it", async () => {
  const entries = await pack("gon-ruby-mp3.zip");
  const sentences = extractBook(entries, "sentence");
  assert.deepEqual(sentences.chapters.slice(0, 2).map((part) => part.text), ["一", "1"]);
  assert.ok(sentences.chapters[2].text.startsWith("これは、私（わたし）"), sentences.chapters[2].text);
  // A chapter card shows the first real sentence, not the section number read out before it.
  assert.ok(extractBook(entries, "chapter").chapters[0].text.startsWith("これは、私（わたし）"));
});

test("a sentence-cut part knows the chapter it was read under", async () => {
  const entries = await pack("gon-ruby-mp3.zip");
  const sentences = extractBook(entries, "sentence").chapters;
  const chapters = extractBook(entries, "chapter").chapters;
  const owners = sentences.map((part) => part.chapter);
  assert.deepEqual(
    [...new Set(owners)],
    chapters.map((_, index) => index),
    "the book's own chapters, in the order they play",
  );
  assert.equal(
    owners.filter((value, index) => index === 0 || value !== owners[index - 1]).length,
    chapters.length,
    "a chapter's sentences stay together in the list",
  );
  assert.equal(sentences[0].chapterTitle, chapters[0].title);
  assert.deepEqual(
    chapters.map((part) => part.chapter),
    chapters.map((_, index) => index),
    "at chapter level a part is its own chapter",
  );
});

test("a card below chapter level plays the opening seconds of its part", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const story = await buildStoryFiles(await pack("gon-ruby-mp3.zip"), silent, () => {}, {
    cut: "paragraph",
    autoplay: true,
    pauseAfter: "paragraph",
  });
  const bytes = new Map(story.files.map((file) => [file.name.replace(`${story.dirName}/`, ""), file.data.length]));

  // A paragraph runs about a minute, so the card clip is a fraction of its part.
  assert.ok(bytes.get("audios/c0.mp3") > 0);
  assert.equal(bytes.get("audios/c0.mp3") < bytes.get("audios/s0.mp3"), true);
  assert.equal(story.files.filter((file) => file.name.includes("/audios/c")).length, story.book.chapters.length);
});

test("a part shorter than the card clip is not stored twice", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const story = await buildStoryFiles(await pack("gon-ruby-mp3.zip"), silent, () => {}, {
    cut: "sentence",
    autoplay: true,
    pauseAfter: "sentence",
  });
  const nodes = JSON.parse(new TextDecoder().decode(story.files.find((file) => file.name.endsWith("nodes.json")).data));
  const cards = story.book.chapters.map((_, k) => nodes.stages[`m${k}`].audio);

  assert.ok(cards.every((audio) => audio !== null), "every card should have sound to navigate by");
  // The first sentence lasts under three seconds, so its card plays the part itself.
  assert.equal(cards[0], "s0.mp3");
  assert.equal(story.files.some((file) => file.name.endsWith("/audios/c0.mp3")), false);
  assert.ok(story.files.filter((file) => file.name.includes("/audios/c")).length < story.book.chapters.length);
});

test("a chapter card can play its part's opening instead of a recorded clip", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  let asked = 0;
  const story = await buildStoryFiles(await pack("gon-ruby-mp3.zip"), silent, () => {}, {
    cut: "chapter",
    cardAudioSource: "snippet",
    cardAudio: async () => {
      asked += 1;
      return silent;
    },
  });
  const bytes = new Map(story.files.map((file) => [file.name.replace(`${story.dirName}/`, ""), file.data.length]));

  assert.equal(asked, 0, "no recorded clip is asked for");
  assert.ok(bytes.get("audios/c0.mp3") > 0);
  assert.equal(bytes.get("audios/c0.mp3") < bytes.get("audios/s0.mp3"), true);
});

test("the card audio length is the one typed in", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const entries = await pack("gon-ruby-mp3.zip");
  const clip = async (seconds) => {
    const story = await buildStoryFiles(entries, silent, () => {}, {
      cut: "paragraph",
      cardAudioSource: "snippet",
      cardSeconds: seconds,
    });
    return story.files.find((file) => file.name.endsWith("/audios/c0.mp3")).data.length;
  };
  const short = await clip(1);
  const long = await clip(6);
  assert.ok(short < long, `${short} bytes for 1 s, ${long} for 6 s`);
});

test("a silent card is given no audio at all", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const story = await buildStoryFiles(await pack("gon-ruby-mp3.zip"), silent, () => {}, {
    cut: "chapter",
    cardAudioSource: "off",
    cardAudio: async () => silent,
  });
  const nodes = JSON.parse(new TextDecoder().decode(story.files.find((file) => file.name.endsWith("nodes.json")).data));

  assert.equal(story.files.some((file) => file.name.includes("/audios/c")), false);
  assert.ok(story.book.chapters.every((_, k) => nodes.stages[`m${k}`].audio === null));
});

test("a story cut at sentences carries a picture of each part's own words", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const story = await buildStoryFiles(await pack("gon-ruby-mp3.zip"), silent, () => {}, {
    cut: "sentence",
    player: "telmi",
    captions: true,
  });
  const scenes = story.files.filter((file) => /\/images\/s\d+\.png$/.test(file.name));
  const worded = story.book.chapters.filter((part) => part.text.trim());
  assert.equal(scenes.length, worded.length, "one picture per part that has words to draw");
  assert.equal(story.nodes.stages.s0.image, "s0.png");
  // The card keeps its own picture: only the part being played shows its words.
  assert.equal(story.nodes.stages.m0.image, "m0.png");
});

test("a screen too small for a sentence, or a cut too coarse, gets no caption pictures", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  for (const asked of [{ player: "lunii", cut: "sentence" }, { player: "telmi", cut: "chapter" }]) {
    const story = await buildStoryFiles(await pack("gon-ruby-mp3.zip"), silent, () => {}, { ...asked, captions: true });
    assert.equal(story.files.some((file) => /\/images\/s\d+\.png$/.test(file.name)), false, asked.player);
    assert.equal(story.nodes.stages.s0.image, null);
  }
});

test("a chapter can carry its own picture", () => {
  const nodes = buildNodes(["s0", "s1"], ["c0.mp3", null], ["s0.png", null]);
  assert.equal(nodes.stages.s0.image, "s0.png");
  assert.equal(nodes.stages.s1.image, null);
  assert.equal(nodes.stages.m1.audio, null);
});

test("a card that runs on does not wait for ok", () => {
  const once = buildNodes(["s0", "s1"], [], [], [false, true]);
  assert.equal(once.stages.m0.control.autoplay, false);
  assert.equal(once.stages.m1.control.autoplay, true);
  const all = buildNodes(["s0", "s1"], [], [], true);
  assert.deepEqual(
    ["m0", "m1"].map((name) => all.stages[name].control.autoplay),
    [true, true],
  );
});

test("where a story stops for ok follows its cut and the pause asked for", () => {
  const parts = [0, 0, 1, 2, 2].map((group) => ({ chapter: group }));
  assert.deepEqual(pausePoints(parts, "chapter", "paragraph"), [true, true, true, true, true]);
  assert.deepEqual(pausePoints(parts, "chapter", "never"), [false, false, false, false, false]);
  assert.deepEqual(pausePoints(parts, "sentence", "sentence"), [true, true, true, true, true]);
  assert.deepEqual(pausePoints(parts, "sentence", "never"), [false, false, false, false, false]);
  for (const pause of ["chapter", "paragraph"]) {
    assert.deepEqual(pausePoints(parts, "sentence", pause), [true, false, true, true, false], pause);
  }
});

test("a story cut below its chapters stops at the end of each one", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const story = await buildStoryFiles(await pack("gon-ruby-mp3.zip"), silent, () => {}, {
    cut: "sentence",
    autoplay: true,
    pauseAfter: "chapter",
    cardAudioSource: "snippet",
  });
  const cards = Object.keys(story.nodes.stages).filter((name) => name.startsWith("m"));
  const stops = cards.filter((name) => !story.nodes.stages[name].control.autoplay);
  const spoken = cards.filter((name) => story.nodes.stages[name].audio !== null);
  assert.ok(stops.length > 0 && stops.length < cards.length, `${stops.length} stops over ${cards.length} cards`);
  assert.deepEqual(spoken, stops, "only the cards the story stops on have something to play");
});

test("a story that does not autoplay waits at its chapters, whatever pause was asked for", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const story = await buildStoryFiles(await pack("gon-ruby-mp3.zip"), silent, () => {}, {
    cut: "sentence",
    pauseAfter: "never",
    cardAudioSource: "snippet",
  });
  const cards = Object.keys(story.nodes.stages).filter((name) => name.startsWith("m"));
  const stops = cards.filter((name) => !story.nodes.stages[name].control.autoplay);
  assert.ok(stops.length > 0 && stops.length < cards.length, `${stops.length} stops over ${cards.length} cards`);
});

test("trimming an MP3 keeps whole frames inside the asked range", async () => {
  const data = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const whole = mp3Frames(data);
  assert.ok(whole.length > 0);
  assert.equal(trimFrames(data, null, null).length, whole.length);
  const part = trimFrames(data, 0.2, 0.6);
  assert.ok(part.length > 0 && part.length < whole.length);
  // The same range cut twice gives the same bytes: the cut is on frame boundaries.
  assert.equal(trimFrames(data, 0.2, 0.6).length, part.length);
});

test("card words wrap to three lines and break a run with no spaces", () => {
  const ctx = { measureText: (line) => ({ width: [...line].length * 10 }) };
  assert.deepEqual(cardWords(ctx, "one two", 200), ["one two"]);
  assert.equal(cardWords(ctx, "abcdefghij ".repeat(30), 100).length, 3);
  assert.deepEqual(cardWords(ctx, "これは私が小さいときに", 60), ["これは私が小", "さいときに"]);
  assert.deepEqual(cardWords(ctx, "", 100), []);
});

test(
  "a second, structurally different pack also parses",
  { skip: !(await present("Rachel-Corenblit-Wesh-la-fee.zip")) ? "sample pack not in the repo" : false },
  async () => {
    const book = extractBook(await pack("Rachel-Corenblit-Wesh-la-fee.zip"));
    assert.equal(book.chapters.length, 23);
  },
);

test("the story zip carries every entry Telmi-Sync requires", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const story = await buildStoryZip(await pack("gon-ruby-mp3.zip"), silent);
  const names = new Set(story.files.map((file) => file.name.replace(`${story.dirName}/`, "")));

  for (const required of ["audios", "images", "metadata.json", "nodes.json", "title.mp3", "title.png"]) {
    assert.ok([...names].some((name) => name.startsWith(`${required}/`) || name === required), `missing ${required}`);
  }
  assert.equal([...names].filter((name) => name.startsWith("audios/")).length, 6);
  assert.equal([...names].filter((name) => name.startsWith("images/")).length, 6);
});

test("the story keeps the book's own words in text/", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const story = await buildStoryZip(await pack("gon-ruby-mp3.zip"), silent);
  const named = new Map(story.files.map((file) => [file.name.replace(`${story.dirName}/`, ""), file.data]));
  const spanned = story.book.chapters.map((chapter, k) => (chapter.spans.length ? k : -1)).filter((k) => k >= 0);
  assert.ok(spanned.length, "the test pack carries no synced text to keep");

  assert.ok(new TextDecoder().decode(named.get("text/book.txt")).trim());
  for (const k of spanned) {
    for (const extension of [".lrc", ".srt"]) {
      assert.ok(named.has(`text/s${k}${extension}`), `missing text/s${k}${extension}`);
    }
  }
  const lyric = new TextDecoder().decode(named.get("text/s0.lrc")).split("\n");
  assert.ok(lyric[0].startsWith("[ti:"), lyric[0]);
  assert.ok(lyric.slice(1).some((line) => line.startsWith("[0")), lyric.slice(1)[0]);

  const inZip = new Set((await readZip(story.zip)).map((entry) => entry.name.replace(`${story.dirName}/`, "")));
  assert.ok(inZip.has("text/book.txt"));
});

// The desktop Chapters window lets parts be left out; the page's boxes do the
// same, so the numbers stay in order after the removal.
test("leaving parts out renumbers the story", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const entries = await pack("gon-ruby-mp3.zip");
  const whole = extractBook(entries, "chapter").chapters;
  const kept = await buildStoryFiles(entries, silent, () => {}, { drop: [2, 5] });
  const names = new Set(kept.files.map((file) => file.name.replace(`${kept.dirName}/`, "")));

  assert.equal(kept.book.chapters.length, 4);
  assert.deepEqual(kept.book.chapters.map((part) => part.title), [0, 2, 3, 5].map((k) => whole[k].title));
  for (const k of [0, 1, 2, 3]) {
    assert.ok(names.has(`audios/s${k}.mp3`), `missing s${k}`);
    assert.ok(names.has(`images/m${k}.png`), `missing m${k}`);
  }
  assert.equal(names.has("audios/s4.mp3"), false, "a dropped part left its audio behind");
  assert.equal(names.has("images/m5.png"), false, "a dropped part left its image behind");

  const nodes = JSON.parse(new TextDecoder().decode(kept.files.find((file) => file.name.endsWith("nodes.json")).data));
  assert.deepEqual(Object.keys(nodes.stages).sort(), ["m0", "m1", "m2", "m3", "s0", "s1", "s2", "s3"]);
});

test("feeding back a converted story says what it is", async () => {
  const entries = await pack("gon-ruby-mp3-telmi.zip");
  assert.throws(() => extractBook(entries), /already a Telmi story/);
});

test("the written zip reads back identically", async () => {
  const silent = new Uint8Array(await readFile(join(ROOT, "web/silence.mp3")));
  const story = await buildStoryFiles(await pack("gon-ruby-mp3.zip"), silent);
  const dir = await mkdtemp(join(tmpdir(), "daisy2telmi-"));
  const written = await writeZip(story.files);
  const back = await readZip(written);

  assert.equal(back.length, story.files.length);
  back.forEach((entry, index) => {
    assert.equal(entry.name, story.files[index].name);
    assert.equal(entry.data.length, story.files[index].data.length);
  });
  assert.ok(dir);
});

test("a label that only numbers the part is not a title", () => {
  for (const [label, code] of [
    ["Chapitre 1", "fr"],
    ["CHAPITRE IV", "fr"],
    ["Chapitre 1er", "fr"],
    ["Part 2", "en"],
    ["第 1 章", "zh"],
    ["الفصل 2", "ar"],
    ["Kapitel 3", "de"],
    ["1", "en"],
  ]) {
    assert.equal(partTitle(label, chapterWords(code)), "", `${code}: ${label}`);
  }
});

test("a label with a name beside the number is the part's title", () => {
  for (const [label, code] of [
    ["Chapitre 1 : Le lapin", "fr"],
    ["La fee des bois", "fr"],
    ["Kapitel 3 - Der Wald", "de"],
    ["Chapter 2: The invisible dog", "en"],
  ]) {
    assert.equal(partTitle(label, chapterWords(code)), label, `${code}: ${label}`);
  }
});

export { PACK };
