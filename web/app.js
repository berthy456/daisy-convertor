import { CUT_LEVELS, cutChoices, extractBook, languageSample } from "./daisy.js";
import {
  CROP_FOCUS,
  MAX_ZOOM,
  CONTRAST_LIMITS,
  MIN_ZOOM,
  cropRect,
  drawPreview,
  loadCoverFile,
  loadCoverUrl,
  rle4Bitmap,
  screen,
  searchUrls,
  squareJpeg,
} from "./cover.js";
import { findCover } from "./covers.js";
import { CAPTION_FORMATS, CARD_SNIPPET_SECONDS, buildStoryZip, partTitle } from "./convert.js";
import { LANGUAGES, NAMES, apply, chapterWords, pickLanguage, storyLanguage, text } from "./i18n.js";
import { buildPlainPk, buildStudioZip, packName } from "./lunii.js";
import { buildMerlinFiles } from "./merlin.js";
import { buildPlaylistZip } from "./playlist.js";
import { cardAudio } from "./voices.js";
import { readZip, writeZip } from "./zip.js";

const drop = document.getElementById("drop");
const picker = document.getElementById("picker");
const folderPicker = document.getElementById("folder-picker");
const saveButton = document.getElementById("save");
const testButton = document.getElementById("test");
const downloadLink = document.getElementById("download");
const summary = document.getElementById("summary");
const bar = document.getElementById("bar");
const logView = document.getElementById("log");
const langSelect = document.getElementById("lang");
const coverPicker = document.getElementById("cover-picker");
const coverUrl = document.getElementById("cover-url");
const coverClear = document.getElementById("cover-clear");
const coverAuthor = document.getElementById("cover-author");
const coverTitle = document.getElementById("cover-title");
const searchDdg = document.getElementById("cover-search-ddg");
const searchGoogle = document.getElementById("cover-search-google");
const searchApp = document.getElementById("cover-search-app");
const coverSpinner = document.getElementById("cover-spinner");
const coverSearching = document.getElementById("cover-searching");
const coverStop = document.getElementById("cover-stop");
const cutInputs = [...document.querySelectorAll('input[name="cut"]')];
const cropCanvas = document.getElementById("crop-canvas");
const cropInputs = [...document.querySelectorAll('input[name="crop"]')];
const cropZoom = document.getElementById("crop-zoom");
const cropContrast = document.getElementById("crop-contrast");
const cropReset = document.getElementById("crop-reset");
// One box per field, on the line that asks for it: the four a card can carry, and
// the two a card can say around its number.
const cardTextBoxes = {
  title: document.getElementById("card-text-title"),
  chapter: document.getElementById("card-text-chapter"),
  number: document.getElementById("card-text-number"),
  total: document.getElementById("card-text-total"),
};
const cardSayBoxes = {
  word: document.getElementById("card-say-word"),
  count: document.getElementById("card-say-count"),
};
const cardLanguageSelect = document.getElementById("card-language");
const cardAudioSelect = document.getElementById("card-audio");
const cardSecondsInput = document.getElementById("card-seconds");
const formatInputs = [...document.querySelectorAll('input[name="format"]')];
const coverSection = document.getElementById("cover-section");
const negativeRow = document.getElementById("card-negative-row");
const negativeBox = document.getElementById("card-negative");
const captionsRow = document.getElementById("captions-row");
const captionsBox = document.getElementById("captions");
const partsBox = document.getElementById("parts");
const partsList = document.getElementById("parts-list");
const partsNote = document.getElementById("parts-note");
const partsAll = document.getElementById("parts-all");
const packLabel = document.getElementById("pack-name");
const captionsNote = document.getElementById("captions-note");
const negativeNote = document.getElementById("negative-note");
const testPlayer = document.getElementById("test-player");
const testFrame = document.getElementById("test-frame");

const silentMp3 = new Uint8Array(await (await fetch("./silence.mp3")).arrayBuffer());
const STORED_LANGUAGE = "daisy2telmi.language";
const STORED_CUT = "daisy2telmi.cut";
const STORED_FORMAT = "daisy2telmi.format";
const STORED_CROP = "daisy2telmi.crop";
const STORED_CARDS = "daisy2telmi.cards";
const STORED_CARD_LANGUAGE = "daisy2telmi.cardLanguage";
const STORED_NEGATIVE = "daisy2telmi.negative";
const STORED_CAPTIONS = "daisy2telmi.captions";
const STORED_CONTRAST = "daisy2telmi.contrast";
// What a card plays: one of the recorded clips, the part's own opening, or nothing.
const CARD_SOURCES = ["voice", "snippet", "off"];
const CARD_SECONDS_LIMITS = [0.5, 30];
// The summary lists the parts it starts; a book cut to the sentence level has hundreds.
const LISTED_PARTS = 30;
// A sentence-level book can run to thousands of parts: past this the page says
// how many there are instead of drawing a thousand checkboxes.
const LISTED_BOXES = 400;

// The crop box is drawn this wide, whatever the player's ratio. It is wider than
// any screen it previews, so a cover's own lettering can be read while it is placed.
const CROP_WIDTH = 720;

let source = null;
let cover = null;
// Whether the cover on the page came from a lookup or from the user: only the
// second one is a setting worth carrying to the next pack. The book's name the
// pack gave the two search boxes follows the same rule.
let coverAuto = false;
let autoTitle = "";
let autoAuthor = "";
let lang = "en";
let cut = "chapter";
let crop = "center";
let format = "telmi";
// Where the frame sits over the cover and how far it is enlarged. `focus` stays
// null while one of the three named bands is chosen, and takes over once the
// user moves the frame themselves.
let focus = null;
let zoom = 1;
// How far the cover's lights and shadows are pulled apart before it is drawn.
let contrast = 1;
// What the card between two parts shows and says. `title`, `chapter`, `number` and
// `total` are the words drawn on the picture; `word` and `count` are the ones spoken
// around the part's number; `audio` is what the card plays — a recorded phrase, the
// part's own opening, or nothing.
let cards = {
  title: false,
  chapter: true,
  number: true,
  total: true,
  word: true,
  count: false,
  audio: "voice",
  seconds: CARD_SNIPPET_SECONDS,
};
// The language the cards are written and spoken in: null leaves it to the book,
// whose own language is read off the pack as soon as it is opened (`bookLang`).
let cardLang = null;
let bookLang = null;
// Whether the black-and-white Lunii cards are drawn as a negative of the cover.
let negative = false;
// Whether each part gets a picture of its own words, shown while its audio plays.
let captions = false;
// The cut levels the pack being read can be cut at, empty until it has been read.
let cutLevels = [];
// What the pack's own reading gave the crop preview: how many parts the chosen level
// has, and the name the first one carries. null until the pack has been read.
let previewCard = null;
// Part numbers left out of the story, in the numbering of the chosen cut level.
let dropped = new Set();
// Counts the lookups in flight, so an answer about a pack that has been replaced
// is thrown away instead of landing in the cover box.
let coverJob = 0;
// The lookup the page started on its own, so a conversion can wait for it: a cover
// that lands after the story was built would make that story out of date.
let coverLookup = Promise.resolve();
// The link the box was left holding, once it has been asked for: the same link is
// not fetched twice for one pack, whatever asked for it.
let coverLink = "";
// The pack a test just played, kept so Save can hand it over without building the
// story a second time.
let tested = null;
// The zip the last download link pointed at. A browser keeps an object URL's bytes
// until it is revoked, and a big book's zip is tens of megabytes.
let downloadUrl = null;
// The story the player overlay is showing, or null when it is shut.
let playing = null;
// How many covers came and went: a bitmap cannot be compared to its earlier self,
// so the settings key counts the changes instead.
let coverStamp = 0;

const t = (key, values) => text(lang, key, values);

function log(line) {
  logView.textContent += `${line}\n`;
  logView.scrollTop = logView.scrollHeight;
}

function saved(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function remember(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // A blocked or full localStorage only costs the saved choice.
  }
}

function startLanguage() {
  for (const code of LANGUAGES) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = NAMES[code];
    langSelect.append(option);
  }
  lang = apply(pickLanguage(saved(STORED_LANGUAGE), navigator.language));
  langSelect.value = lang;
  langSelect.addEventListener("change", () => {
    lang = apply(langSelect.value);
    remember(STORED_LANGUAGE, lang);
  });
}

function startCut() {
  const stored = saved(STORED_CUT);
  cut = cutInputs.some((input) => input.value === stored) ? stored : "chapter";
  for (const input of cutInputs) {
    input.checked = input.value === cut;
    input.addEventListener("change", () => {
      cut = input.value;
      remember(STORED_CUT, cut);
      showCaptions();
      showParts();
    });
  }
}

// The two answers every card field is asked for: on the picture it is drawn or
// left out, in the phrase it is said or held back.
const CARD_DRAWN = [
  ["cards_show", true],
  ["cards_hide", false],
];
const CARD_SPOKEN = [
  ["cards_say", true],
  ["cards_silent", false],
];

function startCards() {
  const stored = JSON.parse(saved(STORED_CARDS) ?? "null");
  for (const [name, box] of Object.entries(cardTextBoxes)) {
    if (stored && typeof stored[name] === "boolean") cards[name] = stored[name];
    startCardChoice(box, CARD_DRAWN, cards[name], (on) => {
      cards[name] = on;
      if (name === "number") showCardTotal();
      remember(STORED_CARDS, JSON.stringify(cards));
      drawCrop();
    });
  }
  for (const [name, box] of Object.entries(cardSayBoxes)) {
    if (stored && typeof stored[name] === "boolean") cards[name] = stored[name];
    startCardChoice(box, CARD_SPOKEN, cards[name], (on) => {
      cards[name] = on;
      remember(STORED_CARDS, JSON.stringify(cards));
    });
  }
  showCardTotal();
  if (stored && CARD_SOURCES.includes(stored.audio)) cards.audio = stored.audio;
  cardAudioSelect.value = cards.audio;
  cardAudioSelect.addEventListener("change", () => {
    cards.audio = cardAudioSelect.value;
    showCardSeconds();
    remember(STORED_CARDS, JSON.stringify(cards));
  });
  showCardSeconds();
  cards.seconds = cardSeconds(stored?.seconds);
  cardSecondsInput.value = cards.seconds;
  cardSecondsInput.addEventListener("change", () => {
    cards.seconds = cardSeconds(cardSecondsInput.value);
    cardSecondsInput.value = cards.seconds;
    remember(STORED_CARDS, JSON.stringify(cards));
  });
}

/** One field's box: the two answers it offers, and the one it was left on. */
function startCardChoice(box, answers, value, choose) {
  for (const [key, on] of answers) {
    const option = document.createElement("option");
    option.value = String(on);
    option.dataset.i18n = key;
    option.textContent = t(key);
    box.append(option);
  }
  box.value = String(Boolean(value));
  box.addEventListener("change", () => choose(box.value === "true"));
}

/** The total is the number's tail: a card that draws no number has no total to draw. */
function showCardTotal() {
  cardTextBoxes.total.disabled = !cards.number;
}

/** The extract length belongs to the one card audio that plays the part's own opening. */
function showCardSeconds() {
  cardSecondsInput.disabled = cards.audio !== "snippet";
}

/** The card audio length as typed: a blank or unreadable box keeps the default. */
function cardSeconds(value) {
  const typed = Number(value);
  if (!Number.isFinite(typed)) return CARD_SNIPPET_SECONDS;
  const [low, high] = CARD_SECONDS_LIMITS;
  return Math.min(Math.max(typed, low), high);
}

/**
 * The language the cards are written and spoken in. Left on Auto it follows the
 * book: a French book on an English page gets French cards, which is the only way
 * the two can agree. Pinned, it stays whatever was picked, whatever the pack says.
 */
function startCardLanguage() {
  const auto = document.createElement("option");
  auto.value = "";
  cardLanguageSelect.append(auto);
  for (const code of LANGUAGES) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = NAMES[code];
    cardLanguageSelect.append(option);
  }
  cardLang = LANGUAGES.includes(saved(STORED_CARD_LANGUAGE)) ? saved(STORED_CARD_LANGUAGE) : null;
  cardLanguageSelect.value = cardLang ?? "";
  showCardLanguage();
  cardLanguageSelect.addEventListener("change", () => {
    cardLang = cardLanguageSelect.value || null;
    if (cardLang === null) cardLanguageSelect.value = "";
    remember(STORED_CARD_LANGUAGE, cardLang ?? "");
    showCardLanguage();
    drawCrop();
  });
}

// The Auto line names the language the last pack was read as, so the page says
// what it is about to do rather than leaving it to be guessed.
function showCardLanguage() {
  const auto = cardLanguageSelect.firstElementChild;
  auto.textContent = bookLang ? `${t("speech_auto")} — ${NAMES[bookLang]}` : t("speech_auto");
}

/** The language a card's words and its clip are asked for. */
const cardLanguage = () => cardLang ?? bookLang ?? lang;

function showCaptions() {
  // Only a Telmi or a Flam screen holds a sentence; the choice stays put while
  // another player is chosen.
  captionsRow.hidden = !CAPTION_FORMATS.includes(format);
  captionsNote.hidden = captionsRow.hidden;
  // A box that could draw nothing but the card's own picture is not worth offering.
  captionsBox.disabled = cut !== "sentence" || !cutLevels.includes("sentence");
}

function startCaptions() {
  captions = saved(STORED_CAPTIONS) === "1";
  captionsBox.checked = captions;
  captionsBox.addEventListener("change", () => {
    captions = captionsBox.checked;
    remember(STORED_CAPTIONS, captions ? "1" : "0");
  });
  showCaptions();
}

function showFormat() {
  // Only the plain Lunii screen has no colour, so only its cards can be read the
  // other way round; the choice stays put while another player is chosen.
  negativeRow.hidden = format !== "lunii";
  negativeNote.hidden = negativeRow.hidden;
  // A plain player is handed no pictures at all, so there is no cover to frame and
  // no story for the drawn machine to play.
  coverSection.hidden = format === "mp3";
  testButton.hidden = format === "mp3";
  showCaptions();
}

function startFormat() {
  const stored = saved(STORED_FORMAT);
  format = formatInputs.some((input) => input.value === stored) ? stored : "telmi";
  for (const input of formatInputs) {
    input.checked = input.value === format;
    input.addEventListener("change", () => {
      format = input.value;
      remember(STORED_FORMAT, format);
      showFormat();
      drawCrop();
    });
  }
  showFormat();
}

function startNegative() {
  negative = saved(STORED_NEGATIVE) === "1";
  negativeBox.checked = negative;
  negativeBox.addEventListener("change", () => {
    negative = negativeBox.checked;
    remember(STORED_NEGATIVE, negative ? "1" : "0");
    drawCrop();
  });
}

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/** The frame's place over the cover: the chosen band until the user moves it. */
function currentFocus() {
  return focus ?? CROP_FOCUS[crop];
}

function cropView() {
  return { crop, zoom, focus };
}

/**
 * The crop box drawn at the chosen player's ratio: what is inside this frame is
 * what the story will carry, so the choice is made on the picture rather than
 * described to it.
 *
 * The frame is drawn with no cover too: the shape a cover has to be fitted into is
 * worth seeing before one exists.
 */
/** The words a card carries: its number, its total, and the word for the part when
    the book was cut by chapter. */
function cardLabel(number, total, worded) {
  if (!cards.number) return "";
  const key = cards.total
    ? worded && cards.chapter
      ? "card_label"
      : "card_number"
    : worded && cards.chapter
      ? "card_label_one"
      : "card_number_one";
  return text(cardLanguage(), key, { n: number, total });
}

/**
 * The words the book's first card will carry, drawn on the crop frame.
 *
 * Only a chapter-level cut has chapter names and a chapter count to letter a card
 * with, and only the pack's own reading says what they are: until the pack has been
 * read, the card carries its number alone.
 */
function previewLabel() {
  if (format === "lunii") return "1";
  const label = cardLabel(1, previewCard?.count ?? 1, cut === "chapter");
  if (!cards.title || cut !== "chapter" || !previewCard) return label;
  const title = partTitle(previewCard.title, chapterWords(cardLanguage()));
  return title ? (label ? `${label}, ${title}` : title) : label;
}

function drawCrop() {
  if (format === "mp3") return;
  const [scene] = screen(format);
  const width = CROP_WIDTH;
  const height = Math.round((width * scene[1]) / scene[0]);
  if (cropCanvas.width !== width || cropCanvas.height !== height) {
    cropCanvas.width = width;
    cropCanvas.height = height;
  }
  drawPreview(
    cropCanvas.getContext("2d"),
    cover,
    width,
    height,
    cropView(),
    format === "lunii" ? "bw" : "colour",
    negative,
    contrast,
    previewLabel(),
  );
}

/** A drag moves the cover, so the frame's place moves the other way. The canvas
 * is scaled down by CSS on a narrow screen, hence the divide. */
function moveFocus(from, dx, dy) {
  const width = cropCanvas.width;
  const height = cropCanvas.height;
  const scale = cropCanvas.clientWidth / width || 1;
  const [, , drawnWidth, drawnHeight] = cropRect(cover, width, height, { focus: from, zoom });
  const out = [...from];
  if (drawnWidth > width) out[0] = clamp(out[0] - dx / scale / (drawnWidth - width), 0, 1);
  if (drawnHeight > height) out[1] = clamp(out[1] - dy / scale / (drawnHeight - height), 0, 1);
  return out;
}

function setZoom(value) {
  zoom = clamp(value, MIN_ZOOM, MAX_ZOOM);
  cropZoom.value = String(zoom);
  drawCrop();
}

/** A notch or a key scales the zoom, and zero scales to zero: from the whole cover
 * the only way out is a step in, and there is nothing below it to step down to. */
function stepZoom(factor) {
  if (zoom) setZoom(zoom * factor);
  else if (factor > 1) setZoom(0.25);
}

/** A moved frame answers to no band, so the three radios let go of it. */
function setFocus(next) {
  focus = next;
  for (const input of cropInputs) input.checked = false;
  drawCrop();
}

function moveFrame(dx, dy) {
  setFocus(moveFocus(currentFocus(), dx, dy));
}

function resetCrop() {
  crop = "center";
  focus = null;
  zoom = 1;
  cropZoom.value = "1";
  for (const input of cropInputs) input.checked = input.value === crop;
  remember(STORED_CROP, crop);
  drawCrop();
}

function startCrop() {
  const stored = saved(STORED_CROP);
  crop = cropInputs.some((input) => input.value === stored) ? stored : "center";
  for (const input of cropInputs) {
    input.checked = input.value === crop;
    input.addEventListener("change", () => {
      crop = input.value;
      focus = null;
      remember(STORED_CROP, crop);
      drawCrop();
    });
  }
  drawCrop();
  cropZoom.min = String(MIN_ZOOM);
  cropZoom.max = String(MAX_ZOOM);
  cropZoom.addEventListener("input", () => setZoom(Number(cropZoom.value)));
  cropReset.addEventListener("click", resetCrop);

  contrast = Number(saved(STORED_CONTRAST)) || 1;
  contrast = clamp(contrast, CONTRAST_LIMITS[0], CONTRAST_LIMITS[1]);
  cropContrast.value = String(contrast);
  cropContrast.addEventListener("input", () => {
    contrast = clamp(Number(cropContrast.value), CONTRAST_LIMITS[0], CONTRAST_LIMITS[1]);
    remember(STORED_CONTRAST, String(contrast));
    drawCrop();
  });

  let drag = null;
  cropCanvas.addEventListener("pointerdown", (event) => {
    if (!cover) return;
    cropCanvas.setPointerCapture(event.pointerId);
    drag = { x: event.clientX, y: event.clientY, focus: currentFocus() };
  });
  // A drag reports how far the pointer travelled from where it went down, so it
  // is measured against the frame as it was then, not as each move left it.
  cropCanvas.addEventListener("pointermove", (event) => {
    if (!drag) return;
    setFocus(moveFocus(drag.focus, event.clientX - drag.x, event.clientY - drag.y));
  });
  cropCanvas.addEventListener("pointerup", () => (drag = null));
  cropCanvas.addEventListener("pointercancel", () => (drag = null));
  cropCanvas.addEventListener(
    "wheel",
    (event) => {
      if (!cover) return;
      event.preventDefault();
      stepZoom(1 - event.deltaY / 400);
    },
    { passive: false },
  );
  // The keyboard reaches the same frame: the arrows move it, + and - enlarge it.
  cropCanvas.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: [1, 0], ArrowRight: [-1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[event.key];
    if (step) {
      event.preventDefault();
      moveFrame(step[0] * CROP_WIDTH / 10, step[1] * CROP_WIDTH / 10);
    } else if (event.key === "+" || event.key === "=") stepZoom(1.2);
    else if (event.key === "-") stepZoom(1 / 1.2);
  });
}

function setCover(bitmap, lookedUp = false) {
  if (cover) cover.close();
  cover = bitmap;
  coverAuto = lookedUp;
  coverStamp += 1;
  coverClear.hidden = false;
  log(t("log_cover_set", { w: bitmap.width, h: bitmap.height }));
  drawCrop();
}

function clearCover() {
  if (cover) cover.close();
  cover = null;
  coverAuto = false;
  coverStamp += 1;
  coverClear.hidden = true;
  coverUrl.value = "";
  coverLink = "";
  // The picture belongs to the cover that went away; the frame and the chosen band do not.
  focus = null;
  zoom = 1;
  cropZoom.value = "1";
  drawCrop();
}

/**
 * The cover the pack does not carry, asked for as soon as the pack is read. A
 * cover the user picked is never replaced by a suggestion, and a newer pack
 * cancels an answer that is still on its way.
 */
async function suggestCover() {
  const job = ++coverJob;
  if (cover) return;
  showSearching();
  let book = null;
  let found = { bitmap: null };
  try {
    book = extractBook(await packEntries(), cut);
    found = await findCover(book, location.href);
  } catch {
    found = { bitmap: null };
  }
  // Called off, or a newer pack took over: this answer is an old one.
  if (job !== coverJob) return;
  // A cover picked while the lookup ran wins over the suggestion, but either way
  // this lookup is over and the waiting goes away.
  showSearchDone();
  // The words the lookup asked with land in the two boxes, whether or not it found
  // anything: they are the words to correct when it did not.
  if (book) fillCoverWords(book);
  if (cover) return;
  // The service names are its own, so they are not translated, only appended.
  log(found.bitmap ? `${t("cover_found")} (${found.source})` : t("cover_none"));
  if (found.bitmap) setCover(found.bitmap, true);
}

/**
 * The picture a link typed in the box holds. The box is left as it was typed, and
 * the link is remembered, so the same one is not fetched twice for one pack.
 */
async function useCoverLink() {
  const link = coverUrl.value.trim();
  if (!/^https?:\/\//.test(link) || link === coverLink) return;
  coverLink = link;
  const bitmap = await loadCoverUrl(link, location.href);
  if (bitmap) setCover(bitmap);
  else log(t("log_cover_url"));
}

function showSearching() {
  coverSpinner.hidden = false;
  coverSearching.hidden = false;
  coverStop.hidden = false;
}

function showSearchDone() {
  coverSpinner.hidden = true;
  coverSearching.hidden = true;
  coverStop.hidden = true;
}

/**
 * Call the lookup off. It is already running and cannot be recalled, so it is
 * stopped by number: the answer it posts carries the number the ask started with,
 * and raising the current number makes that answer an old one.
 */
function stopCoverSearch() {
  coverJob += 1;
  showSearchDone();
  log(t("cover_stopped"));
}

/**
 * The two words typed in the boxes, asked of the same services the pack's own
 * metadata is asked of. A title the pack got wrong - a folder name, a producer's
 * truncation - is corrected here rather than left to a browser search.
 */
async function searchCoverWords() {
  const title = coverTitle.value.trim();
  if (!title) return;
  const job = ++coverJob;
  showSearching();
  let found = { bitmap: null };
  try {
    found = await findCover({ title, author: coverAuthor.value.trim() }, location.href);
  } catch {
    found = { bitmap: null };
  }
  if (job !== coverJob) return;
  showSearchDone();
  log(found.bitmap ? `${t("cover_found")} (${found.source})` : t("cover_none"));
  if (found.bitmap) setCover(found.bitmap, true);
}

function partSeconds(chapter) {
  return chapter.segments.reduce((sum, part) => sum + Math.max(0, (part.end ?? 0) - (part.begin ?? 0)), 0);
}

function formatSeconds(seconds) {
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/**
 * The chapters a sentence-cut book is made of: its parts in the order they play,
 * grouped by the chapter each was read under. Dropping one leaves out every
 * sentence it owns, which is what a listener trims a book with.
 */
function chapterRows(parts) {
  const rows = [];
  parts.forEach((part, index) => {
    const last = rows[rows.length - 1];
    if (last && last.chapter === part.chapter) {
      last.numbers.push(index + 1);
      last.seconds += partSeconds(part);
      return;
    }
    rows.push({
      chapter: part.chapter,
      numbers: [index + 1],
      title: part.chapterTitle || `Part ${rows.length + 1}`,
      seconds: partSeconds(part),
    });
  });
  return rows;
}

/**
 * The parts the pack was cut into, each with a box to leave it out. The numbers
 * belong to the chosen level, so changing level redraws the list and forgets the
 * parts left out of the old one. At the sentence level the boxes are the chapters:
 * a book cut to sentences runs to thousands of parts, and no one trims it one
 * sentence at a time.
 */
async function showParts() {
  if (!source) return;
  dropped.clear();
  partsAll.hidden = true;
  partsList.textContent = "";
  try {
    const { chapters } = extractBook(await packEntries(), cut);
    previewCard = { count: chapters.length, title: chapters[0]?.title ?? "" };
    drawCrop();
    const rows =
      cut === "sentence"
        ? chapterRows(chapters)
        : chapters.map((chapter, index) => ({ numbers: [index + 1], title: chapter.title, seconds: partSeconds(chapter) }));
    if (rows.length > LISTED_BOXES) {
      partsNote.textContent = t("parts_too_many", { n: chapters.length });
      partsBox.hidden = false;
      return;
    }
    partsNote.textContent = "";
    rows.forEach((row, index) => {
      const line = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = true;
      box.addEventListener("change", () => {
        for (const number of row.numbers) {
          if (box.checked) dropped.delete(number);
          else dropped.add(number);
        }
        partsAll.hidden = dropped.size === 0;
      });
      const title = document.createElement("span");
      title.textContent = `${index + 1}. ${row.title}`;
      const length = document.createElement("span");
      length.className = "muted";
      length.textContent = formatSeconds(row.seconds);
      line.append(box, title, length);
      partsList.append(line);
    });
    partsBox.hidden = false;
  } catch {
    previewCard = null;
    partsBox.hidden = true;
  }
}

function keepAllParts() {
  for (const box of partsList.querySelectorAll("input")) box.checked = true;
  dropped.clear();
  partsAll.hidden = true;
}

/** The two search links carry the author and the title typed in the boxes; with
    nothing typed, they carry the name the pack was uploaded under. */
function showCoverSearch(fallback = "") {
  const words = [coverAuthor.value.trim(), coverTitle.value.trim()].filter(Boolean).join(" ") || fallback;
  const links = searchUrls(words);
  searchDdg.href = links.ddg;
  searchGoogle.href = links.google;
  searchDdg.hidden = !words;
  searchGoogle.hidden = !words;
  // The app's own search needs a title: the catalogues it asks answer by words.
  searchApp.hidden = !coverTitle.value.trim();
}

function showSummary(story) {
  const parts = story.book.chapters;
  const listed = parts.slice(0, LISTED_PARTS).map((part, index) => `${index + 1}. ${part.title}`).join("\n");
  const more = parts.length > LISTED_PARTS ? `\n${t("summary_more", { n: parts.length - LISTED_PARTS })}` : "";
  summary.textContent = `${story.dirName}\n${t("log_chapters", { n: parts.length })}\n${listed}${more}`;
  summary.hidden = false;
  fillCoverWords(story.book);
}

/** The words a book is known by, left in the two boxes so a search can be asked
    again with them corrected. Only the boxes no one typed in are filled. */
function fillCoverWords(book) {
  if (!coverTitle.value.trim()) {
    coverTitle.value = book.title || "";
    autoTitle = coverTitle.value;
  }
  if (!coverAuthor.value.trim() && book.author) {
    coverAuthor.value = book.author;
    autoAuthor = coverAuthor.value;
  }
  showCoverSearch();
}

function clearResult() {
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  downloadUrl = null;
  downloadLink.hidden = true;
  downloadLink.removeAttribute("href");
}

/** Everything a build reads from the page. Two builds with the same key write the
    same story, so the second one is not worth waiting for. */
function settingsKey() {
  return JSON.stringify([
    coverStamp,
    cut,
    format,
    crop,
    zoom,
    focus,
    negative,
    contrast,
    captionsOn(),
    lang,
    cards.title,
    cards.chapter,
    cards.number,
    cards.total,
    cards.word,
    cards.count,
    cards.audio,
    cards.seconds,
    [...dropped].sort((a, b) => a - b),
  ]);
}

/** The story and the archive the chosen player takes. */
async function buildPack() {
  bar.hidden = false;
  bar.value = 0;

  const entries = await packEntries();
  log(t("log_files", { n: entries.length }));
  const progress = (label, done, total) => {
    bar.value = done / total;
    log(label);
  };
  const spoken = async (number, count) => {
    if (cards.audio === "off") return null;
    const voice = await cardAudio(cardLanguage(), number, {
      word: cards.word,
      count: cards.count ? count : null,
    });
    if (voice === null) log(t("log_card_silent", { n: number }));
    return voice;
  };
  // An mp3 pack has no cards and no story graph, so it is written on its own.
  const story =
    format === "mp3"
      ? await buildPlaylistZip(entries, progress, {
          cut,
          drop: [...dropped],
          t: (key) => t(key),
          cardAudio: spoken,
        })
      : await buildStoryZip(entries, silentMp3, progress, {
          pack: source.name,
          cover,
          crop,
          zoom,
          focus,
          cut,
          drop: [...dropped],
          player: format,
          // The plain Lunii screen has no colour: its cards go to its own sixteen
          // greys. A commercial `.pk` keeps each picture's own sixteen colours.
          style: format === "lunii" ? "bw" : "colour",
          negative,
          contrast,
          captions: captionsOn(),
          cardTitle: cards.title,
          t: (key) => t(key),
          cardLabel,
          cardAudio: spoken,
          cardAudioSource: cards.audio,
          cardSeconds: cards.seconds,
          chapterWords: chapterWords(cardLanguage()),
        });

  // One archive, for the player chosen: a Lunii takes the STUdio form of the same
  // story, or the layout its card keeps itself; the Flam launcher imports the Telmi
  // one; and a Merlin card holds no story graph at all — only the files and the
  // playlist that names them.
  let name = `${story.dirName}-${format}.zip`;
  let bytes = story.zip;
  if (format === "lunii") {
    name = `${story.dirName}-lunii.zip`;
    bytes = await buildStudioZip(story);
  } else if (format === "lunii-pk") {
    name = packName(story.metadata.title, story.metadata.uuid);
    bytes = await buildPlainPk(story, { picture: rle4Bitmap });
  } else if (format === "merlin") {
    name = `${story.dirName}_merlin.zip`;
    bytes = await writeZip(await buildMerlinFiles(story, { picture: squareJpeg, titles: story.titles }));
    log(t("log_merlin_beta"));
  }

  showSummary(story);
  return { name, bytes };
}

function offer(name, bytes) {
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  downloadUrl = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
  downloadLink.href = downloadUrl;
  downloadLink.download = name;
  downloadLink.hidden = false;
  bar.value = 1;
  log(t("log_ready", { name }));
  // The button asked for the file, so it arrives without a second click. The
  // link stays on screen in case the browser wants one.
  downloadLink.click();
}

/** One action: build the story for the chosen player and hand the zip over. */
async function saveStory() {
  if (!source) return;
  saveButton.disabled = true;
  clearResult();
  try {
    // A link still held by the box is fetched before anything is built: a story
    // built without the cover the user just pasted would be out of date at once.
    await useCoverLink();
    await coverLookup;
    const pack = tested && tested.key === settingsKey() ? tested : await buildPack();
    if (pack === tested) log(t("test_reuse"));
    offer(pack.name, pack.bytes);
  } catch (error) {
    log(t("log_error", { message: error.message ?? error }));
    bar.hidden = true;
  } finally {
    saveButton.disabled = false;
  }
}

/**
 * Ask where the story should go, build it, write it there, and only then open the
 * player over the page. The save dialog is asked for on the click itself: a browser
 * lets a page open one only while it is still handling that click, and a story takes
 * seconds to build.
 */
async function testStory() {
  if (!source) return;
  testButton.disabled = true;
  clearResult();
  const target = await saveTarget();
  if (target === null) {
    testButton.disabled = false;
    return;
  }
  try {
    await useCoverLink();
    await coverLookup;
    const key = settingsKey();
    const pack = tested && tested.key === key ? tested : await buildPack();
    tested = { key, ...pack };
    if (target) {
      await writeFile(target, pack);
      bar.value = 1;
      log(t("log_ready", { name: target.name }));
    } else {
      offer(pack.name, pack.bytes);
    }
    openPlayer(pack);
  } catch (error) {
    log(t("log_error", { message: error.message ?? error }));
    bar.hidden = true;
  } finally {
    testButton.disabled = false;
  }
}

/**
 * Where the story should be written, as a file handle the page can write to.
 * null: the user backed out of the dialog. undefined: this browser cannot ask, so
 * the story goes to its own download folder instead.
 */
async function saveTarget() {
  if (typeof window.showSaveFilePicker !== "function") return undefined;
  const base = source.name.replace(/\.[^.]+$/, "") || "story";
  try {
    return await window.showSaveFilePicker({
      suggestedName:
        format === "lunii-pk" ? `${base}.pk` : `${base}-${format}.zip`,
    });
  } catch (error) {
    if (error.name === "AbortError") return null;
    return undefined;
  }
}

async function writeFile(handle, pack) {
  const file = await handle.createWritable();
  await file.write(new Blob([pack.bytes], { type: "application/zip" }));
  await file.close();
}

/** The player, opened over the converter with the story it plays already built. */
function openPlayer(pack) {
  playing = pack;
  testFrame.src = "./emulator.html?test=1";
  testPlayer.showModal();
}

function send() {
  testFrame.contentWindow.postMessage(
    { type: "daisy2telmi-story", name: playing.name, bytes: playing.bytes },
    location.origin,
  );
}

// The player asks for the story it was opened to play, so nothing is uploaded and
// nothing is read back from the file it was just written. It asks as soon as it is
// loaded, which is usually before the story has been built.
window.addEventListener("message", (event) => {
  if (event.origin !== location.origin || !playing || event.source !== testFrame.contentWindow) return;
  if (event.data?.type === "daisy2telmi-ask") send();
  else if (event.data?.type === "daisy2telmi-close") testPlayer.close();
});

testPlayer.addEventListener("close", () => {
  playing = null;
  // A frame left loaded keeps playing the story it was handed.
  testFrame.src = "about:blank";
});

/** The pack read once: the cut levels need its outline, and the conversion needs the same files. */
function packEntries() {
  source.entries ??= source.loader();
  return source.entries;
}

/**
 * Open the cut levels this pack can cut at, once its outline has been read.
 * The counts come from the pack's own table of contents and its synced marks,
 * never from its audio, so a level the pack has nothing to cut at stays shut.
 */
async function openCutLevels() {
  for (const input of cutInputs) input.disabled = true;
  try {
    const entries = await packEntries();
    const books = CUT_LEVELS.map((level) => extractBook(entries, level));
    const choices = cutChoices(books.map((book) => book.chapters.length));
    if (!choices.includes(cut)) {
      cut = choices[Math.min(CUT_LEVELS.indexOf(cut), choices.length - 1)];
      remember(STORED_CUT, cut);
    }
    cutLevels = choices;
    showBookLanguage(books[CUT_LEVELS.indexOf(cut)] ?? books[0]);
    for (const input of cutInputs) {
      input.disabled = !choices.includes(input.value);
      input.checked = input.value === cut;
    }
    showCaptions();
    log(t("log_cut_levels", { levels: choices.map((level) => t(`cut_${level}`)).join(", ") }));
    await showParts();
  } catch (error) {
    log(t("log_error", { message: error.message ?? error }));
  }
}

/**
 * The language the book's cards will be written and spoken in, read off the pack as
 * soon as it is opened: its own metadata first, then the text it carries, then the
 * page's language — which is all that is left when a pack is audio alone. A language
 * the user pinned on the cards row stands over all three.
 */
function showBookLanguage(book) {
  const found = storyLanguage(book.language, languageSample(book), lang);
  bookLang = found.code;
  if (cardLang) {
    log(t("log_card_language", { language: NAMES[cardLang], source: t("source_pinned") }));
  } else {
    const source =
      found.source === "declared"
        ? t("source_declared", { declared: book.language.trim() })
        : t(`source_${found.source}`);
    log(t("log_card_language", { language: NAMES[found.code], source }));
  }
  showCardLanguage();
}

function setSource(name, loader) {
  // The cover and the book's name a lookup gave belong to the pack they were
  // looked up for; everything else the page holds is the user's own setting and stays.
  if (coverAuto) clearCover();
  if (coverTitle.value === autoTitle) coverTitle.value = "";
  if (coverAuthor.value === autoAuthor) coverAuthor.value = "";
  autoTitle = autoAuthor = "";
  coverLink = "";
  source = { name, loader, entries: null };
  // A pack that is not the one a test played cannot be the story that test showed.
  tested = null;
  // The last book's language says nothing about this one; the row goes back to
  // naming the page's own until this pack has been read.
  bookLang = null;
  showCardLanguage();
  saveButton.disabled = false;
  testButton.disabled = false;
  clearResult();
  // A dropped part is a number in one book's part list, and the Save button is
  // live again before the new pack's list has been read: clear them here, not in
  // the redraw that follows.
  dropped.clear();
  previewCard = null;
  partsBox.hidden = true;
  logView.textContent = "";
  packLabel.textContent = t("pack_loaded", { name });
  packLabel.hidden = false;
  showCoverSearch(name.replace(/\.[^.]+$/, ""));
  log(t("log_loaded", { name }));
  openCutLevels();
  coverLookup = suggestCover();
}

async function entriesFromZip(file) {
  return readZip(new Uint8Array(await file.arrayBuffer()));
}

async function entriesFromFolder(fileList) {
  const entries = [];
  for (const file of fileList) {
    entries.push({
      name: file.webkitRelativePath || file.name,
      data: new Uint8Array(await file.arrayBuffer()),
    });
  }
  return entries;
}

drop.addEventListener("dragover", (event) => {
  event.preventDefault();
  drop.classList.add("over");
});
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", async (event) => {
  event.preventDefault();
  drop.classList.remove("over");
  const file = event.dataTransfer.files[0];
  if (!file) return;
  if (!/\.(zip|epub)$/.test(file.name.toLowerCase())) {
    log(t("log_drop_zip"));
    return;
  }
  setSource(file.name, () => entriesFromZip(file));
});

picker.addEventListener("change", () => {
  const file = picker.files[0];
  if (file) setSource(file.name, () => entriesFromZip(file));
});

folderPicker.addEventListener("change", () => {
  if (folderPicker.files.length) setSource(`${folderPicker.files.length} files`, () => entriesFromFolder(folderPicker.files));
});

coverPicker.addEventListener("change", async () => {
  const file = coverPicker.files[0];
  if (!file) return;
  const bitmap = await loadCoverFile(file);
  if (bitmap) setCover(bitmap);
  else log(t("log_cover_bad"));
  coverPicker.value = "";
});

// The link is asked for as the box is left — Enter, or the next field — the way
// the desktop window reads its Image box.
coverUrl.addEventListener("change", useCoverLink);

coverClear.addEventListener("click", clearCover);
coverAuthor.addEventListener("input", () => showCoverSearch());
coverTitle.addEventListener("input", () => showCoverSearch());
coverStop.addEventListener("click", stopCoverSearch);
searchApp.addEventListener("click", searchCoverWords);
partsAll.addEventListener("click", keepAllParts);
saveButton.addEventListener("click", saveStory);
testButton.addEventListener("click", testStory);

startLanguage();
startFormat();
startNegative();
startCaptions();
startCut();
startCards();
startCardLanguage();
startCrop();
