// Cover images: read one from a file or a link, then draw it at the chosen
// player's sizes.
//
// The canvas does the work the desktop build hands to ffmpeg: centre-crop to the
// target ratio and write the chapter number over it in white with a black
// border, which is what stays legible on an unknown photograph.

export const IMAGE_SIZE = [640, 480];
export const COVER_SIZE = [512, 512];
// The Merlin card's picture: it has no screen, only a 128 px square by each of
// its menu entries.
export const MERLIN_PICTURE = 128;
export const CARD_MARGIN = 24;
export const CARD_LINES = 3;
export const CARD_WORDS = 20;
// A digit's ink is about three quarters of the font size, in any of them.
const DIGIT_INK = 0.72;

// What each player's screen shows, so the picture is drawn where it will be seen:
// a cover cropped for a 320x240 Lunii keeps the band that Lunii displays.
const SCREENS = {
  telmi: [IMAGE_SIZE, COVER_SIZE],
  // The Merlin has no screen: its card is built from a Telmi story, so the pictures
  // are drawn at Telmi's sizes and cut to 128 px squares on the way out.
  merlin: [IMAGE_SIZE, COVER_SIZE],
  lunii: [
    [320, 240],
    [320, 240],
  ],
  // The same screen as the Lunii's, in the layout the story card keeps itself.
  "lunii-pk": [
    [320, 240],
    [320, 240],
  ],
  flam: [
    [320, 212],
    [104, 136],
  ],
};

/** The scene and cover sizes of one player. */
export function screen(player) {
  return SCREENS[player];
}

/** The margin and the height the cover keeps to itself: a smaller screen shrinks both. */
function layout(width) {
  const step = width / IMAGE_SIZE[0];
  const cover = Math.floor(width / 4);
  return {
    margin: Math.max(6, Math.round(CARD_MARGIN * step)),
    coverTop: Math.floor((cover * 3) / 4),
  };
}

// A Lunii that is not a Flam has no colour panel: sixteen greys, and the light end
// of them, so its cards are drawn the other way round — white ground, black ink.
// The published packs keep the same sixteen levels in each picture's own palette.
export const STYLES = ["colour", "bw"];
export const GREY_LEVELS = 16;
const GREY_STEP = Math.floor(255 / (GREY_LEVELS - 1));
// `band` fills the frame's empty room when the cover is shown whole: it belongs to
// the card, so it is the card's own second colour and not black.
const PALETTE = {
  colour: { background: "#1a1e2a", band: "#4e5c7a", ink: "#fff", border: "#000" },
  bw: { background: "#fff", band: "#bbbbbb", ink: "#000", border: "#fff" },
};

/**
 * One of the sixteen levels, from a 0-255 luminance. The contrast and the levels
 * match the desktop build's ffmpeg chain, so both draw the same card.
 */
export function greyLevel(luminance, negative = false) {
  const light = (luminance - 128) * 1.6 + 128;
  const value = Math.min(255, Math.max(0, negative ? 255 - light : light));
  return Math.round((value * (GREY_LEVELS - 1)) / 255) * GREY_STEP;
}

export const CONTRAST_LIMITS = [0.5, 1.5];

/**
 * Pull the lights and the shadows further apart, the way ffmpeg's eq=contrast does:
 * every value moves off the middle by `value`, so 1 leaves the picture alone.
 *
 * It runs on the cover alone, before the lettering goes on, as the desktop build
 * runs it on the crop before the card is written.
 */
export function applyContrast(ctx, width, height, value = 1) {
  const factor = Math.min(CONTRAST_LIMITS[1], Math.max(CONTRAST_LIMITS[0], value));
  if (factor === 1) return;
  const image = ctx.getImageData(0, 0, width, height);
  for (let at = 0; at < image.data.length; at += 4) {
    for (const channel of [0, 1, 2]) {
      const light = Math.round((image.data[at + channel] - 128) * factor + 128);
      image.data[at + channel] = Math.min(255, Math.max(0, light));
    }
  }
  ctx.putImageData(image, 0, 0);
}

/** Flatten a finished picture to the sixteen levels a plain Lunii shows. */
export function toGreys(ctx, width, height, negative = false) {
  const image = ctx.getImageData(0, 0, width, height);
  for (let at = 0; at < image.data.length; at += 4) {
    // The weights are the ones the device's own converter uses, so the card
    // that reaches its screen is the one drawn here.
    const grey = greyLevel(0.299 * image.data[at] + 0.587 * image.data[at + 1] + 0.114 * image.data[at + 2], negative);
    image.data[at] = image.data[at + 1] = image.data[at + 2] = grey;
  }
  ctx.putImageData(image, 0, 0);
}

/** Where the user goes to look for a cover. Neither engine hands one back to the
    page, so the picture is saved from the search and pasted into the URL box. */
export function searchUrls(query) {
  const words = encodeURIComponent(query);
  return {
    ddg: `https://duckduckgo.com/?q=${words}&iax=images&ia=images`,
    google: `https://www.google.com/search?tbm=isch&q=${words}`,
  };
}

/** Only http(s) links: a pasted file:, data: or javascript: is not a cover. */
export function isReadableUrl(value, base) {
  if (!value.trim()) return false;
  try {
    const url = new URL(value, base);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

export async function loadCoverFile(file) {
  try {
    return await createImageBitmap(file);
  } catch {
    return null;
  }
}

/**
 * A link only works when the other site allows this page to read it, which most
 * do not. The caller reports that as a reason to download the file instead.
 */
export async function loadCoverUrl(value, base) {
  if (!isReadableUrl(value, base)) return null;
  try {
    const response = await fetch(new URL(value, base), { mode: "cors" });
    if (!response.ok) return null;
    return await createImageBitmap(await response.blob());
  } catch {
    return null;
  }
}

function surface(width, height) {
  if (typeof OffscreenCanvas === "function") return new OffscreenCanvas(width, height);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

async function toBytes(canvas, type, quality) {
  const blob = canvas.convertToBlob
    ? await canvas.convertToBlob({ type, quality })
    : await new Promise((resolve) => canvas.toBlob(resolve, type, quality));
  return new Uint8Array(await blob.arrayBuffer());
}

const toPng = (canvas) => toBytes(canvas, "image/png");

export const CROP_MODES = ["top", "center", "bottom"];

// Where the frame sits over the cover: 0.5 is centred, 0 is the left or top edge,
// 1 the right or bottom one. The three named modes are the presets a user picks
// without a mouse; a drag sets the same two numbers anywhere between.
export const CROP_FOCUS = { top: [0.5, 0], center: [0.5, 0.5], bottom: [0.5, 1] };
export const MIN_ZOOM = 0;
export const MAX_ZOOM = 4;

/**
 * The part of the cover the frame shows: {crop} for a preset, or {focus, zoom}
 * for a place the user moved and scaled themselves.
 *
 * Zoom 0 fits the whole cover inside the frame, zoom 1 fills the frame, and past 1
 * the cover grows further and the frame crops it. Between 0 and 1 the scale moves
 * geometrically from the fit to the fill, so the slider's ends land on the two
 * things a listener can see.
 */
export function cropRect(cover, width, height, view = {}) {
  const focus = view.focus ?? CROP_FOCUS[view.crop ?? "center"];
  const zoom = Math.min(Math.max(MIN_ZOOM, view.zoom ?? 1), MAX_ZOOM);
  const fit = Math.min(width / cover.width, height / cover.height);
  const fill = Math.max(width / cover.width, height / cover.height);
  const scale = fit * (fill / fit) ** Math.min(Math.max(zoom, 0), 1) * Math.max(1, zoom);
  const drawnWidth = cover.width * scale;
  const drawnHeight = cover.height * scale;
  return [(width - drawnWidth) * focus[0], (height - drawnHeight) * focus[1], drawnWidth, drawnHeight];
}

/**
 * The cover, cropped into the box at (left, top). What the cover leaves of the box
 * takes the card's band colour, so a whole cover reads as a whole cover.
 */
function drawCover(ctx, cover, left, top, width, height, view = {}, style = "colour") {
  const [x, y, drawnWidth, drawnHeight] = cropRect(cover, width, height, view);
  if (drawnWidth < width || drawnHeight < height) {
    ctx.fillStyle = PALETTE[style].band;
    ctx.fillRect(left, top, width, height);
  }
  ctx.drawImage(cover, left + x, top + y, drawnWidth, drawnHeight);
}

/**
 * The cover as the card chosen will show it: the frame's empty room filled with the
 * card's band, and a grey card's picture flattened to the greys its screen has.
 *
 * Drawn the way `coverPng` draws the picture this becomes, so what the crop frame
 * shows is what the story will carry. `label` letters it as the card will be lettered,
 * so the words it will carry are seen on the picture they will be carried on.
 */
export function drawPreview(ctx, cover, width, height, view = {}, style = "colour", negative = false, contrast = 1, label = "") {
  // The frame is painted over each time it is drawn, so the room the cover leaves
  // takes the band and a cover with transparency has something of its own to sit on.
  ctx.fillStyle = PALETTE[style].band;
  ctx.fillRect(0, 0, width, height);
  if (cover) {
    drawCover(ctx, cover, 0, 0, width, height, view, style);
    applyContrast(ctx, width, height, contrast);
  }
  if (label) cardLettering(ctx, label, width, height, style);
  if (style === "bw") toGreys(ctx, width, height, negative);
}

/**
 * The opening words of a part, as the three lines a card can show.
 * A run with no spaces (a CJK sentence) is broken where it stops fitting.
 */
export function cardWords(ctx, text, width) {
  const pieces = [];
  for (const word of (text ?? "").trim().split(/\s+/).slice(0, CARD_WORDS)) {
    if (!word) continue;
    if (ctx.measureText(word).width <= width) {
      pieces.push(word);
      continue;
    }
    let run = "";
    for (const character of word) {
      if (run && ctx.measureText(run + character).width > width) {
        pieces.push(run);
        run = character;
      } else {
        run += character;
      }
    }
    if (run) pieces.push(run);
  }

  const lines = [];
  let line = "";
  for (const piece of pieces) {
    const joined = line ? `${line} ${piece}` : piece;
    if (line && ctx.measureText(joined).width > width) {
      lines.push(line);
      line = piece;
    } else {
      line = joined;
    }
    if (lines.length === CARD_LINES) break;
  }
  if (line && lines.length < CARD_LINES) lines.push(line);
  return lines;
}

/**
 * The card's own lettering, laid over whatever is already drawn on the canvas.
 *
 * A grey card carries its number alone, as large as its screen allows; a colour card
 * numbers itself top right. `cardPng` draws it over the cover it composed, and the
 * crop preview draws it over the frame, so the words a card will carry are seen on
 * the picture they will be carried on.
 */
export function cardLettering(ctx, label, width, height, style) {
  const { margin } = layout(width);
  const room = width - 2 * margin;
  ctx.lineJoin = "round";
  ctx.miterLimit = 2;
  if (style === "bw") {
    // The Lunii screen sits behind a plastic window, so nothing drawn small is
    // readable there: the card carries the number alone. It is sized as a
    // three-digit number, so every card of a story carries a number of the same
    // height and a hundred-part story fills the width.
    const base = Math.round(height / 11);
    ctx.font = `bold ${base}px system-ui, sans-serif`;
    const widest = Math.max(ctx.measureText(label).width, ctx.measureText("888").width);
    const size = Math.min((base * room) / widest, (height - 2 * margin) / DIGIT_INK);
    const middle = Math.round(height / 2);
    const ink = Math.round(size * DIGIT_INK);
    ctx.fillStyle = PALETTE[style].background;
    ctx.fillRect(0, middle - ink / 2 - margin, width, ink + 2 * margin);
    ctx.font = `bold ${size}px system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineWidth = Math.max(4, Math.round(size / 12));
    ctx.strokeStyle = PALETTE[style].border;
    ctx.fillStyle = PALETTE[style].ink;
    ctx.strokeText(label, width / 2, middle);
    ctx.fillText(label, width / 2, middle);
    return;
  }
  ctx.textBaseline = "top";
  // A card asked to carry no words of its own keeps the picture and its opening
  // lines: the number is what a card adds, and it was asked not to.
  if (!label) return;
  const numberSize = Math.round(height / 11);
  ctx.font = `bold ${numberSize}px system-ui, sans-serif`;
  const wide = ctx.measureText(label).width;
  // A titled line is drawn smaller than a bare number, never smaller than the
  // card's own opening words: below that the number stops being the thing a
  // listener reads on a card.
  const drawn = wide <= room ? numberSize : Math.max(Math.round(height / 17), Math.round((numberSize * room) / wide));
  ctx.font = `bold ${drawn}px system-ui, sans-serif`;
  ctx.lineWidth = 6;
  ctx.strokeStyle = PALETTE[style].border;
  ctx.fillStyle = PALETTE[style].ink;
  ctx.textAlign = "right";
  ctx.strokeText(label, width - margin, margin);
  ctx.fillText(label, width - margin, margin);
}

/**
 * One card: the part's number, its opening words, and the cover behind them.
 *
 * Every card carries the cover across the whole screen. A sixteen-grey card is the
 * Lunii's: the number alone in the middle of it.
 *
 * `negative` only means anything for the grey card: the card is drawn as usual and
 * light and dark are swapped once, when it is flattened, so the number reads as the
 * other way round too.
 *
 * `title` is written beside the number. A card with no room for it keeps its number,
 * as the app keeps it from a card whose typeface cannot write the title.
 */
export async function cardPng(
  label,
  text,
  cover = null,
  view = {},
  width = IMAGE_SIZE[0],
  height = IMAGE_SIZE[1],
  style = "colour",
  negative = false,
  title = "",
  contrast = 1,
) {
  const canvas = surface(width, height);
  const ctx = canvas.getContext("2d");
  const { margin, coverTop } = layout(width);
  const band = style === "bw";
  ctx.fillStyle = PALETTE[style].background;
  ctx.fillRect(0, 0, width, height);
  ctx.lineJoin = "round";
  ctx.miterLimit = 2;
  ctx.textBaseline = "top";
  if (cover) {
    drawCover(ctx, cover, 0, 0, width, height, view, style);
    applyContrast(ctx, width, height, contrast);
  }

  if (band) {
    cardLettering(ctx, label, width, height, style);
    // Flattened last, once the cover and the number are down, so a card cannot
    // pick up a grey the screen lacks where two layers meet.
    if (style === "bw") toGreys(ctx, width, height, negative);
    return toPng(canvas);
  }

  const size = Math.round(height / 17);
  const pitch = Math.round(size * 1.3);
  const room = width - 2 * margin;
  // The words are measured in the font they will be drawn in, so it goes on first.
  ctx.font = `${size}px system-ui, sans-serif`;
  const lines = cardWords(ctx, text, room);
  const wordsTop = margin + coverTop + margin;

  // A title is kept when the card has room for it at the size of its own words.
  const named = title ? (label ? `${label}, ${title}` : title) : label;
  const lettering = ctx.measureText(named).width <= room ? named : label;
  cardLettering(ctx, lettering, width, height, style);

  ctx.font = `${size}px system-ui, sans-serif`;
  ctx.textAlign = "left";
  ctx.lineWidth = 4;
  for (const [index, line] of lines.entries()) {
    const top = wordsTop + index * pitch;
    ctx.strokeText(line, margin, top);
    ctx.fillText(line, margin, top);
  }
  return toPng(canvas);
}

// A sentence's own picture: the whole of it, at the largest lettering the screen
// still holds. The size is found by trying the largest and stepping down until
// every line of the sentence fits.
export const CAPTION_MAX_SIZE = 96;
export const CAPTION_MIN_SIZE = 8;
// The air between two lines grows with the lettering, but never below a few pixels:
// at the small lettering a tall sentence needs, one pixel of air reads as one solid
// block of words.
export const CAPTION_LINE_GAP = 4;

/** How many pixel rows one line of a caption takes: its own height and the air under it. */
export function linePitch(size) {
  return size + Math.max(Math.round(size / 7), CAPTION_LINE_GAP);
}

/** `words` in lines no wider than `room`; a word wider than that is cut where it stops fitting. */
function wrapWords(ctx, words, room) {
  const lines = [];
  let line = "";
  for (const word of words) {
    let piece = word;
    while (ctx.measureText(piece).width > room) {
      if (piece.length < 2) return null;
      let cut = piece.length - 1;
      while (cut > 1 && ctx.measureText(piece.slice(0, cut)).width > room) cut -= 1;
      if (line) {
        lines.push(line);
        line = "";
      }
      lines.push(piece.slice(0, cut));
      piece = piece.slice(cut);
    }
    const joined = line ? `${line} ${piece}` : piece;
    if (line && ctx.measureText(joined).width > room) {
      lines.push(line);
      line = piece;
    } else {
      line = joined;
    }
  }
  if (line) lines.push(line);
  return lines;
}

/** The largest lettering the whole sentence fits at, and its lines. */
export function captionFit(ctx, text, width, height) {
  const { margin } = layout(width);
  const room = [width - 2 * margin, height - 2 * margin];
  const words = (text ?? "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return { size: CAPTION_MIN_SIZE, lines: [] };
  for (let size = CAPTION_MAX_SIZE; size > CAPTION_MIN_SIZE; size -= 2) {
    ctx.font = `${size}px system-ui, sans-serif`;
    const lines = wrapWords(ctx, words, room[0]);
    if (lines && (lines.length - 1) * linePitch(size) + size <= room[1]) return { size, lines };
  }
  ctx.font = `${CAPTION_MIN_SIZE}px system-ui, sans-serif`;
  const lines = wrapWords(ctx, words, room[0]) ?? [];
  const room_for_lines = Math.max(1, Math.floor((room[1] - CAPTION_MIN_SIZE) / linePitch(CAPTION_MIN_SIZE)) + 1);
  return { size: CAPTION_MIN_SIZE, lines: lines.slice(0, room_for_lines) };
}

/** The sentence as one screen will show it, at the largest size that holds it. */
export function captionLines(ctx, text, width, height) {
  return captionFit(ctx, text, width, height).lines;
}

/**
 * One sentence on its own ground: no cover behind it, nothing but the words.
 *
 * A part's caption replaces the card's picture while its audio plays, so the
 * sentence is read on the plain ground the card's lettering sits on too.
 *
 * null when the part has no words to draw: there is nothing to show, so the part
 * keeps the card's own picture.
 */
export async function captionPng(text, width = IMAGE_SIZE[0], height = IMAGE_SIZE[1], style = "colour", negative = false) {
  const canvas = surface(width, height);
  const ctx = canvas.getContext("2d");
  const { margin } = layout(width);
  const { size, lines } = captionFit(ctx, text, width, height);
  if (!lines.length) return null;
  ctx.fillStyle = PALETTE[style].background;
  ctx.fillRect(0, 0, width, height);
  ctx.lineJoin = "round";
  ctx.miterLimit = 2;
  ctx.textBaseline = "top";
  ctx.textAlign = "center";
  ctx.font = `${size}px system-ui, sans-serif`;
  ctx.lineWidth = Math.max(2, Math.round(size / 10));
  ctx.strokeStyle = PALETTE[style].border;
  ctx.fillStyle = PALETTE[style].ink;
  const block = (lines.length - 1) * linePitch(size) + size;
  const top = Math.max(margin, Math.round((height - block) / 2));
  for (const [index, line] of lines.entries()) {
    ctx.strokeText(line, width / 2, top + index * linePitch(size));
    ctx.fillText(line, width / 2, top + index * linePitch(size));
  }
  if (style === "bw") toGreys(ctx, width, height, negative);
  return toPng(canvas);
}

/** The cover, cropped to width x height. */
export async function coverPng(cover, width, height, view = {}, style = "colour", negative = false, contrast = 1) {
  const canvas = surface(width, height);
  const ctx = canvas.getContext("2d");
  drawCover(ctx, cover, 0, 0, width, height, view, style);
  applyContrast(ctx, width, height, contrast);
  if (style === "bw") toGreys(ctx, width, height, negative);
  return toPng(canvas);
}

/**
 * A card picture: the middle square of the source, at `size`, as a JPEG.
 *
 * The story's own PNGs go in, not the cover: a card shows the picture that was
 * already cropped for this player. ffmpeg's crop filter centres by default, so
 * this cuts the same square the desktop build cuts.
 */
export async function squareJpeg(bytes, size = MERLIN_PICTURE) {
  const image = await createImageBitmap(new Blob([bytes]));
  const side = Math.min(image.width, image.height);
  const canvas = surface(size, size);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(
    image,
    Math.floor((image.width - side) / 2),
    Math.floor((image.height - side) / 2),
    side,
    side,
    0,
    0,
    size,
    size,
  );
  return toBytes(canvas, "image/jpeg", 0.92);
}

// Every picture of a Lunii card carries its own sixteen colours, which is what a
// published pack does. The desktop build picks them with ffmpeg's `palettegen`;
// the canvas has no such filter, so the same median cut is done here.
const BITMAP_COLORS = 16;

/** How far one channel reaches over `pixels[a..b]`. */
function span(pixels, a, b, channel) {
  let low = 255;
  let high = 0;
  for (let i = a; i < b; i++) {
    const value = pixels[i].rgb[channel];
    if (value < low) low = value;
    if (value > high) high = value;
  }
  return high - low;
}

/**
 * `colors` colours covering the picture's own: the box that varies most is split
 * along the channel it varies along most, where its pixels halve, and each half
 * keeps the weighted mean of what fell inside it.
 */
function medianCut(pixels, colors = BITMAP_COLORS) {
  const boxes = [[0, pixels.length]];
  while (boxes.length < colors) {
    let widest = null;
    for (const box of boxes) {
      if (box[1] - box[0] < 2) continue;
      for (let channel = 0; channel < 3; channel++) {
        const range = span(pixels, box[0], box[1], channel);
        if (!widest || range > widest.range) widest = { box, channel, range };
      }
    }
    if (!widest || widest.range === 0) break;
    const { box, channel } = widest;
    // Replacing the box with the same number of entries leaves every other
    // box's indices valid.
    const sorted = pixels.slice(box[0], box[1]).sort((x, y) => x.rgb[channel] - y.rgb[channel]);
    pixels.splice(box[0], box[1] - box[0], ...sorted);
    // Split where the pixels halve, not where the colours do: a handful of rare
    // colours should not take a slot from the busy part of the picture.
    let total = 0;
    for (let i = box[0]; i < box[1]; i++) total += pixels[i].count;
    let at = box[0];
    let seen = 0;
    for (let i = box[0]; i < box[1] - 1; i++) {
      seen += pixels[i].count;
      at = i;
      if (seen * 2 >= total) break;
    }
    boxes.splice(boxes.indexOf(box), 1, [box[0], at + 1], [at + 1, box[1]]);
  }
  return boxes.map(([a, b]) => {
    const mean = [0, 0, 0];
    let total = 0;
    for (let i = a; i < b; i++) {
      for (let channel = 0; channel < 3; channel++) mean[channel] += pixels[i].rgb[channel] * pixels[i].count;
      total += pixels[i].count;
    }
    return mean.map((value) => Math.round(value / total));
  });
}

/**
 * A card drawn the way a Lunii story card stores it: a 320x240 bitmap in the
 * run-length form the device reads, one line at a time, top line first.
 *
 * The desktop build hands the scaling and the palette to ffmpeg; here the canvas
 * does both, and the runs are written by hand. A colour card keeps its colour:
 * the sixteen entries are the picture's own, not a fixed grey ramp.
 */
export async function rle4Bitmap(bytes, size = SCREENS["lunii-pk"][0]) {
  const image = await createImageBitmap(new Blob([bytes]));
  const [width, height] = size;
  const canvas = surface(width, height);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(image, 0, 0, width, height);
  const { data } = ctx.getImageData(0, 0, width, height);

  const distinct = new Map();
  for (let at = 0; at < data.length; at += 4) {
    const key = (data[at] << 16) | (data[at + 1] << 8) | data[at + 2];
    const colour = distinct.get(key);
    if (colour) colour.count++;
    else distinct.set(key, { rgb: [data[at], data[at + 1], data[at + 2]], count: 1 });
  }
  const colours = medianCut([...distinct.values()]);
  while (colours.length < BITMAP_COLORS) colours.push(colours[colours.length - 1]);

  // ffmpeg's `paletteuse=dither=none`: each pixel takes the nearest of the
  // sixteen, with no error spread into its neighbours.
  const slots = new Map();
  const slotOf = (key) => {
    const known = slots.get(key);
    if (known !== undefined) return known;
    const [red, green, blue] = [(key >> 16) & 0xff, (key >> 8) & 0xff, key & 0xff];
    let best = 0;
    let closest = Infinity;
    for (const [slot, [pr, pg, pb]] of colours.entries()) {
      const distance = (red - pr) ** 2 + (green - pg) ** 2 + (blue - pb) ** 2;
      if (distance < closest) {
        closest = distance;
        best = slot;
      }
    }
    slots.set(key, best);
    return best;
  };

  const runs = [];
  for (let line = 0; line < height; line++) {
    let count = 0;
    let slot = -1;
    for (let x = 0; x < width; x++) {
      const at = (line * width + x) * 4;
      const index = slotOf((data[at] << 16) | (data[at + 1] << 8) | data[at + 2]);
      if (index === slot && count < 255) {
        count++;
        continue;
      }
      if (slot >= 0) runs.push(count, (slot << 4) | slot);
      slot = index;
      count = 1;
    }
    runs.push(count, (slot << 4) | slot);
    // End of line, but not after the last one: `00 01` closes the bitmap.
    if (line + 1 < height) runs.push(0, 0);
  }
  runs.push(0, 1);

  const head = new Uint8Array(54 + BITMAP_COLORS * 4);
  const view = new DataView(head.buffer);
  head[0] = 0x42;
  head[1] = 0x4d;
  view.setUint32(2, head.length + runs.length, true);
  view.setUint32(10, head.length, true);
  view.setUint32(14, 40, true);
  view.setInt32(18, width, true);
  view.setInt32(22, height, true);
  view.setUint16(26, 1, true);
  view.setUint16(28, 4, true);
  view.setUint32(30, 2, true);
  view.setUint32(34, runs.length, true);
  view.setUint32(46, BITMAP_COLORS, true);
  for (const [slot, [red, green, blue]] of colours.entries()) {
    // A BMP palette is blue, green, red; the canvas reads the other way round.
    head.set([blue, green, red, 0], 54 + 4 * slot);
  }

  const bitmap = new Uint8Array(head.length + runs.length);
  bitmap.set(head);
  bitmap.set(runs, head.length);
  return bitmap;
}
