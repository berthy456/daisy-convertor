// Placeholder images, written by hand so the page needs no canvas and no image library.
// Port of the Python placeholder_png in telmi_builder.py.

import { crc32 } from "./zip.js";

// The Lunii's grey band is one of the sixteen levels its pictures keep, on white.
const GREY_STEP = Math.floor(255 / 15);
const PALETTE = {
  colour: { background: [26, 30, 42], band: [78, 92, 122] },
  bw: { background: [255, 255, 255], band: [11 * GREY_STEP, 11 * GREY_STEP, 11 * GREY_STEP] },
};

/** A negative turns the grey card inside out; a colour card has no negative. */
function palette(style, negative) {
  const colours = PALETTE[style];
  if (!negative) return colours;
  return Object.fromEntries(
    Object.entries(colours).map(([name, rgb]) => [name, rgb.map((value) => 255 - value)]),
  );
}

function fill(rgb, count) {
  const out = new Uint8Array(rgb.length * count);
  for (let i = 0; i < count; i++) out.set(rgb, i * rgb.length);
  return out;
}

function concat(parts) {
  const total = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let at = 0;
  for (const part of parts) {
    total.set(part, at);
    at += part.length;
  }
  return total;
}

async function chunk(tag, payload) {
  const body = concat([new TextEncoder().encode(tag), payload]);
  const header = new Uint8Array(4);
  new DataView(header.buffer).setUint32(0, payload.length, false);
  const crc = new Uint8Array(4);
  new DataView(crc.buffer).setUint32(0, crc32(body), false);
  return concat([header, body, crc]);
}

async function png(width, height, rows) {
  const rawRows = rows.map((row) => concat([new Uint8Array([0]), row]));
  const raw = concat(rawRows);

  const ihdr = new Uint8Array(13);
  const view = new DataView(ihdr.buffer);
  view.setUint32(0, width, false);
  view.setUint32(4, height, false);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // truecolour RGB

  const deflated = new Uint8Array(
    await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream("deflate"))).arrayBuffer(),
  );

  return concat([
    new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    await chunk("IHDR", ihdr),
    await chunk("IDAT", deflated),
    await chunk("IEND", new Uint8Array(0)),
  ]);
}

export async function placeholderPng(width = 640, height = 480, style = "colour", negative = false) {
  const bandTop = Math.floor(height / 2) - 48;
  const bandBottom = Math.floor(height / 2) + 48;
  const { background, band } = palette(style, negative);
  const rows = [];
  for (let y = 0; y < height; y++) rows.push(fill(y >= bandTop && y < bandBottom ? band : background, width));
  return png(width, height, rows);
}

