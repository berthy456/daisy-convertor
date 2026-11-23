// The mp3 playlist pack: one mp3 per chapter, the chapter's number spoken before
// each, the book's own text, four playlists, and the synced text as a lyric file
// and a subtitle file beside each chapter.
//
// This pack is not a story graph. It is a folder an ordinary player can open, so
// it draws no pictures and carries no nodes.

import { extractBook, normalizePath } from "./daisy.js";
import { concatSegments, mp3Duration } from "./mp3.js";
import { writeZip } from "./zip.js";

// Every player reads an M3U; `.m3u8` is the same file in UTF-8, which is what a
// title with accents needs. `.pls` and `.xspf` cover the players that want a
// playlist with real metadata rather than comments.
export const PLAYLISTS = ["m3u8", "m3u", "pls", "xspf"];

const UNSAFE = /[<>:"/\\|?*\u0000-\u001f]/g;

export function safeName(value) {
  const cleaned = value
    .replace(UNSAFE, " ")
    .replace(/\s{2,}/g, " ")
    .replace(/^[\s.]+|[\s.]+$/g, "");
  return cleaned || "story";
}

/**
 * `001 - Chapter title - Book title`: the number leads so a player that sorts by
 * name still plays the book in order, and the `-intro` interlude sorts in front
 * of the chapter it introduces.
 */
export function chapterName(number, title, bookTitle) {
  const parts = [String(number).padStart(3, "0")];
  if (title) parts.push(title);
  parts.push(bookTitle);
  return safeName(parts.join(" - "));
}

/** `1:23:45`, or `?` when the pack gave no length. */
export function clock(seconds) {
  if (seconds === null || seconds === undefined) return "?";
  const whole = Math.round(seconds);
  const hours = Math.floor(whole / 3600);
  const rest = whole - hours * 3600;
  const minutes = Math.floor(rest / 60);
  const secs = rest - minutes * 60;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}` : `${minutes}:${String(secs).padStart(2, "0")}`;
}

const display = (track, byline) => (byline ? `${byline} - ${track.title}` : track.title);

/** Extended M3U: `#EXTINF:seconds,Artist - Title` over the file name. */
export function m3u(tracks, byline) {
  const lines = ["#EXTM3U"];
  for (const track of tracks) {
    lines.push(`#EXTINF:${track.duration ? Math.trunc(track.duration) : -1},${display(track, byline)}`);
    lines.push(track.file);
  }
  return `${lines.join("\n")}\n`;
}

/** PLS v2: an .ini-shaped list, lengths in seconds. */
export function pls(tracks, byline) {
  const lines = ["[playlist]"];
  tracks.forEach((track, index) => {
    const n = index + 1;
    lines.push(`File${n}=${track.file}`);
    lines.push(`Title${n}=${display(track, byline)}`);
    lines.push(`Length${n}=${track.duration ? Math.trunc(track.duration) : -1}`);
  });
  lines.push(`NumberOfEntries=${tracks.length}`);
  lines.push("Version=2");
  return `${lines.join("\n")}\n`;
}

const escapeXml = (value) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** XSPF: the XML playlist, whose duration is in milliseconds. */
export function xspf(tracks, title, author) {
  const head = [`<?xml version="1.0" encoding="UTF-8"?>`, `<playlist version="1" xmlns="http://xspf.org/ns/0/">`, `  <title>${escapeXml(title)}</title>`];
  if (author) head.push(`  <creator>${escapeXml(author)}</creator>`);
  const items = tracks.map((track, index) => {
    const fields = [
      `    <location>${encodeURIComponent(track.file)}</location>`,
      `    <title>${escapeXml(track.title)}</title>`,
      `    <album>${escapeXml(title)}</album>`,
      `    <trackNum>${index + 1}</trackNum>`,
    ];
    if (author) fields.push(`    <creator>${escapeXml(author)}</creator>`);
    if (track.duration) fields.push(`    <duration>${Math.trunc(track.duration * 1000)}</duration>`);
    return `  <track>\n${fields.join("\n")}\n  </track>`;
  });
  return `${head.join("\n")}\n  <trackList>\n${items.join("\n")}\n  </trackList>\n</playlist>\n`;
}

/** What the pack says about itself, and what it turned into. */
export function details(book, chapters) {
  const lines = [book.title];
  for (const [label, value] of [
    ["Author", book.author],
    ["Narrator", book.narrator],
    ["Publisher", book.publisher],
    ["Language", book.language],
    ["Identifier", book.identifier],
    ["Source", book.source],
  ]) {
    if (value) lines.push(`${label}: ${value}`);
  }
  const known = chapters.map((track) => track.duration);
  lines.push(`Chapters: ${chapters.length}`);
  lines.push(`Length: ${known.every((d) => d) ? clock(known.reduce((a, b) => a + b, 0)) : clock(null)}`);
  lines.push("");
  chapters.forEach((track, index) => {
    lines.push(`${String(index + 1).padStart(3, "0")}  ${clock(track.duration)}  ${track.title}`);
  });
  return `${lines.join("\n")}\n`;
}

/** The book as plain text, chapter by chapter, from the pack's own words. */
export function bookText(book) {
  const blocks = [];
  book.chapters.forEach((chapter, index) => {
    const body = (chapter.body || chapter.text).trim();
    if (!body) return;
    const heading = chapter.title || String(index + 1);
    blocks.push(`${String(index + 1).padStart(3, "0")}  ${heading}\n${"-".repeat(40)}\n${body}`);
  });
  return blocks.length ? `${blocks.join("\n\n")}\n` : "";
}

const lrcClock = (seconds) => {
  const hundredths = Math.round(seconds * 100);
  const minutes = Math.floor(hundredths / 6000);
  const rest = hundredths - minutes * 6000;
  return `[${String(minutes).padStart(2, "0")}:${String(Math.floor(rest / 100)).padStart(2, "0")}.${String(rest % 100).padStart(2, "0")}]`;
};

const srtClock = (seconds) => {
  const millis = Math.round(seconds * 1000);
  const hours = Math.floor(millis / 3_600_000);
  let rest = millis - hours * 3_600_000;
  const minutes = Math.floor(rest / 60_000);
  rest -= minutes * 60_000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(Math.floor(rest / 1000)).padStart(2, "0")},${String(rest % 1000).padStart(3, "0")}`;
};

/**
 * Put the pack's synced spans on the timeline of the mp3 they were rendered into.
 *
 * The SMIL times are trims inside each source clip, and the chapter is those clips
 * end to end, so the offsets are their durations added up. The pack rounds its
 * times, so the sum lands a little short of the file that was written: scaling by
 * the measured length keeps the last line inside the chapter instead of after it.
 */
export function timings(chapter, length, bytesOf) {
  const spans = chapter.spans.filter((span) => span.text);
  if (!spans.length) return [];
  const probed = new Map();
  const starts = [];
  let offset = 0;
  for (const span of spans) {
    starts.push(offset);
    if (span.begin !== null && span.begin !== undefined && span.end !== null && span.end !== undefined) {
      offset += Math.max(span.end - span.begin, 0);
    } else {
      // A clip the pack never trimmed plays whole, so only its own length is known.
      if (!probed.has(span.src)) probed.set(span.src, mp3Duration(bytesOf({ src: span.src })) ?? 0);
      offset += probed.get(span.src);
    }
  }
  const scale = length && offset > 0 ? length / offset : 1;
  const scaled = starts.map((start) => start * scale);
  const ends = scaled.slice(1).concat([Math.max(length || offset, scaled[scaled.length - 1])]);

  // A pack that synced only its headings repeats the heading over every clip of the
  // level; those clips are one line of text, so they become one line covering them.
  const lines = [];
  spans.forEach((span, index) => {
    const text = span.text.replace(/\s+/g, " ");
    const last = lines[lines.length - 1];
    if (last && last.text === text) last.end = ends[index];
    else lines.push({ start: scaled[index], end: ends[index], text });
  });
  return lines;
}

/** LRC: one `[mm:ss.xx]` stamp per line, which is what a lyric screen reads. */
export function lrc(lines, title, byline) {
  const head = [`[ti:${title}]`];
  if (byline) head.push(`[au:${byline}]`);
  return `${head.concat(lines.map((line) => `${lrcClock(line.start)}${line.text}`)).join("\n")}\n`;
}

/** SubRip: the numbered block most video players already know how to show. */
export function srt(lines) {
  return `${lines.map((line, index) => `${index + 1}\n${srtClock(line.start)} --> ${srtClock(line.end)}\n${line.text}`).join("\n\n")}\n`;
}

const text = (value) => new TextEncoder().encode(value);

/**
 * entries: [{name, data}] of a DAISY pack.
 * onProgress(label, done, total): optional progress callback.
 * options.cut: where to cut the book. options.drop: 1-based part numbers to leave out.
 * options.cardAudio(number, count): bytes saying which chapter comes next, or null.
 *
 * The interlude says the number alone: this page has recorded numbers, not a voice
 * that can read a chapter's title.
 */
export async function buildPlaylistFiles(entries, onProgress = () => {}, options = {}) {
  const cut = options.cut ?? "chapter";
  const book = extractBook(entries, cut);
  if (!book.chapters.length) throw new Error("no chapter with audio or text found in this pack");
  const silent = book.chapters.filter((chapter) => !chapter.segments.length);
  if (silent.length) {
    throw new Error(
      `${silent.length} part(s) of this pack have no audio, starting with "${silent[0].title}". ` +
        "This page only cuts the audio a pack already carries; the desktop app speaks their text instead.",
    );
  }
  const drop = new Set(options.drop ?? []);
  if (drop.size) book.chapters = book.chapters.filter((_, k) => !drop.has(k + 1));

  const folder = safeName(book.title);
  const byName = new Map(entries.map((entry) => [normalizePath(entry.name), entry]));
  const bytesOf = (segment) => ({ ...segment, data: byName.get(segment.src).data });
  const byline = book.author || book.narrator || "";
  const say = options.t ?? ((key) => key);
  const spoken = options.cardAudio ?? (async () => null);
  const files = [];
  const total = book.chapters.length + 2;
  let done = 0;
  const step = (label) => {
    done += 1;
    onProgress(label, done, total);
  };

  const chapters = [];
  for (const [k, chapter] of book.chapters.entries()) {
    const name = chapterName(k + 1, chapter.title, folder);
    const audio = concatSegments(chapter.segments.map(bytesOf));
    const length = mp3Duration(audio);
    files.push({ name: `${folder}/${name}.mp3`, data: audio });
    chapters.push({ file: `${name}.mp3`, title: chapter.title || String(k + 1), duration: length });

    // Players look for these beside the audio: VLC and mpv pick up the `.srt`,
    // most lyric screens pick up the `.lrc`. A pack that synced nothing gets neither.
    const lines = timings(chapter, length, bytesOf);
    if (lines.length) {
      files.push({ name: `${folder}/${name}.lrc`, data: text(lrc(lines, book.title, byline)) });
      files.push({ name: `${folder}/${name}.srt`, data: text(srt(lines)) });
    }
    step(`${k + 1}/${book.chapters.length}: ${chapter.title}`);
  }

  // A spoken chapter name before every chapter, sorted in front of it by the `-intro`.
  const interludes = [];
  for (const [k, chapter] of book.chapters.entries()) {
    const voice = await spoken(k + 1, book.chapters.length);
    if (voice === null) continue;
    const stem = chapters[k].file.slice(0, -4);
    files.push({ name: `${folder}/${stem}-intro.mp3`, data: voice });
    interludes.push({ file: `${stem}-intro.mp3`, title: chapter.title || String(k + 1), duration: mp3Duration(voice) });
  }
  step(say("prog_interludes"));

  const order = chapters.concat(interludes).sort((a, b) => (a.file < b.file ? -1 : 1));
  files.push({ name: `${folder}/${folder}.txt`, data: text(details(book, chapters)) });
  const body = bookText(book);
  if (body) files.push({ name: `${folder}/${folder} - text.txt`, data: text(body) });
  for (const extension of PLAYLISTS) {
    const body = extension === "xspf" ? xspf(order, book.title, byline || null) : extension === "pls" ? pls(order, byline) : m3u(order, byline);
    files.push({ name: `${folder}/${folder}.${extension}`, data: text(body) });
  }
  step(say("prog_playlists"));

  return { dirName: folder, book, files };
}

export async function buildPlaylistZip(entries, onProgress, options) {
  const pack = await buildPlaylistFiles(entries, onProgress, options);
  return { ...pack, zip: await writeZip(pack.files) };
}
