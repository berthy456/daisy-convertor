// Chapter-card announcements, recorded ahead of time under web/voices/.
//
// A browser tab can play speech but cannot capture it into a file, so the page
// ships the phrases instead of synthesising them. `tools/make_web_voices.py`
// regenerates them with espeak-ng.
//
// What a card says is put together from those pieces: the number, with or
// without the word chapter, then the total in the language's own words, then
// the question that invites the listener to go on. The three pieces of the
// total come from the language's own "{n} of {total}" pattern, so a language
// that puts the word after the number, or before both, is recorded as it speaks.

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

/** One of the words around the numbers: "of", or what a language puts there. */
export function fragmentPath(language, which) {
  return `./voices/${language}/of-${which}.mp3`;
}

async function load(path) {
  if (clips.has(path)) return clips.get(path);
  const response = await fetch(path);
  if (!response.ok) return null;
  const data = new Uint8Array(await response.arrayBuffer());
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
    const before = await get(fragmentPath(language, "pre"));
    if (before !== null) pieces.push(before);
  }
  const said = word ? clipPath(language, number) : numberPath(language, number);
  const numberClip = said === null ? null : await get(said);
  if (numberClip === null) return null;
  pieces.push(numberClip);

  const totalPath = count === null ? null : numberPath(language, count);
  if (totalPath !== null) {
    // A language whose total needs no word of its own — English's " of " is the
    // only sound between them — still says the number.
    const middle = await get(fragmentPath(language, "mid"));
    const total = await get(totalPath);
    if (total !== null) {
      if (middle !== null) pieces.push(middle);
      pieces.push(total);
      const after = await get(fragmentPath(language, "post"));
      if (after !== null) pieces.push(after);
    }
  }

  if (prompt) {
    const ask = await get(`./voices/${language}/next.mp3`);
    if (ask !== null) pieces.push(ask);
  }
  return pieces.length === 1 ? numberClip : concatMp3(pieces);
}
