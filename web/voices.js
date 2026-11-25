// Chapter-card announcements, recorded ahead of time under web/voices/.
//
// A browser tab can play speech but cannot capture it into a file, so the page
// ships the phrases instead of synthesising them. `tools/make_web_voices.py`
// regenerates them with Piper, Coqui, Chatterbox or espeak-ng. Chatterbox is the
// one engine that loses its place on a bare number, and only its numbers and the
// word before them are left to espeak-ng.
//
// What a card says is put together from those pieces: the number, with or
// without the word chapter, then the total in the language's own words, then
// the question that invites the listener to go on. The total is one recording
// per number — " of 20", " 章，共 20 章" — taken from the language's own
// "{n} of {total}" pattern, so a language that names the part after the total
// says it as it speaks.

import { concatMp3 } from "./mp3.js";

export const MAX_CARD = 50;

const clips = new Map();

function inRange(number) {
  return Number.isInteger(number) && number >= 1 && number <= MAX_CARD;
}

/** Path of the clip saying the chapter number, or null when it was never recorded. */
export function clipPath(language, number) {
  return inRange(number) ? `./voices/${language}/${String(number).padStart(2, "0")}.mp3` : null;
}

/** The number on its own, for a card that does not say the word chapter. */
export function numberPath(language, number) {
  return inRange(number) ? `./voices/${language}/n-${String(number).padStart(2, "0")}.mp3` : null;
}

/** The word a language puts before its chapter number, for a card that does not say it. */
export function prefixPath(language) {
  return `./voices/${language}/of-pre.mp3`;
}

/** The recording naming the total: " of 20", "，共 20 章". */
export function totalPath(language, count) {
  return inRange(count) ? `./voices/${language}/of-${String(count).padStart(2, "0")}.mp3` : null;
}

async function load(path) {
  if (clips.has(path)) return clips.get(path);
  const response = await fetch(path);
  // A missing clip is remembered too: English and Turkish have no word before
  // their number, so a card asked without the word would ask for of-pre.mp3 and
  // get a 404 on every single card.
  const data = response.ok ? new Uint8Array(await response.arrayBuffer()) : null;
  clips.set(path, data);
  return data;
}

/**
 * Bytes of the card announcement for chapter `number`, or null when the number
 * is beyond the recorded range. `word` says the word chapter, `count` adds how
 * many parts there are, and `prompt` the "next chapter?" question.
 */
export async function cardAudio(language, number, options = {}, get = load) {
  const { word = true, count = null, prompt = false } = options;
  const pieces = [];
  // A language that names the part before the number — "الفصل 3" — keeps that
  // word even when the card was asked not to say it, because without it the
  // phrase is not that language any more.
  if (!word) {
    const before = await get(prefixPath(language));
    if (before !== null) pieces.push(before);
  }
  const said = word ? clipPath(language, number) : numberPath(language, number);
  const numberClip = said === null ? null : await get(said);
  if (numberClip === null) return null;
  pieces.push(numberClip);

  const total = count === null ? null : totalPath(language, count);
  if (total !== null) {
    const named = await get(total);
    if (named !== null) pieces.push(named);
  }

  if (prompt) {
    const ask = await get(`./voices/${language}/next.mp3`);
    if (ask !== null) pieces.push(ask);
  }
  return pieces.length === 1 ? numberClip : concatMp3(pieces);
}
