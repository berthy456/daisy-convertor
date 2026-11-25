"""Write the Telmi story folder: metadata.json, nodes.json, notes.json, title assets, audios/, images/."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import audio, images, tts
from .extractor import AudioSegment, Book, Chapter
from .i18n import language

log = logging.getLogger(__name__)

CARD_SNIPPET_SECONDS = 3.0
# A book cut to the sentence level has hundreds of parts; Telmi-Sync shows this
# list, so it stops short rather than filling the story's description.
LISTED_PARTS = 30

_UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://wiki.telmi.fr/developments/daisy2telmi")

# Telmi-Sync's readStoryMetadata() deletes any story folder missing one of these.
REQUIRED_ENTRIES = (
    "audios",
    "images",
    "metadata.json",
    "nodes.json",
    "title.mp3",
    "title.png",
)

class StoryError(RuntimeError):
    """Raised when a story cannot be produced."""


@dataclass(frozen=True)
class StoryReport:
    out_dir: Path
    stages: tuple[str, ...]
    title_source: str
    cards: int = 0
    captions: int = 0
    parts: int = 0
    audio_mode: str = "encoded"


# The two machines whose screen is big enough to read a sentence on. A Lunii's is
# not, and a Merlin card has none at all.
CAPTION_PLAYERS = ("telmi", "flam")


# What a card can say about the part it introduces. The title patterns need a
# pack whose parts carry synced text; where they do not, they fall back to
# `chapter`, which every pack can say.
ANNOUNCE_MODES = (
    "prompt",
    "chapter",
    "number",
    "number_of",
    "title",
    "number_title",
    "title_number",
    "off",
)


@dataclass(frozen=True)
class Announce:
    """What each chapter card says, and whether the story advances by itself.

    `prompt` is the default because the player stops at the end of a chapter:
    the card has to say which chapter is next and that OK starts it.
    """

    mode: str = "prompt"
    language: str = "en"
    autoplay: bool = False
    # Whether the drawn card counts the parts as well as numbering them.
    show_total: bool = True
    engine: str = tts.AUTO
    voice: str | None = None
    command: str | None = None
    python: str | None = None
    clip: str | None = None
    device: str = tts.AUTO
    speed: str = "normal"

    def phrase(self, number: int, total: int | None = None, title: str = "") -> str | None:
        """What the card says for one part.

        The DAISY outline names its parts, so a card can read that name instead of
        a bare number. A pack whose parts carry no text has nothing to read, so
        every title pattern falls back to the numbered one.
        """
        if self.mode == "off":
            return None
        lang = language(self.language)
        chapter = lang.chapter.format(n=number)
        if self.mode == "prompt":
            return f"{chapter} {lang.next_prompt}"
        if self.mode == "number":
            return str(number)
        if self.mode == "number_of":
            return lang.number_of.format(n=number, total=total) if total else chapter
        if not title:
            return chapter
        if self.mode == "title":
            return title
        if self.mode == "number_title":
            return lang.number_title.format(n=number, title=title)
        if self.mode == "title_number":
            return lang.title_number.format(n=number, title=title)
        return chapter


def story_uuid(book: Book) -> str:
    """Stable per book, so re-converting the same pack yields the same story id."""
    seed = book.identifier or f"{book.title}|{book.author or ''}|{len(book.chapters)}"
    return str(uuid.uuid5(_UUID_NAMESPACE, seed))


def normalize_file_name(value: str) -> str:
    """Telmi-Sync's stringNormalizeFileName: strip accents, collapse odd characters to spaces."""
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(c for c in decomposed if not 0x300 <= ord(c) <= 0x36F)
    return re.sub(r"[^a-zA-Z0-9\-.]+", " ", stripped)


def slugify(value: str) -> str:
    """Telmi-Sync's stringSlugify."""
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(c for c in decomposed if not 0x300 <= ord(c) <= 0x36F)
    return re.sub(r"[^a-z0-9]+", "-", stripped.lower())


def story_dir_name(title: str, story_id: str, age: int = 0, category: str = "", fallback: str = "") -> str:
    """Telmi-Sync's `generateDirNameStory`: `<category>_<age>_<title>_<uuid>`.

    A title outside the Latin alphabet - `ごんぎつね`, an Arabic one - normalizes to
    nothing, and the name the player shows would be a blank with a uuid after it. The
    pack's own file name is the next thing the user can read.
    """
    name = normalize_file_name(title).strip() or normalize_file_name(fallback).strip() or "story"
    return (
        f"{normalize_file_name(category)[:32]}_"
        f"{('0' if age < 10 else '') + str(age)}_"
        f"{name[:32]}_"
        f"{slugify(story_id)[:36]}"
    )


def build_metadata(book: Book) -> dict:
    """`metadata.json`, using the keys Telmi-Sync reads."""
    metadata = {
        "title": book.title,
        "uuid": story_uuid(book),
        "image": images.COVER_IMAGE,
        "category": "",
        "version": 0,
        "age": 0,
    }
    shown = book.chapters[:LISTED_PARTS]
    details = [f"{chapter.title} ({index + 1}/{len(book.chapters)})" for index, chapter in enumerate(shown)]
    if len(book.chapters) > len(shown):
        details.append(f"... {len(book.chapters) - len(shown)} more")
    credits = [
        f"{label}: {value}"
        for label, value in (
            ("Author", book.author),
            ("Narrator", book.narrator),
            ("Publisher", book.publisher),
            ("Language", book.language),
        )
        if value
    ]
    description = "\n".join(f"- {line}" for line in details)
    if credits:
        description = " - ".join(credits) + "\n" + description
    metadata["description"] = description
    return metadata


def build_nodes(
    chapter_names: Sequence[str],
    card_audio: Sequence[str | None] | None = None,
    card_autoplay: bool = False,
    chapter_images: Sequence[str | None] | None = None,
) -> dict:
    """Chapter-menu `nodes.json`, mirroring Telmi-Sync's own audio-list generator.

    `m<k>` is a menu card naming chapter k, `s<k>` the chapter audio.
    `ok` on the last chapter is null: the published packs use null, not a
    wrap-around, to end a story.

    A card plays its `audio` as soon as it is drawn, whether or not autoplay is
    on (`readStage` calls `audio_play` for any defined audio); autoplay only
    decides whether the player waits for OK afterwards.

    A chapter stage shows the card it was read from until its own picture, if the
    story has one, replaces it: `chapter_images` is that picture per chapter.
    """
    if not chapter_names:
        raise StoryError("no chapters to build a story from")

    if card_audio is None:
        card_audio = [None] * len(chapter_names)
    if chapter_images is None:
        chapter_images = [None] * len(chapter_names)

    stages: dict[str, dict] = {}
    actions: dict[str, list[dict]] = {"m": [{"stage": f"m{k}"} for k in range(len(chapter_names))]}

    for k in range(len(chapter_names)):
        stages[f"m{k}"] = {
            "image": f"m{k}.png",
            "audio": card_audio[k],
            "ok": {"action": f"s{k}", "index": 0},
            "home": None,
            "control": {"ok": True, "home": True, "autoplay": card_autoplay},
        }
        stages[f"s{k}"] = {
            "image": chapter_images[k],
            "audio": f"{chapter_names[k]}.mp3",
            "ok": None if k == len(chapter_names) - 1 else {"action": "m", "index": k + 1},
            "home": {"action": "m", "index": k},
            "control": {"ok": False, "home": True, "autoplay": True},
        }
        actions[f"s{k}"] = [{"stage": f"s{k}"}]

    return {"startAction": {"action": "m", "index": 0}, "stages": stages, "actions": actions}


def build_notes(book: Book) -> dict:
    """`notes.json`: the chapter titles, in the slots Telmi-Sync's Studio reads."""
    notes: dict[str, dict] = {}
    for k, chapter in enumerate(book.chapters):
        notes[f"m{k}"] = {"title": f"m{k}", "notes": chapter.title}
        notes[f"s{k}"] = {"title": chapter.title, "notes": ""}
    return notes


def build_card_audio(
    count: int,
    announce: Announce,
    audios: Path,
    loudness: str | None = None,
    titles: Sequence[str] = (),
) -> tuple[list[str | None], bool]:
    """Speak one announcement per card. A failure silences them all: the same
    engine would fail again, and a silent card is still a valid story."""
    phrases = {
        k: announce.phrase(k + 1, count, titles[k] if k < len(titles) else "") for k in range(count)
    }
    spoken = [k for k, phrase in phrases.items() if phrase is not None]
    if not spoken:
        return [None] * count, 0
    try:
        tts.synthesize_all(
            [phrases[k] for k in spoken],
            announce.language,
            [audios / f"c{k}.mp3" for k in spoken],
            engine=announce.engine,
            voice=announce.voice,
            command=announce.command,
            python=announce.python,
            clip=announce.clip,
            device=announce.device,
            speed=announce.speed,
            loudness=loudness,
        )
    except (tts.TtsError, audio.AudioError) as exc:
        log.warning("chapter cards stay silent: %s", exc)
        return [None] * count, 0
    cards: list[str | None] = [None] * count
    for k in spoken:
        cards[k] = f"c{k}.mp3"
    return cards, len(spoken)


def narrate(text: str, announce: Announce | None, dest: Path) -> None:
    """Speak a part of a pack that carries no audio, on the engine the cards use.

    Unlike a card, a part that cannot be spoken leaves the story without a scene,
    so the failure is raised rather than logged.
    """
    voice = announce or Announce()
    try:
        tts.narrate(
            text,
            voice.language,
            dest,
            engine=voice.engine,
            voice=voice.voice,
            command=voice.command,
            python=voice.python,
            clip=voice.clip,
            device=voice.device,
            speed=voice.speed,
        )
    except tts.TtsError as exc:
        raise StoryError(f"this pack has no audio, and its text cannot be spoken: {exc}") from exc


def _span(segments: Sequence[AudioSegment]) -> float | None:
    """Seconds the segments cover, or None when the pack left a range open."""
    total = 0.0
    for segment in segments:
        if segment.begin is None or segment.end is None:
            return None
        total += segment.end - segment.begin
    return total


def build_snippets(chapters: Sequence[Chapter], audios: Path) -> list[str | None]:
    """The first seconds of each part, so a card can be told apart by ear.

    A book cut to the sentence level has hundreds of parts, and no word in any
    language would say which one a card is; its own opening sound does. A part
    shorter than the clip gets no second copy: its card plays the part.
    """
    cards: list[str | None] = []
    for k, chapter in enumerate(chapters):
        span = _span(chapter.segments)
        if span is not None and span <= CARD_SNIPPET_SECONDS:
            cards.append(f"s{k}.mp3")
            continue
        name = f"c{k}.mp3"
        try:
            audio.render(chapter.segments, audios / name, limit=CARD_SNIPPET_SECONDS)
        except audio.AudioError as exc:
            log.warning("cards stay silent: %s", exc)
            return [None] * len(chapters)
        cards.append(name)
    return cards


def build_story(
    book: Book,
    out_dir: Path,
    announce: Announce | None = None,
    cover: Path | None = None,
    cut: str = "chapter",
    crop: str = "center",
    lunii_dir: Path | None = None,
    player: str = "telmi",
    view: dict | None = None,
    negative: bool = False,
    captions: bool = False,
) -> StoryReport:
    """Render every chapter to `audios/` and write the story's JSON files and images.

    `cover` is the user's picture: it becomes the title image, the cover and the
    background of every card, which carries the chapter number and the total.
    `crop` says which band of it survives the three different aspect ratios, and
    `view` the frame moved or enlarged off that band (see images.crop_view).
    `player` decides the size every picture is drawn at.

    `lunii_dir` gets the same pictures drawn again for a greyscale Lunii screen:
    a plain player has no colour panel, so its set is drawn light-on-dark the
    other way round and flattened to the sixteen greys the card keeps. `negative`
    turns that set inside out, as an old film's negative reads.

    `captions` gives each part a picture of its own words, shown while its audio
    plays. That needs the book cut to sentences — one sentence per part, one
    picture per sentence — and a screen big enough to read a sentence on.
    """
    if not book.chapters:
        raise StoryError("the pack contains no chapter with audio or text")
    silent = [c for c in book.chapters if not c.segments and not c.body]
    if silent:
        raise StoryError(
            f"{len(silent)} chapter(s) have neither audio nor text, starting with {silent[0].title!r}"
        )
    if captions and cut != "sentence":
        raise StoryError("the sentence pictures need one sentence per part: --cut sentence")
    if captions and player not in CAPTION_PLAYERS:
        raise StoryError(f"the sentence pictures are drawn for a {' or a '.join(CAPTION_PLAYERS)} screen, not {player}")

    audios = out_dir / "audios"
    scene = out_dir / "images"
    audios.mkdir(parents=True, exist_ok=True)
    scene.mkdir(parents=True, exist_ok=True)

    count = len(book.chapters)
    chapter_names = [f"s{k}" for k in range(count)]
    modes = set()
    spoken = [c for c in book.chapters if not c.segments]
    if spoken:
        log.info(
            "this pack carries no audio: %d part(s) will be spoken from their text",
            len(spoken),
        )
    for name, chapter in zip(chapter_names, book.chapters):
        if chapter.segments:
            log.info("rendering %s (%d audio clip group(s)) <- %s", name, len(chapter.segments), chapter.title)
            modes.add(audio.render(chapter.segments, audios / f"{name}.mp3"))
        else:
            log.info("speaking %s (%d characters) <- %s", name, len(chapter.body), chapter.title)
            narrate(chapter.body, announce, audios / f"{name}.mp3")
            modes.add("spoken")

    cards = [None] * count
    spoken = 0
    if announce is not None and announce.mode != "off":
        if cut == "chapter":
            # The card is heard over the narration, so it is levelled against it:
            # one pack's audio sits at -18 LUFS, another's at -25.
            story = audio.loudness(audios / f"{chapter_names[0]}.mp3")
            where = f"; the card is levelled to {tts.card_target(story):.1f} LUFS" if story is not None else ""
            log.info("speaking %d chapter card(s) in %s%s", count, language(announce.language).name, where)
            cards, spoken = build_card_audio(
                count, announce, audios, tts.card_loudness(story), [c.title for c in book.chapters]
            )
        else:
            log.info("playing the first %.0f s of each of the %d part(s) on its card", CARD_SNIPPET_SECONDS, count)
            cards = build_snippets(book.chapters, audios)
            spoken = len(cards)

    # The player plays title.mp3 when the story is selected, so the pack's own
    # spoken title belongs there rather than in a second stage inside the story.
    title_path = out_dir / "title.mp3"
    if book.front_matter:
        log.info("rendering title.mp3 from the pack's front matter")
        modes.add(audio.render(book.front_matter, title_path))
        title_source = "front matter"
    elif all(not chapter.segments for chapter in book.chapters):
        log.info("a spoken story has no recorded title; speaking %r as title.mp3", book.title)
        narrate(book.title, announce, title_path)
        title_source = "spoken title"
    else:
        log.info("pack has no front-matter audio; writing 1s of silence as title.mp3")
        audio.render_silence(title_path, 1.0)
        title_source = "generated silence"

    code = announce.language if announce is not None else "en"
    show_total = announce.show_total if announce is not None else True
    labels = [
        images.card_label(code, k + 1, count, cut == "chapter", show_total) for k in range(count)
    ]
    texts = [chapter.text for chapter in book.chapters]
    scene_size = images.screen(player)[0]
    # A part whose words the font cannot write gets no picture: its audio plays on
    # the card's own picture, as it does when no caption was asked for.
    captioned = (
        [bool(images.caption_lines(text, *scene_size)) for text in texts] if captions else [False] * count
    )

    def draw(target: Path, style: str) -> None:
        """The title image, the cover and every card, at one player's sizes."""
        scene, cover_size = images.screen(player)
        # A four-grey card is the Lunii's, and carries its number alone.
        cards = [str(k + 1) for k in range(count)] if style == "bw" else labels
        # The negative is asked for where the screen is hard to read: the greys of
        # a Lunii behind its plastic window. A colour card has nothing to invert.
        grey = negative and style == "bw"
        if cover is not None:
            try:
                images.write_cover(cover, target, cards, texts, crop, style, scene, cover_size, view, grey)
                return
            except images.ImageError as exc:
                # The audio is already rendered by now, and a picture the machine
                # cannot read is no reason to throw it away: the cards fall back to
                # the placeholder and the pack still plays.
                log.error("drawing without the cover %s: %s", cover, exc)
        (target / "images").mkdir(parents=True, exist_ok=True)
        for index, text in enumerate(texts):
            (target / "images" / f"m{index}.png").write_bytes(
                images.card_png(cards[index], text, *scene, style=style, negative=grey)
            )
        (target / images.TITLE_IMAGE).write_bytes(
            images.placeholder_png(*scene, style=style, negative=grey)
        )
        (target / images.COVER_IMAGE).write_bytes(
            images.placeholder_png(*cover_size, style=style, negative=grey)
        )

    def draw_captions(target: Path, style: str) -> None:
        """One picture per part: its whole sentence, on the card's own ground."""
        grey = negative and style == "bw"
        for index, (text, shown) in enumerate(zip(texts, captioned)):
            if shown:
                (target / "images" / f"s{index}.png").write_bytes(
                    images.caption_png(text, *scene_size, style=style, negative=grey)
                )

    log.info("drawing %d card(s)%s", count, "" if cover is None else f" from the cover {cover.name}")
    draw(out_dir, "colour")
    if captions:
        log.info("drawing %d sentence picture(s) for the %s screen", sum(captioned), player)
        draw_captions(out_dir, "colour")
    if lunii_dir is not None:
        log.info("drawing the same %d card(s) in greys for the Lunii", count)
        draw(lunii_dir, "bw")

    metadata = build_metadata(book)
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "nodes.json").write_text(
        json.dumps(
            build_nodes(
                chapter_names,
                cards,
                announce.autoplay if announce else False,
                [f"s{k}.png" if shown else None for k, shown in enumerate(captioned)],
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "notes.json").write_text(
        json.dumps(build_notes(book), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    audio_mode = modes.pop() if len(modes) == 1 else "mixed"
    return StoryReport(
        out_dir=out_dir,
        stages=tuple(chapter_names),
        title_source=title_source,
        cards=spoken,
        captions=sum(captioned),
        parts=count,
        audio_mode=audio_mode,
    )


def write_story_zip(story_dir: Path, parent: Path, player: str = "telmi") -> Path:
    """Package the story folder for Telmi-Sync's zip import.

    `findDirectory` in ConvertZip.js counts the Telmi marker files per zip
    directory and needs exactly four, so the folder goes in nested one level
    rather than unpacked at the archive root. The archive is named after the
    machine the story was drawn for, so the file in the download folder says
    which player it is for.
    """
    zip_path = parent / f"{story_dir.name}-{player}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in sorted(story_dir.rglob("*")):
            if entry.is_file():
                archive.write(entry, f"{story_dir.name}/{entry.relative_to(story_dir)}")
    return zip_path
