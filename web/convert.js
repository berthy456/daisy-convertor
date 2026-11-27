// DAISY zip in, Telmi story zip out, entirely in the browser.

import { captionPng, cardPng, coverPng, screen } from "./cover.js";
import { extractBook, normalizePath } from "./daisy.js";
import { concatSegments, mp3Duration } from "./mp3.js";
import { placeholderPng } from "./png.js";
import { bookText, lrc, srt, timings } from "./playlist.js";
import { writeZip } from "./zip.js";
import { buildMetadata, buildNotes, buildNodes, storyDirName, storyUuid } from "./telmi.js";

const json = (value) => new TextEncoder().encode(`${JSON.stringify(value, null, 2)}\n`);
const utf8 = (value) => new TextEncoder().encode(value);

// A part cut below chapter level has no title that says which part it is, so its
// card plays the sound it opens with instead of a spoken number.
export const CARD_SNIPPET_SECONDS = 3;

// The formats whose screen is big enough to read a sentence on. A Lunii's is not,
// and a Merlin card has no screen at all.
export const CAPTION_FORMATS = ["telmi", "flam"];

export const PAUSE_LEVELS = ["sentence", "paragraph", "chapter", "never"];

/**
 * Which part's card waits for OK before its audio plays.
 *
 * A book cut to chapters stops at every part whatever was asked for. Below that, a
 * pause the cut cannot find — a paragraph pause in a book cut to sentences — falls
 * back to the chapter ends, which the cut keeps as each part's `chapter`.
 */
export function pausePoints(chapters, cut, pause) {
  if (pause === "never") return chapters.map(() => false);
  if (cut === "chapter" || pause === "sentence" || pause === cut) return chapters.map(() => true);
  return chapters.map((chapter, k) => k === 0 || chapter.chapter !== chapters[k - 1].chapter);
}

/**
 * The part's own name, or nothing when its label only numbers it.
 *
 * A DAISY outline labels its parts "Chapter 1", "Chapitre II", "第 1 章": the chapter
 * word and a number, which is what a card says by itself. A label with a word beside
 * them - "Chapitre 1 : Le lapin" - names the part, and is worth reading out.
 */
export function partTitle(title, words) {
  const known = new Set(words);
  let tokens = fold(title)
    .split(/[^\p{L}\p{N}_]+/u)
    .filter(Boolean);
  const numbered = tokens.some((token) => known.has(token));
  tokens = tokens.filter((token) => !known.has(token));
  if (!tokens.length) return "";
  const numbering = numbered ? /^\d\w*$|^[ivxlcdm]{1,7}$/iu : /^\d+$/u;
  return tokens.some((token) => !numbering.test(token)) ? title : "";
}

/** Lowercase and without accents: a label's spelling is not what matters here. */
function fold(text) {
  return text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .split(/\s+/u)
    .filter(Boolean)
    .join(" ")
    .replace(/(?<=\w)(?=\d)/gu, " ");
}

/** The first seconds of a part, cut at MP3 frame boundaries. */
export function firstSeconds(segments, bytesOf, seconds) {
  let budget = seconds;
  const kept = [];
  for (const segment of segments) {
    if (budget <= 0) break;
    const begin = segment.begin ?? 0;
    const end = Math.min(segment.end ?? begin + budget, begin + budget);
    kept.push(bytesOf({ ...segment, begin, end }));
    budget -= end - begin;
  }
  return concatSegments(kept);
}

/**
 * entries: [{name, data}] of a DAISY pack.
 * silentMp3: bytes used for title.mp3 when the pack has no spoken front matter.
 * onProgress(label, done, total): optional progress callback.
 * options.cut: "chapter", "paragraph" or "sentence" — where to cut the book.
 * options.cover: an ImageBitmap to crop into every scene image.
 * options.crop: "top", "center" or "bottom" — the band of the cover to keep.
 * options.focus: [x, y] in 0..1, where the frame sits over the cover; it replaces
 *   the band once the user has moved it. options.zoom: 1..MAX_ZOOM.
 * options.style: "colour", or "bw" for a plain Lunii's sixteen greys.
 * options.negative: with "bw", swap light and dark, as an old film's negative reads.
 * options.contrast: how far the cover's lights and shadows are pulled apart.
 * options.player: "telmi", "lunii", "lunii-pk", "merlin" or "flam" — whose screen
 *   sizes every picture is drawn at.
 * options.cardLabel(number, total, worded): the words drawn on a card.
 * options.cardTitle: write each chapter's own title beside its number, when the card
 *   has room for it.
 * options.cardAudio(number, count): bytes for the card to say, or null to leave it silent.
 * options.cardAudioSource: "voice" (a recorded clip), "snippet" (the part's own opening)
 *   or "off" (a silent card). A part cut below chapter level always plays its opening.
 * options.cardSeconds: how many seconds of the part a card that plays its opening plays it for.
 * options.autoplay: run on to the next part when a card's audio ends, instead of waiting
 *   for OK. Without it the story waits at its chapters, whatever pauseAfter asks for.
 * options.pauseAfter: "sentence", "paragraph", "chapter" or "never" — where an
 *   autoplaying story waits for OK.
 * options.captions: give each part a picture of its own words, shown while its
 *   audio plays instead of the card's.
 * options.drop: 1-based part numbers to leave out of the story.
 */
export async function buildStoryFiles(entries, silentMp3, onProgress = () => {}, options = {}) {
  const cut = options.cut ?? "chapter";
  const book = extractBook(entries, cut);
  if (!book.chapters.length) throw new Error("no chapter with audio or text found in this pack");
  // A text-only DAISY pack or an EPUB has words and no sound. This page cuts audio
  // out of a pack; it cannot make any, so it says so rather than build a silent story.
  const silent = book.chapters.filter((chapter) => !chapter.segments.length);
  if (silent.length) {
    throw new Error(
      `${silent.length} part(s) of this pack have no audio, starting with "${silent[0].title}". ` +
        "This page only cuts the audio a pack already carries; the desktop app speaks their text instead.",
    );
  }
  const drop = new Set(options.drop ?? []);
  if (drop.size) book.chapters = book.chapters.filter((_, k) => !drop.has(k + 1));

  const uuid = await storyUuid(book);
  const dirName = storyDirName(book.title, uuid, 0, "", options.pack);
  const byline = book.author || book.narrator || "";
  const byName = new Map(entries.map((entry) => [normalizePath(entry.name), entry]));
  const bytesOf = (segment) => ({ ...segment, data: byName.get(segment.src).data });
  const files = [];
  const total = book.chapters.length + 3;
  const partCount = book.chapters.length;
  const cover = options.cover ?? null;
  const view = { crop: options.crop ?? "center", zoom: options.zoom ?? 1, focus: options.focus ?? null };
  const style = options.style ?? "colour";
  // The negative belongs to the greys of a Lunii behind its plastic window; a
  // colour card has nothing to invert.
  const negative = Boolean(options.negative) && style === "bw";
  const contrast = options.contrast ?? 1;
  const [scene, coverSize] = screen(options.player ?? "telmi");
  const cardLabel = options.cardLabel ?? ((number, count) => `Chapter ${number}/${count}`);
  const say = options.t ?? ((key) => key);
  const spoken = options.cardAudio ?? (async () => null);
  const source = options.cardAudioSource ?? "voice";
  const seconds = options.cardSeconds ?? CARD_SNIPPET_SECONDS;
  // A part's own words are only worth drawing when each part is one sentence, on a
  // screen big enough to read one on.
  const captioned = Boolean(options.captions) && cut === "sentence" && CAPTION_FORMATS.includes(options.player ?? "telmi");
  // A part cut below chapter level has no name of its own: the outline names the
  // chapter, and the part is a piece of it.
  const titled = Boolean(options.cardTitle) && cut === "chapter";
  const words = options.chapterWords ?? [];
  const titles = book.chapters.map((chapter) => partTitle(chapter.title, words));
  const autoplay = Boolean(options.autoplay);
  const pauses = pausePoints(book.chapters, cut, autoplay ? options.pauseAfter ?? "chapter" : "chapter");
  const cards = [];
  const scenes = [];
  let done = 0;

  const step = (label) => {
    done += 1;
    onProgress(label, done, total);
  };

  for (const [k, chapter] of book.chapters.entries()) {
    const audio = concatSegments(chapter.segments.map(bytesOf));
    files.push({ name: `${dirName}/audios/s${k}.mp3`, data: audio });
    files.push({
      name: `${dirName}/images/m${k}.png`,
      data: await cardPng(
        // A grey card is the Lunii's, and carries its number alone.
        style === "bw" ? String(k + 1) : cardLabel(k + 1, partCount, cut === "chapter"),
        chapter.text,
        cover,
        view,
        ...scene,
        style,
        negative,
        titled ? titles[k] : "",
        contrast,
      ),
    });

    // A part whose words cannot be drawn at all keeps the card it was read from.
    const caption = captioned ? await captionPng(chapter.text, ...scene, style, negative) : null;
    if (caption) {
      files.push({ name: `${dirName}/images/s${k}.png`, data: caption });
      scenes[k] = `s${k}.png`;
    }

    let card = null;
    // A part cut below chapter level has no name of its own to speak, so its card
    // plays its opening whatever the card audio was set to. A card the story runs
    // straight through has nothing to say at all.
    const plays = pauses[k] && source !== "off" && (source === "snippet" || cut !== "chapter");
    if (plays) {
      const snippet = firstSeconds(chapter.segments, bytesOf, seconds);
      // A part shorter than the snippet needs no second copy: its card plays the part.
      card = snippet.length === 0 ? null : snippet.length === audio.length ? `s${k}.mp3` : `c${k}.mp3`;
      if (card === `c${k}.mp3`) files.push({ name: `${dirName}/audios/c${k}.mp3`, data: snippet });
    } else if (pauses[k] && source !== "off") {
      const voice = await spoken(k + 1, partCount);
      if (voice !== null) {
        files.push({ name: `${dirName}/audios/c${k}.mp3`, data: voice });
        card = `c${k}.mp3`;
      }
    }
    cards[k] = card;

    // No story teller reads these. They are the pack's own words, kept beside the
    // audio they were read over, so the conversion does not throw the text away.
    const lines = timings(chapter, mp3Duration(audio), bytesOf);
    if (lines.length) {
      files.push({ name: `${dirName}/text/s${k}.lrc`, data: utf8(lrc(lines, book.title, byline)) });
      files.push({ name: `${dirName}/text/s${k}.srt`, data: utf8(srt(lines)) });
    }

    step(`${k + 1}/${partCount}: ${chapter.title}`);
  }

  const body = bookText(book);
  if (body) files.push({ name: `${dirName}/text/book.txt`, data: utf8(body) });

  files.push({
    name: `${dirName}/title.mp3`,
    data: book.frontMatter.length ? concatSegments(book.frontMatter.map(bytesOf)) : silentMp3,
  });
  step(say("prog_title_audio"));

  files.push({
    name: `${dirName}/title.png`,
    data: cover
      ? await coverPng(cover, ...scene, view, style, negative, contrast)
      : await placeholderPng(...scene, style, negative),
  });
  files.push({
    name: `${dirName}/cover.png`,
    data: cover
      ? await coverPng(cover, ...coverSize, view, style, negative, contrast)
      : await placeholderPng(...coverSize, style, negative),
  });
  step(say("prog_title_images"));

  const metadata = buildMetadata(book, uuid);
  const nodes = buildNodes(book.chapters.map((_, k) => `s${k}`), cards, scenes, pauses.map((stop) => !stop));
  files.push({ name: `${dirName}/metadata.json`, data: json(metadata) });
  files.push({ name: `${dirName}/nodes.json`, data: json(nodes) });
  files.push({ name: `${dirName}/notes.json`, data: json(buildNotes(book)) });
  step(say("prog_metadata"));

  return { dirName, uuid, book, files, metadata, nodes, titles };
}

export async function buildStoryZip(entries, silentMp3, onProgress, options) {
  const story = await buildStoryFiles(entries, silentMp3, onProgress, options);
  return { ...story, zip: await writeZip(story.files) };
}
