import { CUT_LEVELS, cutChoices, extractBook } from "./daisy.js";
import {
  CROP_FOCUS,
  MAX_ZOOM,
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
import { CAPTION_FORMATS, buildStoryZip } from "./convert.js";
import { LANGUAGES, NAMES, apply, pickLanguage, text } from "./i18n.js";
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
const coverUse = document.getElementById("cover-use");
const coverClear = document.getElementById("cover-clear");
const coverAuthor = document.getElementById("cover-author");
const coverTitle = document.getElementById("cover-title");
const searchDdg = document.getElementById("cover-search-ddg");
const searchGoogle = document.getElementById("cover-search-google");
const coverSpinner = document.getElementById("cover-spinner");
const cutInputs = [...document.querySelectorAll('input[name="cut"]')];
const cropCanvas = document.getElementById("crop-canvas");
const cropInputs = [...document.querySelectorAll('input[name="crop"]')];
const cropZoom = document.getElementById("crop-zoom");
const cropReset = document.getElementById("crop-reset");
const cardBoxes = {
  total: document.getElementById("card-total"),
  word: document.getElementById("card-word"),
  count: document.getElementById("card-count"),
};
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

const silentMp3 = new Uint8Array(await (await fetch("./silence.mp3")).arrayBuffer());
const STORED_LANGUAGE = "daisy2telmi.language";
const STORED_CUT = "daisy2telmi.cut";
const STORED_FORMAT = "daisy2telmi.format";
const STORED_CROP = "daisy2telmi.crop";
const STORED_CARDS = "daisy2telmi.cards";
const STORED_NEGATIVE = "daisy2telmi.negative";
const STORED_CAPTIONS = "daisy2telmi.captions";
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
// What the card between two parts shows and says: the total on the picture, the
// word chapter in the phrase, and the total in it too.
let cards = { total: true, word: true, count: false };
// Whether the black-and-white Lunii cards are drawn as a negative of the cover.
let negative = false;
// Whether each part gets a picture of its own words, shown while its audio plays.
let captions = false;
// The cut levels the pack being read can be cut at, empty until it has been read.
let cutLevels = [];
// Part numbers left out of the story, in the numbering of the chosen cut level.
let dropped = new Set();
// Counts the lookups in flight, so an answer about a pack that has been replaced
// is thrown away instead of landing in the cover box.
let coverJob = 0;
// The pack a test just played, kept so Save can hand it over without building the
// story a second time.
let tested = null;
// The player tab that asked for a pack, and the pack it asked for.
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

function startCards() {
  const stored = JSON.parse(saved(STORED_CARDS) ?? "null");
  for (const [name, box] of Object.entries(cardBoxes)) {
    if (stored && typeof stored[name] === "boolean") box.checked = stored[name];
    cards[name] = box.checked;
    box.addEventListener("change", () => {
      cards[name] = box.checked;
      remember(STORED_CARDS, JSON.stringify(cards));
    });
  }
}

/** A part's own words need a screen big enough to read a sentence on, and one sentence per part. */
const captionsOn = () => captions && cut === "sentence" && CAPTION_FORMATS.includes(format);

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
  coverSpinner.hidden = false;
  let found = { bitmap: null };
  try {
    found = await findCover(extractBook(await packEntries(), cut), location.href);
  } catch {
    found = { bitmap: null };
  }
  if (job !== coverJob) return;
  // A cover picked while the lookup ran wins over the suggestion, but either way
  // this lookup is over and the spinner goes away.
  coverSpinner.hidden = true;
  if (cover) return;
  // The service names are its own, so they are not translated, only appended.
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
}

function showSummary(story) {
  const parts = story.book.chapters;
  const listed = parts.slice(0, LISTED_PARTS).map((part, index) => `${index + 1}. ${part.title}`).join("\n");
  const more = parts.length > LISTED_PARTS ? `\n${t("summary_more", { n: parts.length - LISTED_PARTS })}` : "";
  summary.textContent = `${story.dirName}\n${t("log_chapters", { n: parts.length })}\n${listed}${more}`;
  summary.hidden = false;
  if (!coverTitle.value.trim()) {
    coverTitle.value = story.book.title || "";
    autoTitle = coverTitle.value;
  }
  if (!coverAuthor.value.trim() && story.book.author) {
    coverAuthor.value = story.book.author;
    autoAuthor = coverAuthor.value;
  }
  showCoverSearch();
}

function clearResult() {
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
    captionsOn(),
    lang,
    cards.total,
    cards.word,
    cards.count,
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
    const voice = await cardAudio(lang, number, {
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
          captions: captionsOn(),
          t: (key) => t(key),
          cardLabel: (number, total, worded) =>
            t(cards.total ? (worded ? "card_label" : "card_number") : (worded ? "card_label_one" : "card_number_one"), {
              n: number,
              total,
            }),
          cardAudio: spoken,
        });

  // One archive, for the player chosen: a Lunii takes the STUdio form of the same
  // story, or the layout its card keeps itself; the Flam launcher imports the Telmi
  // one; and a Merlin card holds no story graph at all — only the files and the
  // playlist that names them.
  let name = `${story.dirName}.zip`;
  let bytes = story.zip;
  if (format === "lunii") {
    name = `${story.dirName}-lunii.zip`;
    bytes = await buildStudioZip(story);
  } else if (format === "lunii-pk") {
    name = packName(story.metadata.title, story.metadata.uuid);
    bytes = await buildPlainPk(story, { picture: rle4Bitmap });
  } else if (format === "merlin") {
    name = `${story.dirName}_merlin.zip`;
    bytes = await writeZip(await buildMerlinFiles(story, { picture: squareJpeg }));
    log(t("log_merlin_beta"));
  }

  showSummary(story);
  return { name, bytes };
}

function offer(name, bytes) {
  downloadLink.href = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
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

/** Build the story into memory, play it, and keep it for the Save button. */
async function testStory() {
  if (!source) return;
  testButton.disabled = true;
  clearResult();
  // The tab is opened on the click itself: a browser only lets a page open one
  // while it is still handling the click, and the story takes seconds to build.
  const tab = window.open("./emulator.html?test=1", "_blank");
  playing = tab ? { tab, pack: null, asked: false } : null;
  try {
    tested = { key: settingsKey(), ...(await buildPack()) };
    if (playing) hand(tested);
    // A blocked popup still has the pack: hand it over as a file the player opens.
    else offer(tested.name, tested.bytes);
  } catch (error) {
    log(t("log_error", { message: error.message ?? error }));
    // The tab is this page's own, and the story it was opened for never came.
    if (playing) playing.tab.close();
    playing = null;
    bar.hidden = true;
  } finally {
    testButton.disabled = false;
  }
}

/** The story the player tab asked for, or asks for the moment it has one. */
function hand(pack) {
  playing.pack = pack;
  if (playing.asked) send();
}

function send() {
  playing.tab.postMessage(
    { type: "daisy2telmi-story", name: playing.pack.name, bytes: playing.pack.bytes },
    location.origin,
  );
}

// The player tab asks for the pack it was opened to play, so nothing is uploaded
// and nothing is saved to disk on the way. It asks as soon as it is loaded, which
// is usually before the story it is waiting for has been built.
window.addEventListener("message", (event) => {
  if (event.origin !== location.origin || !playing || event.source !== playing.tab) return;
  if (event.data?.type !== "daisy2telmi-ask") return;
  playing.asked = true;
  if (playing.pack) send();
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
    const choices = cutChoices(CUT_LEVELS.map((level) => extractBook(entries, level).chapters.length));
    if (!choices.includes(cut)) {
      cut = choices[Math.min(CUT_LEVELS.indexOf(cut), choices.length - 1)];
      remember(STORED_CUT, cut);
    }
    cutLevels = choices;
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

function setSource(name, loader) {
  // The cover and the book's name a lookup gave belong to the pack they were
  // looked up for; everything else the page holds is the user's own setting and stays.
  if (coverAuto) clearCover();
  if (coverTitle.value === autoTitle) coverTitle.value = "";
  if (coverAuthor.value === autoAuthor) coverAuthor.value = "";
  autoTitle = autoAuthor = "";
  source = { name, loader, entries: null };
  // A pack that is not the one a test played cannot be the story that test showed.
  tested = null;
  saveButton.disabled = false;
  testButton.disabled = false;
  clearResult();
  // A dropped part is a number in one book's part list, and the Save button is
  // live again before the new pack's list has been read: clear them here, not in
  // the redraw that follows.
  dropped.clear();
  partsBox.hidden = true;
  logView.textContent = "";
  packLabel.textContent = t("pack_loaded", { name });
  packLabel.hidden = false;
  showCoverSearch(name.replace(/\.[^.]+$/, ""));
  log(t("log_loaded", { name }));
  openCutLevels();
  suggestCover();
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

coverUse.addEventListener("click", async () => {
  const bitmap = await loadCoverUrl(coverUrl.value, location.href);
  if (bitmap) setCover(bitmap);
  else log(t("log_cover_url"));
});

coverClear.addEventListener("click", clearCover);
coverAuthor.addEventListener("input", () => showCoverSearch());
coverTitle.addEventListener("input", () => showCoverSearch());
partsAll.addEventListener("click", keepAllParts);
saveButton.addEventListener("click", saveStory);
testButton.addEventListener("click", testStory);

startLanguage();
startFormat();
startNegative();
startCaptions();
startCut();
startCards();
startCrop();
