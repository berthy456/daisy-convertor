// The story player: a converted story, walked with the machine's own buttons.
//
// `emulator.js` reads the archive and says where each button leads; this file only
// draws the stage it lands on, plays its sound, and lights the buttons that stage
// switched on. Nothing is uploaded: the zip is read in this tab.

import { decodeBitmap, readStory, walk } from "./emulator.js";
import { LANGUAGES, NAMES, apply, pickLanguage, text } from "./i18n.js";
import { readZip } from "./zip.js";

const drop = document.getElementById("drop");
const picker = document.getElementById("picker");
const langSelect = document.getElementById("lang");
const error = document.getElementById("error");
const player = document.getElementById("player");
const title = document.getElementById("story-title");
const screen = document.getElementById("screen");
const keys = {
  up: document.getElementById("up"),
  down: document.getElementById("down"),
  ok: document.getElementById("ok"),
  home: document.getElementById("home"),
  pause: document.getElementById("pause"),
};

const STORED_LANGUAGE = "daisy2telmi.language";
// The Telmi's side buttons step through the clip instead of between stages.
const SEEK = 10;
// How long a chapter keeps the card it is read from on the screen.
const CARD_HOLD = 4000;

const audio = new Audio();
const paint = screen.getContext("2d");

let lang = "en";
let story = null;
let current = null;
let sound = null;
let shown = false;
let hold = null;

const t = (key, values) => text(lang, key, values);

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

function fail(cause) {
  error.textContent = t("emu_not_story", { why: cause.message || String(cause) });
  error.hidden = false;
}

/** The stage's picture: a card's own run-length bitmap, or a PNG straight from the zip. */
async function draw(picture) {
  if (hold !== null) {
    clearTimeout(hold);
    hold = null;
  }
  if (!picture) {
    // A chapter has no picture of its own: it is read from the card it came from, so
    // that card stays up while the clip runs, then the screen goes dark until a key.
    if (shown) hold = setTimeout(dark, CARD_HOLD);
    else dark();
    return;
  }
  if (picture[0] === 0x42 && picture[1] === 0x4d) {
    const card = decodeBitmap(picture);
    screen.width = card.width;
    screen.height = card.height;
    paint.putImageData(new ImageData(card.rgba, card.width, card.height), 0, 0);
    shown = true;
    return;
  }
  const image = await createImageBitmap(new Blob([picture]));
  screen.width = image.width;
  screen.height = image.height;
  paint.drawImage(image, 0, 0);
  image.close();
  shown = true;
}

function dark() {
  hold = null;
  shown = false;
  paint.clearRect(0, 0, screen.width, screen.height);
}

function stop() {
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  if (sound !== null) {
    URL.revokeObjectURL(sound);
    sound = null;
  }
}

/** A button is lit when the stage switched it on; the machine leaves the rest dark. */
function light(stage) {
  keys.ok.disabled = !stage.controls.ok;
  keys.home.disabled = !stage.controls.home;
  // On a Telmi the two side buttons are not the wheel: they seek, and the machine
  // lets them whenever there is a clip to seek through.
  keys.up.disabled = keys.down.disabled =
    story.machine === "telmi" ? stage.audio === null : !stage.controls.wheel;
  keys.pause.disabled = stage.audio === null;
  keys.pause.classList.toggle("on", !audio.paused);
}

async function show(id) {
  const stage = story.stages.get(id);
  if (!stage) return;
  current = id;
  stop();
  await draw(stage.image);
  if (stage.audio) {
    sound = URL.createObjectURL(new Blob([stage.audio], { type: "audio/mpeg" }));
    audio.src = sound;
    // The stage's sound starts as soon as the machine lands on it; `autoplay` only
    // decides whether the end of it forces the OK transition.
    audio.play().catch(() => {});
  }
  light(stage);
}

function press(button) {
  if (!story || current === null) return;
  const next = walk(story, current, button);
  if (next !== null && next !== current) show(next);
}

function seek(step) {
  if (!audio.src) return;
  audio.currentTime = Math.min(Math.max(audio.currentTime + step, 0), audio.duration || 0);
}

function startMachine() {
  keys.up.addEventListener("click", () => (story.machine === "telmi" ? seek(-SEEK) : press("up")));
  keys.down.addEventListener("click", () => (story.machine === "telmi" ? seek(SEEK) : press("down")));
  keys.ok.addEventListener("click", () => press("ok"));
  keys.home.addEventListener("click", () => press("home"));
  keys.pause.addEventListener("click", () => {
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  });
  audio.addEventListener("pause", () => current !== null && light(story.stages.get(current)));
  audio.addEventListener("play", () => current !== null && light(story.stages.get(current)));
  audio.addEventListener("ended", () => {
    const stage = story && current !== null ? story.stages.get(current) : null;
    if (stage && stage.controls.autoplay) press("next");
  });

  document.addEventListener("keydown", (event) => {
    if (!story || event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement) return;
    // The Telmi's side buttons are the same two keys, doing their own thing.
    const side = story.machine === "telmi" ? () => seek(event.key === "ArrowUp" ? -SEEK : SEEK) : () => press(event.key === "ArrowUp" ? "up" : "down");
    if (event.key === "ArrowUp" || event.key === "ArrowDown") side();
    else if (event.key === "Enter" || event.key === " ") press("ok");
    else if (event.key === "Backspace" || event.key.toLowerCase() === "h") press("home");
    else if (event.key.toLowerCase() === "p") keys.pause.click();
    else return;
    event.preventDefault();
  });
}

async function loadBytes(bytes, name) {
  error.hidden = true;
  try {
    const entries = await readZip(bytes);
    story = await readStory(entries);
    // A story that arrived has an answer for the box that asked for one.
    drop.hidden = true;
    player.hidden = false;
    player.classList.toggle("telmi", story.machine === "telmi");
    title.textContent = story.title || name;
    await show(story.start);
  } catch (cause) {
    fail(cause);
  }
}

async function load(file) {
  await loadBytes(new Uint8Array(await file.arrayBuffer()), file.name);
}

startLanguage();
startMachine();

picker.addEventListener("change", () => {
  if (picker.files[0]) load(picker.files[0]);
});
drop.addEventListener("dragover", (event) => {
  event.preventDefault();
  drop.classList.add("over");
});
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  drop.classList.remove("over");
  const file = event.dataTransfer.files[0];
  if (file) load(file);
});

// Opened by the converter page to play a story it just built: that page holds the
// pack, so this one asks for it instead of making the user save it and pick it again.
if (window.opener && new URL(location.href).searchParams.get("test") === "1") {
  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || event.data?.type !== "daisy2telmi-story") return;
    loadBytes(event.data.bytes, event.data.name);
  });
  window.opener.postMessage({ type: "daisy2telmi-ask" }, location.origin);
}
