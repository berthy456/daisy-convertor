// The page dictionaries: every language carries the same keys as English.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const { LANGUAGES, NAMES, RTL, UI, chapterWords, codeOf, guess, pickLanguage, storyLanguage, text } = await import(
  "../../web/i18n.js"
);

// A pack names its language under "ISO 639", and which ISO 639 it means, and whether
// it names the language in English or in its own words, is up to whoever made the pack.
const DECLARED = [
  ["FR", "fr"],
  ["fr", "fr"],
  ["fre", "fr"],
  ["fra", "fr"],
  ["fr-FR", "fr"],
  ["Français", "fr"],
  ["eng", "en"],
  ["en-GB", "en"],
  ["English", "en"],
  ["deu", "de"],
  ["ger", "de"],
  ["Deutsch", "de"],
  ["dut", "nl"],
  ["nld", "nl"],
  ["spa", "es"],
  ["por", "pt"],
  ["ita", "it"],
  ["ara", "ar"],
  ["tur", "tr"],
  ["chi", "zh"],
  ["zho", "zh"],
];

// One page per language the page has words for, each a few sentences of ordinary story.
const PAGES = {
  ar: "نظرت الثعوبة إلى البنت ونظرت إليه، ومشيا في الغابة.",
  de: "Der Fuchs sah das Mädchen an und es sah zurück, und sie gingen durch den Wald.",
  en: "The fox looked at the girl and she looked back at him, and they walked on through the wood.",
  es: "El zorro miró a la niña y ella lo miró también, y caminaron por el bosque.",
  fr: "La fée lui répondit avec douceur, et le bateau reprit sa route vers l'île.",
  it: "La volpe guardò la bambina e lei guardò lui, e camminarono nel bosco.",
  nl: "De vos keek naar het meisje en zij keek terug, en ze liepen door het bos.",
  pt: "A raposa olhou para a menina e ela olhou de volta, e caminharam pela floresta.",
  tr: "Tilki kıza baktı ve kız ona baktı ve ormanda babasının evine kadar yürüdüler.",
  zh: "狐狸看着那个女孩，她也看着他，他们走过了树林。",
};

test("every language translates every key", () => {
  const keys = Object.keys(UI.en).sort();
  for (const code of LANGUAGES) {
    assert.ok(UI[code], `${code} has no dictionary`);
    assert.deepEqual(Object.keys(UI[code]).sort(), keys, `${code} keys differ from en`);
  }
});

// "source" is French too, and Spanish writes `error:` the same way, so those two
// keys may match English; `card_number` is digits around a slash in every language,
// and `card_number_one` is the digits alone;
// Telmi, Lunii, the Flam and the Merlin are the players' names, which no language
// translates, and so are the two search engines; "beta" and "Zoom" are the words
// the Latin-script languages all borrow as is; so does the machine's "OK", which the
// five Latin-script dictionaries write as it is, while Arabic, Chinese and Turkish
// write their own; Dutch writes the button's "Stop" and the slider's "Contrast" as
// it is too.
// Anything else that matches was left untranslated.
test("no language is left in English by accident", () => {
  const allowed = new Set([
    "footer_line",
    "log_error",
    "card_number",
    "card_number_one",
    "fmt_telmi",
    "fmt_lunii",
    "fmt_lunii_pk",
    "fmt_flam",
    "fmt_merlin",
    "cover_search_ddg",
    "cover_search_google",
    "crop_zoom",
    "contrast",
    "cover_stop",
    "emu_ok",
  ]);
  for (const code of LANGUAGES) {
    if (code === "en") continue;
    const copied = Object.keys(UI.en).filter(
      (key) => UI[code][key] === UI.en[key] && !allowed.has(key),
    );
    assert.deepEqual(copied, [], `${code} copies English for ${copied.join(", ")}`);
  }
});

test("the picker names each language in itself", () => {
  assert.deepEqual(Object.keys(NAMES).sort(), [...LANGUAGES].sort());
  assert.equal(NAMES.fr, "Français");
});

test("the browser language picks the page language", () => {
  assert.equal(pickLanguage(null, "fr-FR"), "fr");
  assert.equal(pickLanguage(null, "zh-Hans-CN"), "zh");
  assert.equal(pickLanguage(null, "pt-BR"), "pt");
  assert.equal(pickLanguage(null, "sw"), "en");
  assert.equal(pickLanguage(null, undefined), "en");
});

test("a saved choice beats the browser", () => {
  assert.equal(pickLanguage("tr", "de-DE"), "tr");
  assert.equal(pickLanguage("klingon", "de-DE"), "de");
});

test("the card label fills its numbers", () => {
  assert.equal(text("en", "card_label", { n: 2, total: 23 }), "Chapter 2/23");
  assert.equal(text("ar", "card_label", { n: 2, total: 23 }), "الفصل 2/23");
});

test("each language names a chapter the way an outline writes it", () => {
  assert.deepEqual(chapterWords("en"), ["part", "chapter"]);
  assert.ok(chapterWords("fr").includes("chapitre"));
  assert.ok(chapterWords("zh").includes("章"));
  assert.ok(chapterWords("ar").includes("الفصل"));
  for (const code of LANGUAGES) {
    assert.ok(chapterWords(code).includes("part"), `${code} lost the word "part"`);
    assert.ok(!chapterWords(code).includes("n"), `${code} read its own placeholder`);
  }
});

test("an unknown key or language degrades instead of throwing", () => {
  assert.equal(text("nl", "no_such_key"), "no_such_key");
  assert.equal(text("xx", "card_label", { n: 1, total: 4 }), "Chapter 1/4");
});

test("Arabic is the only right-to-left language", () => {
  assert.ok(RTL.has("ar"));
  assert.ok(!RTL.has("ur"));
  assert.ok(!RTL.has("zh"));
});

test("a pack names its language in whichever way it does", () => {
  for (const [tag, code] of DECLARED) assert.equal(codeOf(tag), code, tag);
  for (const tag of ["ja", "jpn", "pol", "", null, undefined]) {
    assert.equal(codeOf(tag), null, String(tag));
  }
});

test("a page reads as the language it is written in", () => {
  for (const [code, page] of Object.entries(PAGES)) assert.equal(guess(page), code, code);
});

test("japanese is not read as chinese", () => {
  assert.equal(guess("狐は女の子を見て、彼女も彼を見た。"), null);
});

test("a page with no words in it gives nothing to read", () => {
  assert.equal(guess(""), null);
  assert.equal(guess("  \n\t "), null);
  assert.equal(guess("123 456 — 789"), null);
});

test("a page too short to tell is not guessed", () => {
  assert.equal(guess("the fox"), null);
});

test("a capitalised turkish letter still counts", () => {
  assert.equal(guess("İLK SÖZ: Tilki kıza baktı ve kız ona baktı ve ormanda yürüdüler."), "tr");
});

test("the pack's own text outranks the language it declares", () => {
  assert.deepEqual(storyLanguage("en", PAGES.fr, "de"), { code: "fr", source: "text" });
});

test("the language a pack declares is used when its text gives nothing", () => {
  assert.deepEqual(storyLanguage("de", "", "en"), { code: "de", source: "metadata" });
});

test("the pack's own text is read when it declares nothing", () => {
  assert.deepEqual(storyLanguage(null, PAGES.fr, "en"), { code: "fr", source: "text" });
});

test("a declared language the page has no words for says so", () => {
  assert.deepEqual(storyLanguage("ja", "", "en"), { code: "en", source: "declared" });
});

test("a pack that is audio only falls back to the machine", () => {
  assert.deepEqual(storyLanguage(null, "", "tr"), { code: "tr", source: "machine" });
});

test("every label the page asks for is in every dictionary", async () => {
  const html = await readFile(`${ROOT}web/index.html`, "utf8");
  const used = [...html.matchAll(/data-i18n(?:-html|-placeholder|-aria)?="([a-z_]+)"/g)].map((match) => match[1]);
  assert.ok(used.length > 50, `only ${used.length} labels found in the page`);
  for (const code of LANGUAGES) {
    for (const key of used) assert.ok(key in UI[code], `${code} has no words for ${key}`);
  }
});
