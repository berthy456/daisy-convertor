// DAISY 2.02, DAISY 3 and EPUB reader over zip entries: find the pack, read the
// outline, resolve each chapter's audio. Port of src/daisy2telmi/{detector,extractor}.py.

import { parseXml, parseClock } from "./xml.js";

const HEADING_LEVELS = { h1: 1, h2: 2, h3: 3, h4: 4, h5: 5, h6: 6 };
const MERGE_EPSILON = 0.005;
const DOCUMENT_SUFFIXES = [".htm", ".html", ".xhtml"];

export function normalizePath(path) {
  const out = [];
  for (const part of path.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") out.pop();
    else out.push(part);
  }
  return out.join("/");
}

export function dirName(path) {
  const at = path.lastIndexOf("/");
  return at < 0 ? "" : path.slice(0, at);
}

function isIgnored(name) {
  const parts = name.split("/");
  return parts.includes("__MACOSX") || parts[parts.length - 1].startsWith("._");
}

function decodeEntryName(bytes) {
  return new TextDecoder("utf-8").decode(bytes);
}

function textOf(element) {
  let out = "";
  const walk = (node) => {
    if (node.tagName === "#text") out += node.value;
    for (const child of node.children) walk(child);
  };
  walk(element);
  return out.trim();
}

/** A tag without its namespace prefix. `dc:Title` and `dc:title` are the same tag. */
function localName(tag) {
  const at = tag.indexOf(":");
  return at < 0 ? tag : tag.slice(at + 1);
}

/** Every descendant with this local name, whatever case or prefix it was written with. */
function named(root, name) {
  const found = [];
  const walk = (node) => {
    for (const child of node.children) {
      if (child.tagName !== "#text" && localName(child.tagName).toLowerCase() === name) found.push(child);
      walk(child);
    }
  };
  walk(root);
  return found;
}

function childNamed(node, name) {
  return (
    node.children.find((child) => child.tagName !== "#text" && localName(child.tagName).toLowerCase() === name) ??
    null
  );
}

/** Locate the pack root: the shallowest folder holding ncc.html or a .ncx. */
export function findPack(entries) {
  const byDir = new Map();
  for (const entry of entries) {
    if (isIgnored(entry.name)) continue;
    const dir = dirName(entry.name);
    if (!byDir.has(dir)) byDir.set(dir, []);
    byDir.get(dir).push(entry);
  }

  const dirs = [...byDir.keys()].sort((a, b) => a.split("/").length - b.split("/").length);
  for (const dir of dirs.slice(0, 32)) {
    const files = byDir.get(dir);
    const baseName = (entry) => entry.name.split("/").pop().toLowerCase();
    const ncc = files.find((f) => baseName(f) === "ncc.html");
    const ncx = files.filter((f) => baseName(f).endsWith(".ncx")).sort((a, b) => a.name.localeCompare(b.name))[0];
    if (!ncc && !ncx) continue;
    // A 2.02 pack names its package document `package.opf`; a 3.x one and an EPUB
    // name it after the book, so any .opf beside the outline will do.
    const opf =
      files.find((f) => baseName(f) === "package.opf") ??
      files.filter((f) => baseName(f).endsWith(".opf")).sort((a, b) => a.name.localeCompare(b.name))[0];
    return { root: dir, toc: ncc ?? ncx, opf, isNcc: Boolean(ncc) };
  }
  return null;
}

const TELMI_MARKERS = ["metadata.json", "nodes.json", "title.mp3", "title.png"];

/** The folder holding every Telmi story marker, when the input is already a Telmi pack. */
export function findTelmiDir(entries) {
  const byDir = new Map();
  for (const entry of entries) {
    if (isIgnored(entry.name)) continue;
    const dir = dirName(entry.name);
    if (!byDir.has(dir)) byDir.set(dir, new Set());
    byDir.get(dir).add(entry.name.split("/").pop().toLowerCase());
  }
  for (const [dir, names] of byDir) {
    if (TELMI_MARKERS.every((name) => names.has(name))) return dir;
  }
  return null;
}

function npt(value) {
  if (!value) return null;
  let text = value.trim();
  if (text.slice(0, 3).toLowerCase() === "npt") text = text.slice(3).replace(/^=/, "").trim();
  if (!text || text.toLowerCase() === "indefinite") return null;
  const parts = text.split(":");
  const num = (s) => Number.parseFloat(s.replace(/s$/i, ""));
  if (parts.length === 3) return Number(parts[0]) * 3600 + Number(parts[1]) * 60 + num(parts[2]);
  if (parts.length === 2) return Number(parts[0]) * 60 + num(parts[1]);
  const seconds = num(text);
  return Number.isFinite(seconds) ? seconds : null;
}

/**
 * A SMIL `<audio>`'s trim points. DAISY 2.02 writes them `clip-begin`, DAISY 3
 * writes `clipBegin`; reading only one spelling leaves a 3.x clip untrimmed,
 * which repeats its whole file.
 */
function clipOf(element) {
  return { begin: npt(element.get("clip-begin") ?? element.get("clipBegin")), end: npt(element.get("clip-end") ?? element.get("clipEnd")) };
}

function merge(segments) {
  const out = [];
  for (const segment of segments) {
    const previous = out[out.length - 1];
    const contiguous =
      previous &&
      previous.src === segment.src &&
      previous.end != null &&
      segment.begin != null &&
      Math.abs(previous.end - segment.begin) < MERGE_EPSILON;
    if (contiguous) out[out.length - 1] = { src: previous.src, begin: previous.begin, end: segment.end };
    else out.push(segment);
  }
  return out;
}

/** An href resolved against a folder, when the pack actually carries that file. */
function resolveTarget(href, base, byName) {
  const raw = decodeURIComponent(href ?? "").split("#")[0].trim();
  if (!raw) return null;
  const name = normalizePath(`${base}/${raw}`);
  return byName.has(name) ? name : null;
}

/**
 * Turn an EPUB's `content src`, such as `book.xhtml#ch1`, into a document and an
 * anchor. An EPUB has no SMIL at all: its whole book is one or a few XHTML files,
 * and its table of contents points into them.
 */
function splitDocument(href) {
  const raw = decodeURIComponent(href ?? "");
  const hash = raw.indexOf("#");
  const filePart = (hash < 0 ? raw : raw.slice(0, hash)).trim();
  const anchor = hash < 0 ? "" : raw.slice(hash + 1);
  const suffix = `.${(filePart.split(".").pop() ?? "").toLowerCase()}`;
  if (!filePart || !DOCUMENT_SUFFIXES.includes(suffix)) return null;
  return { path: filePart, anchor };
}

/** Every anchor the outline names, grouped by the file it sits in. */
function anchorsByDocument(entries) {
  const found = new Map();
  for (const entry of entries) {
    const document = splitDocument(entry.target);
    if (!document || !document.anchor) continue;
    if (!found.has(document.path)) found.set(document.path, new Set());
    found.get(document.path).add(document.anchor);
  }
  return found;
}

/**
 * The words of a document that belong to each anchor: its own, up to the next.
 * The outline of an EPUB names a heading inside the file rather than a range, so
 * a chapter is everything the reader reaches before the next named heading. Text
 * is walked in document order and handed to the last anchor passed, which keeps a
 * section's nested markup with the section that opened it.
 */
function documentTexts(root, anchors) {
  const words = new Map();
  let current = null;
  const walk = (node) => {
    for (const child of node.children) {
      if (child.tagName === "#text") {
        if (current !== null) words.get(current).push(child.value);
        continue;
      }
      const anchor = child.get("id");
      if (anchors.has(anchor)) {
        current = anchor;
        if (!words.has(anchor)) words.set(anchor, []);
      }
      walk(child);
    }
  };
  walk(root);
  const out = new Map();
  for (const [anchor, parts] of words) out.set(anchor, parts.join(" ").replace(/\s+/g, " ").trim());
  return out;
}

const SENTENCES = /(?<=[.!?…])\s+/;

const sentences = (text) => text.split(SENTENCES).map((s) => s.trim()).filter(Boolean);

/** The line a card shows for a part that has no synced text of its own. */
const firstWords = (body) => sentences(body).find((s) => s.length >= 5) ?? "";

/**
 * Join the pack's synced texts, dropping a text repeated by the clips after it.
 *
 * A pack that synced only its headings writes the heading once in the `<par>` that
 * holds the whole level, and every clip of that level inherits it: joining them as
 * they come would read the heading once per sentence.
 */
function joinTexts(texts) {
  const joined = [];
  for (const text of texts) if (!joined.length || text !== joined[joined.length - 1]) joined.push(text);
  return joined.join(" ");
}

/**
 * The outline level that holds the book's chapters. The shallowest is usually it,
 * but a DAISY 3 outline often has one `h1` wrapping everything, which would give
 * a single part as long as the book; the level below it holds the real chapters.
 */
function chapterLevelOf(entries) {
  const body = entries.filter((e) => !e.frontMatter);
  const levels = [...new Set(body.map((e) => e.level))].sort((a, b) => a - b);
  for (const level of levels) {
    if (body.filter((e) => e.level === level).length > 1) return level;
  }
  return levels[0] ?? 1;
}

function metaValues(root) {
  const out = new Map();
  for (const meta of root.find("meta")) {
    const name = meta.get("name");
    if (name) {
      const key = name.toLowerCase();
      if (!out.has(key)) out.set(key, (meta.get("content") ?? "").trim());
    }
  }
  return out;
}

export class DaisyError extends Error {}

/** Where to cut the book: at its chapter headings, at every heading, or at every synced mark. */
export const CUT_LEVELS = ["chapter", "paragraph", "sentence"];

/**
 * Which cut levels a pack can actually cut at, from the part counts its own
 * outline gives at each level. A level that adds no parts over the one above it
 * only repeats that level, so there is nothing there to cut; the audio is never
 * asked, which is what keeps a silent stretch from looking like a boundary.
 */
export function cutChoices(parts) {
  const available = [];
  let count = 0;
  for (const [index, level] of CUT_LEVELS.entries()) {
    if (parts[index] > count) {
      available.push(level);
      count = parts[index];
    }
  }
  return available;
}

/** Entries as [{name, data}] from a DAISY pack; returns the book structure. */
export function extractBook(entries, level = "chapter") {
  if (!CUT_LEVELS.includes(level)) throw new DaisyError(`unknown cut level: ${level}`);
  const pack = findPack(entries);
  if (!pack) {
    if (findTelmiDir(entries)) {
      throw new DaisyError(
        "this is already a Telmi story (metadata.json, nodes.json, title.mp3, title.png), not a DAISY pack. " +
          "A Telmi zip goes straight onto the story teller; convert the original DAISY pack instead."
      );
    }
    throw new DaisyError("no table of contents (ncc.html or *.ncx) found");
  }

  const decoder = new TextDecoder("utf-8");
  const byName = new Map(entries.map((entry) => [normalizePath(entry.name), entry]));
  const parse = (name) => {
    const entry = byName.get(name);
    return entry ? parseXml(decoder.decode(entry.data)) : null;
  };

  const tocRoot = parse(normalizePath(pack.toc.name));
  if (!tocRoot) throw new DaisyError("could not read the table of contents");

  const entries_ = pack.isNcc ? nccEntries(tocRoot) : ncxEntries(tocRoot);
  const metadata = metaValues(tocRoot);

  if (pack.opf) {
    const opf = parse(normalizePath(pack.opf.name));
    if (opf) {
      for (const tag of ["title", "language", "creator", "publisher", "identifier", "description", "source"]) {
        const found = named(opf, tag)[0];
        const key = `dc:${tag}`;
        if (found && !metadata.has(key)) metadata.set(key, textOf(found));
      }
    }
  }

  const smilCache = new Map();
  const audioOf = (element, smilName) => {
    const src = element.get("src");
    if (!src) return null;
    const target = normalizePath(`${dirName(smilName)}/${decodeURIComponent(src)}`);
    return byName.has(target) ? target : null;
  };

  const smilSegments = (smilName) => {
    if (smilCache.has(smilName)) return smilCache.get(smilName);
    const root = parse(smilName);
    const found = [];
    if (root) {
      for (const audio of root.find("audio")) {
        const target = audioOf(audio, smilName);
        if (!target) continue;
        found.push({ src: target, ...clipOf(audio) });
      }
    }
    const result = merge(found);
    smilCache.set(smilName, result);
    return result;
  };

  // The pack's own words at a `text.html#id` anchor, which is what a card can show.
  const textCache = new Map();
  const textAt = (src, base) => {
    const raw = decodeURIComponent(src ?? "");
    const hash = raw.indexOf("#");
    const filePart = hash < 0 ? raw : raw.slice(0, hash);
    const anchor = hash < 0 ? "" : raw.slice(hash + 1);
    // A producer that synced only its headings writes `<text src="#h51"/>` with no
    // file named: the words live in the pack's own text document, which is the NCC.
    const name = filePart ? normalizePath(`${base}/${filePart}`) : pack.isNcc ? pack.toc.name : "";
    if (!name || !anchor) return "";
    if (!textCache.has(name)) textCache.set(name, parse(name));
    const root = textCache.get(name);
    if (!root) return "";
    let found = null;
    const walk = (node) => {
      for (const child of node.children) {
        if (!found && child.get("id") === anchor) found = child;
        walk(child);
      }
    };
    walk(root);
    return found ? textOf(found).replace(/\s+/g, " ") : "";
  };

  // Each clip of a SMIL with the line the pack synced over it: the sentence level's parts.
  const marksCache = new Map();
  const smilMarks = (smilName) => {
    if (marksCache.has(smilName)) return marksCache.get(smilName);
    const root = parse(smilName);
    const found = [];
    if (root) {
      for (const par of root.find("par")) {
        const audio = par.find("audio")[0];
        const target = audio ? audioOf(audio, smilName) : null;
        if (!target) continue;
        const words = par.find("text")[0];
        const text = textAt(words ? words.get("src") ?? "" : "", dirName(smilName));
        const clip = { src: target, ...clipOf(audio) };
        found.push({
          segments: [clip],
          text,
          spans: text ? [{ text, ...clip }] : [],
        });
      }
    }
    if (!found.length && root) {
      // A text-only pack pairs every par with words and no audio at all; the words
      // are kept so the part's audio can be made from them elsewhere.
      for (const element of named(root, "text")) {
        const words = textAt(element.get("src") ?? "", dirName(smilName));
        if (words) found.push({ segments: [], text: words, spans: [] });
      }
    }
    const result = found.length
      ? found
      : smilSegments(smilName).map((segment) => ({ segments: [segment], text: "", spans: [] }));
    marksCache.set(smilName, result);
    return result;
  };

  // The first words read out in a part; a section number read aloud first is not worth a card.
  const openingText = (marks) => marks.find((mark) => mark.text.length >= 5)?.text ?? marks[0]?.text ?? "";

  const chapterLevel = chapterLevelOf(entries_);
  const documents = anchorsByDocument(entries_);
  const pageCache = new Map();
  const chapters = [];
  let frontMatter = [];

  const place = (draft) => {
    // Open a part, or add it to the one before at a level below the chapters.
    if (level === "chapter" && draft.level > chapterLevel && chapters.length) {
      const previous = chapters[chapters.length - 1];
      previous.segments.push(...draft.segments);
      previous.spans.push(...draft.spans);
      previous.body = joinTexts([previous.body, draft.body].filter(Boolean));
    } else {
      chapters.push(draft);
    }
  };

  for (const entry of entries_) {
    const filePart = decodeURIComponent(entry.target.split("#")[0]).trim();
    const base = dirName(pack.toc.name);
    if (filePart && filePart.toLowerCase().endsWith(".smil")) {
      const smilName = normalizePath(`${base}/${filePart}`);
      if (!byName.has(smilName)) continue;
      const segments = smilSegments(smilName);
      if (entry.frontMatter) {
        frontMatter = frontMatter.concat(segments);
        continue;
      }
      const marks = smilMarks(smilName);
      if (level === "sentence") {
        for (const mark of marks) {
          chapters.push({
            title: "",
            segments: [...mark.segments],
            text: mark.text,
            body: mark.text,
            spans: [...mark.spans],
            level: entry.level,
          });
        }
        continue;
      }
      place({
        title: entry.title || `Part ${chapters.length + 1}`,
        segments: [...segments],
        text: openingText(marks),
        body: joinTexts(marks.map((mark) => mark.text).filter(Boolean)),
        spans: marks.flatMap((mark) => mark.spans),
        level: entry.level,
      });
      continue;
    }

    const document = splitDocument(entry.target);
    if (!document) continue;
    const name = normalizePath(`${base}/${document.path}`);
    if (!byName.has(name)) continue;
    if (!pageCache.has(name)) {
      const root = parse(name);
      pageCache.set(name, root ? documentTexts(root, documents.get(document.path)) : new Map());
    }
    const body = pageCache.get(name).get(document.anchor) ?? "";
    if (level === "sentence") {
      for (const sentence of sentences(body)) {
        chapters.push({ title: "", segments: [], text: sentence, body: sentence, spans: [], level: entry.level });
      }
      continue;
    }
    place({
      title: entry.title || `Part ${chapters.length + 1}`,
      segments: [],
      text: firstWords(body),
      body,
      spans: [],
      level: entry.level,
    });
  }

  if (!frontMatter.length && !pack.isNcc) frontMatter = ncxFrontMatter(tocRoot, pack.toc.name, byName);

  return {
    title: metadata.get("dc:title") || pack.root.split("/").pop() || "Untitled",
    author: metadata.get("dc:creator") || null,
    language: metadata.get("dc:language") || null,
    publisher: metadata.get("dc:publisher") || null,
    narrator: metadata.get("ncc:narrator") || null,
    identifier: metadata.get("dc:identifier") || null,
    // The print book this was read from: its ISBN is what a cover lookup asks with.
    source: metadata.get("dc:source") || null,
    totalTime: parseClock(metadata.get("ncc:totaltime")),
    chapters: chapters
      .filter((c) => c.segments.length || c.body)
      .map((c, index) => ({
        title: c.title || `Part ${index + 1}`,
        text: c.text ?? "",
        // What a part says, whether or not the pack also recorded it: a pack with
        // no audio has only this, which this page cannot turn into sound.
        body: c.body ?? "",
        // That same text one piece per synced clip, in the order the clips play.
        spans: c.spans ?? [],
        segments: merge(c.segments),
      })),
    frontMatter: merge(frontMatter),
  };
}

function nccEntries(root) {
  const body = root.find("body")[0];
  if (!body) return [];
  const out = [];
  const walk = (node) => {
    for (const child of node.children) {
      const level = HEADING_LEVELS[child.tagName.toLowerCase()];
      if (level) {
        const anchor = child.find("a")[0] ?? (child.tagName === "a" ? child : null);
        const href = anchor?.get("href") ?? child.get("href") ?? "";
        out.push({
          level,
          title: textOf(anchor ?? child),
          target: href,
          frontMatter: (child.get("class") ?? "").toLowerCase().split(/\s+/).includes("title"),
        });
      }
      walk(child);
    }
  };
  walk(body);
  return out;
}

/**
 * The outline of a DAISY 3 pack or an EPUB. Both spell the element `navPoint`
 * and put the label inside a `navLabel`, and both say how deep a point sits with
 * `class="h1".."h6"` rather than with an attribute.
 */
function ncxEntries(root) {
  const navMap = childNamed(root, "navmap");
  return navMap ? ncxPoints(navMap) : [];
}

function ncxPoints(navMap, level = 1) {
  const out = [];
  for (const navPoint of navMap.children) {
    if (localName(navPoint.tagName).toLowerCase() !== "navpoint") continue;
    const declared = navPoint.get("playLevel") ?? navPoint.get("level");
    const current = declared && /^\d+$/.test(declared) ? Number(declared) : level;
    const label = childNamed(navPoint, "navlabel") ?? navPoint;
    const text = childNamed(label, "text") ?? label;
    const content = childNamed(navPoint, "content");
    const title = text ? (text.get("value") ?? textOf(text)).trim() : "";
    const target = content ? content.get("src") ?? "" : "";
    if (target) {
      out.push({
        level: HEADING_LEVELS[(navPoint.get("class") ?? "").toLowerCase()] ?? current,
        title,
        target,
        frontMatter: false,
      });
    }
    out.push(...ncxPoints(navPoint, current + 1));
  }
  return out;
}

/** The spoken title and author a DAISY 3 NCX keeps in its own header. */
function ncxFrontMatter(root, tocName, byName) {
  const found = [];
  for (const header of [childNamed(root, "doctitle"), childNamed(root, "docauthor")]) {
    if (!header) continue;
    for (const audio of header.children) {
      if (localName(audio.tagName).toLowerCase() !== "audio") continue;
      const target = resolveTarget(audio.get("src"), dirName(tocName), byName);
      if (target) found.push({ src: target, ...clipOf(audio) });
    }
  }
  return found;
}
