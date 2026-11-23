// The mp3 playlist pack in the browser. Every file it writes is checked against
// what src/daisy2telmi/playlist.py writes, because the two builds have to hand the
// same folder to the same player.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));

const { readZip } = await import("../../web/zip.js");
const { chapterName, clock, bookText, buildPlaylistFiles, details, lrc, m3u, pls, safeName, srt, timings, xspf } =
  await import("../../web/playlist.js");

async function pack(name) {
  return readZip(new Uint8Array(await readFile(join(ROOT, name))));
}

const decode = (bytes) => new TextDecoder().decode(bytes);

const span = (text, index, length = 2) => ({ text, src: "a.mp3", begin: index * length, end: (index + 1) * length });

test("a file name keeps its accents and loses what a player cannot open", () => {
  assert.equal(safeName("L'aigui  :  creuse"), "L'aigui creuse");
  assert.equal(safeName("a<b>c/d|e?f*"), "a b c d e f");
  assert.equal(safeName(""), "story");
});

test("the chapter name leads with its number and ends with the book", () => {
  assert.equal(chapterName(1, "Chap 1", "L'aigui creuse"), "001 - Chap 1 - L'aigui creuse");
  assert.equal(chapterName(7, "", "Book"), "007 - Book");
});

test("the clock drops the hours it does not need", () => {
  assert.equal(clock(75), "1:15");
  assert.equal(clock(3735), "1:02:15");
  assert.equal(clock(null), "?");
});

test("the four playlists name the same files in the same order", () => {
  const tracks = [
    { file: "001 - Un - Livre.mp3", title: "Un", duration: 12.4 },
    { file: "001 - Un - Livre-intro.mp3", title: "Un", duration: null },
  ];
  const list = m3u(tracks, "Auteur");
  assert.equal(list.split("\n")[0], "#EXTM3U");
  assert.equal(list.split("\n")[1], "#EXTINF:12,Auteur - Un");
  assert.equal(list.split("\n")[3], "#EXTINF:-1,Auteur - Un");

  const ini = pls(tracks, "Auteur").split("\n");
  assert.equal(ini[0], "[playlist]");
  assert.equal(ini[1], "File1=001 - Un - Livre.mp3");
  assert.equal(ini[3], "Length1=12");
  assert.equal(ini[4], "File2=001 - Un - Livre-intro.mp3");
  assert.equal(ini[6], "Length2=-1");
  assert.equal(ini[7], "NumberOfEntries=2");
  assert.equal(ini[8], "Version=2");

  const xml = xspf(tracks, "Livre", "Auteur");
  assert.match(xml, /<playlist version="1" xmlns="http:\/\/xspf\.org\/ns\/0\/">/);
  // XSPF counts milliseconds where the other two count seconds.
  assert.match(xml, /<duration>12400<\/duration>/);
  assert.match(xml, /<location>001%20-%20Un%20-%20Livre\.mp3<\/location>/);
  assert.ok(xspf(tracks, "Livre <3>", null).includes("Livre &lt;3&gt;"), "the title is escaped");
  assert.ok(!xspf(tracks, "Livre", null).includes("<creator>"), "no author, no creator field");
});

test("the synced lines follow the clips they were read over", () => {
  const chapter = { spans: [span("Un agneau", 0), span("buvait", 1)] };
  const lines = timings(chapter, 4, () => new Uint8Array());
  assert.deepEqual(
    lines.map((line) => [line.start, line.end, line.text]),
    [
      [0, 2, "Un agneau"],
      [2, 4, "buvait"],
    ],
  );
});

test("the lines are stretched to the chapter that was written", () => {
  const chapter = { spans: [span("Un agneau", 0), span("buvait", 1)] };
  const lines = timings(chapter, 20, () => new Uint8Array());
  assert.equal(lines[1].start, 10);
  assert.equal(lines[1].end, 20);
});

test("a heading repeated over every clip of its level becomes one line", () => {
  const chapter = { spans: [span("Chap 1", 0), span("Chap 1", 1), span("Chap 1", 2)] };
  const lines = timings(chapter, 6, () => new Uint8Array());
  assert.deepEqual(
    lines.map((line) => [line.start, line.end, line.text]),
    [[0, 6, "Chap 1"]],
  );
});

test("a chapter with no synced text gets no lyric file", () => {
  assert.deepEqual(timings({ spans: [] }, 4, () => new Uint8Array()), []);
});

test("the lyric and the subtitle clocks", () => {
  const lines = [{ start: 0, end: 2.5, text: "Un agneau" }];
  assert.equal(lrc(lines, "Le loup", "La Fontaine"), "[ti:Le loup]\n[au:La Fontaine]\n[00:00.00]Un agneau\n");
  assert.equal(srt(lines), "1\n00:00:00,000 --> 00:00:02,500\nUn agneau\n");
});

test("the details file lists the pack's own words and each part's length", () => {
  const book = { title: "Livre", author: "Auteur", narrator: null, publisher: null, language: null, identifier: null, source: null };
  const text = details(book, [{ title: "Un", duration: 12 }, { title: "Deux", duration: 30 }]);
  const lines = text.split("\n");
  assert.equal(lines[0], "Livre");
  assert.equal(lines[1], "Author: Auteur");
  assert.equal(lines[2], "Chapters: 2");
  assert.equal(lines[3], "Length: 0:42");
  assert.equal(lines[5], "001  0:12  Un");
});

test("the book text keeps each part under its own number", () => {
  const book = { chapters: [{ title: "Un", body: "Premier.", text: "" }, { title: "", body: "", text: "Seul." }] };
  const text = bookText(book).split("\n");
  assert.equal(text[0], "001  Un");
  assert.equal(text[2], "Premier.");
  assert.equal(text[4], "002  2");
});

test("the pack writes one mp3 per chapter, its lyric files and four playlists", async () => {
  const entries = await pack("gon-ruby-mp3.zip");
  const { dirName, files } = await buildPlaylistFiles(entries, () => {}, { cut: "chapter", cardAudio: async () => null });
  const names = files.map((file) => file.name);

  assert.equal(dirName, "ごんぎつね");
  assert.ok(names.every((name) => name.startsWith(`${dirName}/`)), "everything sits in the book's folder");
  const chapters = names.filter((name) => name.endsWith(".mp3"));
  assert.ok(chapters.length > 1);
  assert.ok(chapters.every((name) => /^\d{3} - .+ - .+\.mp3$/.test(name.split("/")[1])), "each is numbered first");
  assert.ok(!names.some((name) => name.endsWith("-intro.mp3")), "no voice, no interlude");

  for (const chapter of chapters) {
    const stem = chapter.slice(0, -4);
    assert.ok(names.includes(`${stem}.lrc`), `${chapter} gets a lyric file`);
    assert.ok(names.includes(`${stem}.srt`), `${chapter} gets a subtitle file`);
  }
  for (const extension of ["m3u8", "m3u", "pls", "xspf"]) {
    assert.ok(names.includes(`${dirName}/${dirName}.${extension}`), `the ${extension} playlist`);
  }
  assert.ok(names.includes(`${dirName}/${dirName}.txt`), "the details");
  assert.ok(names.includes(`${dirName}/${dirName} - text.txt`), "the book as text");

  const lyric = decode(files.find((file) => file.name.endsWith(".lrc")).data).split("\n");
  const stamps = lyric.filter((line) => /^\[\d\d:\d\d\.\d\d].+$/.test(line));
  assert.ok(stamps.length > 10, "the pack synced every phrase, so the lyric has one line each");
  assert.ok(stamps[0].startsWith("[00:00.00]"), "the first line starts at the top of the chapter");

  const subtitle = decode(files.find((file) => file.name.endsWith(".srt")).data);
  assert.equal(subtitle.split("-->").length - 1, stamps.length, "the subtitle covers the same lines");
});

test("the interlude is named after the chapter it introduces", async () => {
  const entries = await pack("gon-ruby-mp3.zip");
  const voice = new Uint8Array([0xff, 0xfb, 0x00, 0x00]);
  const { files } = await buildPlaylistFiles(entries, () => {}, { cut: "chapter", cardAudio: async () => voice });
  const names = files.map((file) => file.name);
  const chapter = names.find((name) => /^\d{3} - .+ - .+\.mp3$/.test(name.split("/")[1]));
  assert.ok(names.includes(`${chapter.slice(0, -4)}-intro.mp3`), "the intro sorts in front of its chapter");
  const order = names.filter((name) => name.endsWith(".mp3")).sort();
  assert.equal(order[0], `${chapter.slice(0, -4)}-intro.mp3`, "and the playlist reads it first");
});
