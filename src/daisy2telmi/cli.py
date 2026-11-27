"""Command line entry point: daisy-convertor <daisy-pack> <output-folder>."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from . import __version__, audio, chapters, covers, images, merlin, playlist, settings, tts
from .chapters import ChapterError
from .detector import DaisyPackError, open_pack
from .extractor import CUT_LEVELS, extract
from .i18n import LANGUAGES, language
from .lunii import LuniiError, write_studio_zip
from .lunii_pk import write_pack
from .merlin import MerlinError
from .telmi_builder import (
    ANNOUNCE_MODES,
    PAUSE_LEVELS,
    Announce,
    StoryError,
    build_story,
    card_language,
    part_title,
    story_dir_name,
    story_uuid,
    write_story_zip,
)

log = logging.getLogger("daisy-convertor")

# The players, and the archive each one imports. Telmi, the Flam and a commercial
# Lunii card carry colour; a plain Lunii screen has sixteen greys, so its cards are
# redrawn. The Merlin has no screen and no story graph: it takes a card of flat files.
# The mp3 pack has no story graph and no pictures either: it is for an ordinary
# audio player, which is why it is the one format written without a card.
FORMATS = ("telmi", "lunii", "lunii-pk", "flam", "merlin", "mp3")
CARD_FORMATS = tuple(fmt for fmt in FORMATS if fmt != "mp3")
PLAYER = {
    "telmi": "Telmi Sync",
    "lunii": "the Lunii",
    "lunii-pk": "a commercial Lunii card",
    "flam": "the Flam launcher",
    "merlin": "the Merlin story-teller",
    "mp3": "an ordinary audio player",
}
# The two Lunii exports hold the same story; the archive and the pictures differ:
# STUdio's zip with grey cards, or the layout the card keeps with colour ones.
LUNII = ("lunii", "lunii-pk")
# The formats whose screen is big enough to read a sentence on. A Lunii's is not, and
# a Merlin card has no screen at all.
CAPTION_FORMATS = ("telmi", "flam")

# The Merlin writes itself, with no official SDK and no way to try a card on the
# machine that made the format, so it is offered but not promised.
BETA = ("merlin",)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="daisy-convertor",
        description="Convert a DAISY or EPUB audio book pack into a Telmi story folder.",
    )
    parser.add_argument(
        "input", type=Path, help="path to a DAISY pack (.zip, .epub or already extracted folder)"
    )
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="folder to create the story folder in; the story folder gets Telmi's "
        "`<category>_<age>_<title>_<uuid>` name unless this path already has it",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log every ffmpeg invocation")
    parser.add_argument("-f", "--force", action="store_true", help="write into the output folder even if it is not empty")
    parser.add_argument(
        "--cover",
        metavar="IMAGE",
        type=Path,
        help="draw every chapter card over this picture instead of the placeholder",
    )
    parser.add_argument(
        "--find-cover",
        action="store_true",
        help="ask Open Library, Google Books, Apple Books and the Open Library search, in that "
        "order, for the cover of the print book the pack names, and draw the cards over that "
        "instead of the placeholder",
    )
    parser.add_argument(
        "--cover-crop",
        choices=images.CROP_MODES,
        help="how the cover is cropped to fit the chosen player's screens: the middle "
        "(default), the top, or the bottom",
    )
    parser.add_argument(
        "--cover-zoom",
        type=float,
        default=1.0,
        metavar="FACTOR",
        help=f"scale the cover by FACTOR ({images.MIN_ZOOM} to {images.MAX_ZOOM}): 0 shows the whole "
        "cover with the frame's empty room filled by bands, 1 fills the frame, and more than 1 "
        "enlarges it past the frame so it can be moved sideways as well",
    )
    look = parser.add_argument_group("before converting")
    look.add_argument("--list", action="store_true", help="print the pack's chapters and stop")
    look.add_argument(
        "--cut",
        choices=CUT_LEVELS,
        help="where to cut the book: at its chapters (default), at every paragraph the pack "
        "marks, or at every sentence it synced",
    )
    look.add_argument(
        "--captions",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="give each part a picture of its own words, shown while its audio plays; "
        "needs --cut sentence, and a Telmi or a Flam screen (--no-captions for the card's "
        "own picture, as usual)",
    )
    look.add_argument(
        "--preview",
        metavar="FOLDER",
        type=Path,
        help="write one MP3 per chapter into FOLDER and stop, so each chapter can be listened to",
    )
    look.add_argument(
        "--drop",
        metavar="CHAPTERS",
        help="leave these chapters out of the story, by the numbers --list prints: '2' or '2,4-6'",
    )

    speech = parser.add_argument_group("chapter card speech")
    speech.add_argument(
        "--announce",
        choices=ANNOUNCE_MODES,
        help="what each chapter card says: nothing, the number, the number and the total, "
        "the part's title (alone or with the number), 'chapter N', the number and a "
        "prompt to continue, or 'snippet' to play the part's own opening instead",
    )
    speech.add_argument(
        "--card-seconds",
        type=float,
        default=None,
        help="how many seconds of the part a card plays: with --announce snippet, and with any --cut below chapter",
    )
    speech.add_argument(
        "--card-total",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="draw how many parts there are on each chapter card, as in '4/12' (--no-card-total for the number alone)",
    )
    speech.add_argument(
        "--card-word",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="write the word for the part beside its number on each chapter card, as in 'CHAPTER 4' "
        "(--no-card-word for the number alone)",
    )
    speech.add_argument(
        "--card-title",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="draw the part's own name beside its number on each chapter card, as in 'CHAPTER 4/12, THE WOLF' "
        "(--no-card-title for the number alone)",
    )
    speech.add_argument(
        "--card-negative",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="draw the black-and-white Lunii cards as a negative of the cover, light and dark swapped "
        "(--no-card-negative for the cover as it is)",
    )
    speech.add_argument(
        "--card-contrast",
        type=float,
        default=None,
        metavar="FACTOR",
        help="pull the cover's lights and shadows further apart on every card picture: 1 leaves them "
        "as they are, more than 1 separates them, less than 1 brings them together (0.5 to 2)",
    )
    speech.add_argument(
        "--language",
        choices=sorted(LANGUAGES),
        help="language of the chapter cards and the voice that reads them; by default the book's own, "
        "read from the pack's metadata or from the words it carries",
    )
    speech.add_argument(
        "--autoplay",
        action="store_true",
        help="advance to the next part when the card's audio ends, instead of waiting for OK",
    )
    speech.add_argument(
        "--pause-after",
        choices=PAUSE_LEVELS,
        help="where an autoplaying story waits for OK: after each sentence, paragraph or chapter, "
        "or never for full autoplay; a story that does not autoplay waits at its chapters whatever "
        "is given here",
    )
    speech.add_argument(
        "--tts",
        choices=tts.ENGINES,
        help="speech engine for the cards: auto (the default) takes the lightest one found — "
        "Piper when it is installed and has a voice for the language, then espeak-ng, "
        "macOS say, Windows speech",
    )
    speech.add_argument(
        "--tts-voice",
        help="voice for the engine: a Piper voice name, a Coqui model, an espeak-ng or say voice",
    )
    speech.add_argument(
        "--tts-clip",
        help="a few seconds of someone speaking, for --tts chatterbox or --tts coqui to read with",
    )
    speech.add_argument(
        "--tts-speed",
        choices=tts.SPEEDS,
        help="how fast the cards are read; espeak-ng, Piper, say and Windows speech all take it",
    )
    speech.add_argument(
        "--tts-expression",
        choices=tts.EXPRESSIONS,
        help="how expressive Chatterbox is: natural keeps its own defaults, expressive reads with "
        "the tuning its README suggests for expressive speech",
    )
    speech.add_argument(
        "--read-voice",
        help="voice for the book's own text, where a pack carries none: a whole chapter is not read "
        "the way a card's few words are, so it often wants a different voice",
    )
    speech.add_argument(
        "--read-clip",
        help="reference clip the book's text is read with, for --tts chatterbox or --tts coqui",
    )
    speech.add_argument(
        "--read-device",
        choices=tts.DEVICES,
        help="what a neural engine runs on when it reads the book's own text",
    )
    speech.add_argument(
        "--read-speed",
        choices=tts.SPEEDS,
        help="how fast the book's own text is read",
    )
    speech.add_argument(
        "--read-expression",
        choices=tts.EXPRESSIONS,
        help="how expressive Chatterbox is when it reads the book's own text",
    )
    speech.add_argument(
        "--tts-python",
        help="interpreter the engine runs in, for a Python where its package was pip-installed",
    )
    speech.add_argument(
        "--tts-device",
        choices=tts.DEVICES,
        help="what a neural engine runs on: auto (default) takes the fastest device for "
        "Chatterbox and the CPU for Coqui, mps the Apple GPU, cuda an Nvidia one",
    )
    speech.add_argument(
        "--tts-command",
        help="command template for --tts command, using {text}, {out} and {lang}; "
        "for example 'my-tts {text} {out}' with any Chatterbox-style model behind it",
    )
    parser.add_argument(
        "--format",
        choices=FORMATS,
        help="which player the story is written for: Telmi (default), a Lunii, whose "
        "cards are redrawn in its sixteen greys, a commercial Lunii .pk, the same "
        "story in the layout the card itself keeps, a Flam, a Merlin story-teller "
        "(beta), whose card holds flat files and a playlist.bin, or an mp3 playlist "
        "pack: one mp3 per chapter, a spoken chapter name before each, the book's "
        "text and four playlists, for any player that is not a story-teller",
    )
    parser.add_argument(
        "--merlin-merge",
        metavar="PLAYLIST",
        type=Path,
        help="with --format merlin, add the story to this playlist.bin read off the card "
        "instead of writing one holding only the story",
    )
    parser.add_argument("--version", action="version", version=f"daisy-convertor {__version__}")
    args = parser.parse_args(argv)
    if args.output is None and not (args.list or args.preview):
        parser.error("an output folder is required unless --list or --preview is given")
    return args


def announce_options(args: argparse.Namespace) -> Announce:
    """Saved settings, overridden by whatever was passed on the command line."""
    saved = settings.load()
    chosen = args.language or saved["speech_language"]
    return Announce(
        mode=args.announce or saved["announce"],
        seconds=saved["card_seconds"] if args.card_seconds is None else args.card_seconds,
        language=chosen or saved["language"],
        auto_language=chosen is None,
        autoplay=bool(args.autoplay or saved["autoplay"]),
        pause_after=args.pause_after or saved["pause_after"],
        show_total=saved["card_total"] if args.card_total is None else args.card_total,
        show_word=saved["card_word"] if args.card_word is None else args.card_word,
        show_title=saved["card_title"] if args.card_title is None else args.card_title,
        engine=args.tts or saved["tts"],
        voice=args.tts_voice or saved["voice"],
        command=args.tts_command or saved["command"],
        python=args.tts_python or saved["python"],
        clip=args.tts_clip or saved["clip"],
        device=args.tts_device or saved["device"],
        speed=args.tts_speed or saved["speed"],
        expression=args.tts_expression or saved["expression"],
        read_voice=args.read_voice or saved["read_voice"],
        read_clip=args.read_clip or saved["read_clip"],
        read_device=args.read_device or saved["read_device"],
        read_speed=args.read_speed or saved["read_speed"],
        read_expression=args.read_expression or saved["read_expression"],
        contrast=saved["card_contrast"] if args.card_contrast is None else args.card_contrast,
    )


def cut_level(args: argparse.Namespace) -> str:
    """Saved cut level, overridden by --cut."""
    return args.cut or settings.load()["cut"]


def captions(args: argparse.Namespace) -> bool:
    """Saved sentence-picture choice, overridden by --captions or --no-captions."""
    return settings.load()["captions"] if args.captions is None else args.captions


def cover_crop(args: argparse.Namespace) -> str:
    """Saved crop choice, overridden by --cover-crop."""
    return args.cover_crop or settings.load()["cover_crop"]


def card_negative(args: argparse.Namespace) -> bool:
    """Saved negative choice, overridden by --card-negative."""
    return settings.load()["card_negative"] if args.card_negative is None else args.card_negative


def output_format(args: argparse.Namespace) -> str:
    """Saved player choice, overridden by --format."""
    return args.format or settings.load()["format"]


def inspect(
    input_path: Path,
    preview_dir: Path | None = None,
    drop: str | None = None,
    cut: str = "chapter",
) -> int:
    """Print what each cut level gives, then the parts at the chosen one."""
    try:
        with open_pack(input_path) as pack:
            log.info("pack root: %s", pack.root)
            books = {level: extract(pack, level) for level in CUT_LEVELS}
            book = books[cut]
            if not book.chapters:
                log.error("no chapter with audio or text found in %s", input_path)
                return 2
            log.info("%s: %d part(s) at the %s level", book.title, len(book.chapters), cut)
            for row in chapters.describe_levels(books):
                log.info(
                    " %s %-9s %4d part(s), %s each%s",
                    "*" if row.level == cut else " ",
                    row.level,
                    row.parts,
                    chapters.format_seconds(row.each),
                    "" if row.labelled else "  (no synced text to label the cards)",
                )
            if drop:
                book = chapters.drop_chapters(book, chapters.parse_selection(drop))
            rows = chapters.describe(book)
            for row in rows:
                log.info(
                    "  %2d  %s  %s  %d clip(s)%s",
                    row.number,
                    chapters.format_seconds(row.seconds),
                    row.title,
                    row.clips,
                    f"  {row.text[:36]}" if row.text else "",
                )
            if preview_dir is None:
                log.info("to listen to one: --preview FOLDER")
            else:
                audio.require_ffmpeg()
                for row, chapter in zip(rows, book.chapters):
                    log.info("  %s", chapters.render_preview(row, chapter, preview_dir))
    except ChapterError as exc:
        log.error("%s", exc)
        return 2
    except (DaisyPackError, audio.AudioError) as exc:
        log.error("%s", exc)
        return 1
    return 0


def speech(announce: Announce | None) -> Announce | None:
    """The engine this run's cards get, falling back when it is not installed.

    The neural engines are packages the user's own Python installs, not parts of
    the app. A machine that does not have one keeps its own voice rather than
    losing the story over a card.
    """
    if announce is None or announce.engine not in tts.PACKAGES:
        return announce
    if tts.available(announce.engine, announce.python):
        return announce
    log.warning(
        "%s is not installed in %s: the machine's own voice will read the cards "
        "(pip install %s there, or pass --tts auto)",
        tts.LABELS[announce.engine],
        announce.python or "your Python",
        tts.PACKAGES[announce.engine],
    )
    return replace(announce, engine=tts.AUTO)


def _unfinished(target: Path | None) -> None:
    """What a failed run left in the output folder, before the next run refuses it."""
    if target is not None and target.is_dir():
        log.error(
            "a file of the same name already exists: %s holds an unfinished story. Overwrite it to write the story again",
            target,
        )


def convert(
    input_path: Path,
    output_dir: Path,
    force: bool = False,
    announce: Announce | None = None,
    cover: Path | None = None,
    drop: str | None = None,
    cut: str = "chapter",
    fmt: str = "telmi",
    crop: str = "center",
    find_cover: bool = False,
    zoom: float = 1.0,
    focus: tuple[float, float] | None = None,
    merge: Path | None = None,
    negative: bool = False,
    captions: bool = False,
) -> int:
    """Convert one pack. Logs through this module's logger so a GUI can follow along."""
    announce = speech(announce)
    target: Path | None = None
    try:
        audio.require_ffmpeg()
        if cover is not None and not cover.is_file():
            log.error("no such cover image: %s", cover)
            return 2
        if captions and fmt not in CAPTION_FORMATS:
            log.error(
                "the sentence pictures are drawn for a Telmi or a Flam screen: a Lunii's is too "
                "small to read a sentence on and a Merlin card has none"
            )
            return 2
        if captions and cut != "sentence":
            # One picture per sentence is only worth drawing when each part is one sentence.
            log.info("the sentence pictures need one sentence per part: cutting at sentences instead")
            cut = "sentence"
        # The looked-up cover has to outlive the lookup: the cards are drawn below.
        with open_pack(input_path) as pack, tempfile.TemporaryDirectory(prefix="daisy-convertor-cover-") as scratch:
            log.info("pack root: %s", pack.root)
            book = extract(pack, cut)
            if not book.chapters:
                log.error("no chapter with audio or text found in %s", input_path)
                return 2
            log.info(
                "found %d part(s) at the %s level: %s",
                len(book.chapters),
                cut,
                ", ".join(c.title for c in book.chapters),
            )
            code, source = card_language(announce, book)
            if announce is not None:
                announce = replace(announce, language=code)
            log.info("the cards will be written and spoken in %s: %s", language(code).name, source)
            if cover is None and find_cover:
                cover = covers.find(book, Path(scratch) / "cover.jpg")
            if drop:
                book = chapters.drop_chapters(book, chapters.parse_selection(drop))
                log.info("dropped %s: converting %d chapter(s)", drop, len(book.chapters))
            for chapter in book.chapters:
                log.debug("  %s -> %s", chapter.title, [str(s.src.name) for s in chapter.segments])
            # A player shows the folder's name, so the playlist pack is named after
            # the book; the story formats keep Telmi's `<category>_<age>_<title>_<uuid>`.
            name = (
                playlist.safe_name(book.title)
                if fmt == "mp3"
                else story_dir_name(book.title, story_uuid(book), fallback=input_path.stem)
            )
            target = output_dir if output_dir.name == name else output_dir / name
            if target.exists() and any(target.iterdir()) and not force:
                log.error(
                    "a file of the same name already exists: %s holds a story, so nothing was written. "
                    "Tick the overwrite box, or pass --force, to replace it.",
                    target,
                )
                return 2
            target.mkdir(parents=True, exist_ok=True)
            view = images.crop_view(crop, zoom, focus)
            report = None
            if fmt == "mp3":
                # No cards, no story graph: the pack is the chapters themselves.
                written = playlist.export(book, announce, target, input_path, pack.root)
                archive = shutil.make_archive(
                    str(target.parent / f"{target.name}-mp3"), "zip", target.parent, base_dir=target.name
                )
                log.info("wrote %d file(s) to %s", len(written), target)
            else:
                # The Merlin has no story graph to write: its card is made from the
                # Telmi story, so that one is built at Telmi's sizes first.
                player = "telmi" if fmt == "merlin" else fmt
                if fmt == "lunii":
                    # The greys cannot be recovered from the colour cards: their ground
                    # is dark and the Lunii wants the other way round. Draw a second
                    # set in a scratch folder and build the archive from those.
                    with tempfile.TemporaryDirectory(prefix="daisy-convertor-lunii-") as scratch:
                        grey = Path(scratch)
                        report = build_story(
                            book, target, announce, cover, cut, crop, grey, player=fmt, view=view, negative=negative
                        )
                        archive = write_studio_zip(target, image_dir=grey)
                else:
                    # The `.pk` is the commercial layout: its pictures keep their own
                    # sixteen colours, so they are drawn from the colour cards as they are.
                    report = build_story(book, target, announce, cover, cut, crop, player=player, view=view, captions=captions)
                    if fmt == "lunii-pk":
                        archive = write_pack(target)
                    elif fmt == "merlin":
                        log.warning(
                            "the Merlin export is beta: its card format is undocumented, "
                            "so nothing here has been read back off a real device"
                        )
                        card = target.parent / f"{target.name}_merlin"
                        names = merlin.export(target, card, [part_title(c.title, announce.language) for c in book.chapters], merge)
                        archive = shutil.make_archive(str(card), "zip", card)
                        log.info("wrote %d card file(s) and a %s to %s", len(names), merlin.PLAYLIST, card)
                    else:
                        archive = write_story_zip(target, target.parent, fmt)
    except ChapterError as exc:
        log.error("%s", exc)
        return 2
    except (DaisyPackError, StoryError, images.ImageError, audio.AudioError, MerlinError) as exc:
        log.error("%s", exc)
        _unfinished(target)
        return 1
    except LuniiError as exc:
        log.error("the Telmi story is written, but the Lunii one is not: %s", exc)
        return 1
    except OSError as exc:
        # A file the machine would not give up — read-only, open in another program, a
        # full disk — is an overwrite that failed, and the log has to say that plainly.
        log.error("%s: %s", type(exc).__name__, exc)
        if target is not None and str(exc.filename or "").startswith(str(target)):
            log.error("nothing was written: %s could not be overwritten", target)
        _unfinished(target)
        return 1
    except Exception as exc:
        # A converter bug has to reach the log frame like any other failure: a
        # worker thread that dies quietly leaves the progress bar turning forever.
        log.error("%s: %s", type(exc).__name__, exc)
        _unfinished(target)
        return 1

    log.info("zipped for %s import: %s", PLAYER[fmt], archive)
    if report is None:
        return 0
    log.info("wrote %d scene(s) to %s", len(report.stages), report.out_dir)
    if fmt in LUNII:
        log.info("night mode: offered, as a converted audiobook has nothing to choose")
    log.info(
        "audio: %s",
        {
            "joined": "joined as the pack's own MP3s, which the player resamples",
            "mixed": f"some scenes joined, the rest written as {audio.SPEC}",
        }.get(report.audio_mode, f"written as {audio.SPEC}"),
    )
    log.info("title.mp3 source: %s", report.title_source)
    log.info("chapter cards: %d spoken, autoplay %s", report.cards, "on" if announce and announce.autoplay else "off")
    if captions:
        if report.captions == 0:
            log.info("no sentence picture: the built-in font cannot letter this book's words")
        else:
            log.info("sentence pictures: %d of %d parts, shown while its audio plays", report.captions, report.parts)
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    # Nothing but a verbosity flag: the window opens, and the flag decides how much
    # of the converter's log it writes. The window is the only place the app can
    # write on Windows, where its build carries no terminal of its own.
    if not argv or set(argv) <= {"-v", "--verbose"}:
        from .gui import run_gui

        return run_gui(verbose="-v" in argv or "--verbose" in argv)
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    if args.list or args.preview:
        return inspect(args.input, args.preview, args.drop, cut_level(args))
    return convert(
        args.input,
        args.output,
        args.force,
        announce_options(args),
        args.cover,
        args.drop,
        cut_level(args),
        output_format(args),
        cover_crop(args),
        args.find_cover,
        args.cover_zoom,
        merge=args.merlin_merge,
        negative=card_negative(args),
        captions=captions(args),
    )


if __name__ == "__main__":
    raise SystemExit(main())
