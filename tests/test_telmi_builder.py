"""JSON builder tests; no audio, no ffmpeg, no network."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from daisy2telmi import tts
from daisy2telmi.images import card_png, placeholder_png
from daisy2telmi.extractor import Book, Chapter
from daisy2telmi.telmi_builder import (
    Announce,
    StoryError,
    card_language,
    build_card_audio,
    build_metadata,
    build_nodes,
    build_notes,
    build_story,
    narrate,
    part_title,
    pause_points,
    story_dir_name,
    story_uuid,
)
from nodes_validator import validate_nodes

SAMPLE = ["s0", "s1", "s2"]


def make_book(**overrides) -> Book:
    defaults = dict(
        title="ごんぎつね",
        chapters=(),
        front_matter=(),
        author="新美南吉",
        language="ja",
        publisher="ATDO",
        narrator="持田怜香",
        identifier="81353847207-020",
    )
    defaults.update(overrides)
    return Book(**defaults)


def test_the_cards_take_the_language_the_pack_declares() -> None:
    assert card_language(Announce(language="en"), make_book(language="fr")) == ("fr", "the pack's metadata")


def test_a_language_the_user_pinned_stands_over_the_book() -> None:
    announce = Announce(language="de", auto_language=False)
    assert card_language(announce, make_book(language="fr")) == ("de", "the language you chose")


def test_a_pack_that_says_nothing_is_read_off_the_words_it_carries() -> None:
    book = make_book(
        language=None,
        title="Le loup et l'agneau",
        chapters=(
            Chapter("Un", (), body="Le loup sortit de la foret. Il cherchait de quoi manger un agneau."),
            Chapter("Deux", (), body="Un agneau buvait a la riviere quand le loup vint vers lui."),
        ),
    )
    assert card_language(Announce(language="en"), book) == ("fr", "the pack's own text")


def test_a_pack_that_declares_a_language_with_no_words_here_says_so() -> None:
    assert card_language(Announce(language="en"), make_book()) == (
        "en",
        "your machine: the pack says ja, which has no words here",
    )


def test_nodes_have_no_structural_problems() -> None:
    assert validate_nodes(build_nodes(SAMPLE)) == []


def test_single_chapter_story_is_valid() -> None:
    nodes = build_nodes(["s0"])
    assert validate_nodes(nodes) == []
    assert nodes["stages"]["s0"]["ok"] is None


def test_start_action_enters_the_first_chapter_card() -> None:
    nodes = build_nodes(SAMPLE)
    assert nodes["startAction"] == {"action": "m", "index": 0}


def test_the_menu_lists_every_chapter_card_in_order() -> None:
    nodes = build_nodes(SAMPLE)
    assert nodes["actions"]["m"] == [{"stage": "m0"}, {"stage": "m1"}, {"stage": "m2"}]


def test_a_card_shows_its_image_and_waits_for_ok() -> None:
    """A card has no audio, so autoplay must stay off or the player skips it."""
    card = build_nodes(SAMPLE)["stages"]["m1"]
    assert card == {
        "image": "m1.png",
        "audio": None,
        "ok": {"action": "s1", "index": 0},
        "home": None,
        "control": {"ok": True, "home": True, "autoplay": False},
    }


def test_a_chapter_returns_to_the_next_card() -> None:
    """A chapter runs on by itself and waits for no button, which is what lets
    either player put it on its bedtime playlist: Telmi OS reads these same two
    controls (`stories_reader.h:744`), and a Lunii is told in one word that
    `lunii.build_story_json` writes."""
    stage = build_nodes(SAMPLE)["stages"]["s1"]
    assert stage["audio"] == "s1.mp3"
    assert stage["image"] is None
    assert stage["ok"] == {"action": "m", "index": 2}
    assert stage["home"] == {"action": "m", "index": 1}
    assert stage["control"] == {"ok": False, "home": True, "autoplay": True}


def test_last_chapter_ends_the_story() -> None:
    """Published packs use a null `ok` to end, not a wrap-around to the menu."""
    assert build_nodes(SAMPLE)["stages"]["s2"]["ok"] is None


def test_a_chapter_can_carry_its_own_picture() -> None:
    """A captioned part shows its sentence while its audio plays; a part the font
    cannot write keeps showing the card it was read from."""
    nodes = build_nodes(SAMPLE, chapter_images=["s0.png", None, "s2.png"])
    assert nodes["stages"]["s0"]["image"] == "s0.png"
    assert nodes["stages"]["s1"]["image"] is None
    assert validate_nodes(nodes) == []


def test_the_sentence_pictures_ask_for_the_cut_and_the_screen_they_need() -> None:
    from daisy2telmi.extractor import Chapter

    book = make_book(chapters=[Chapter("Un", (), text="Un", body="Un")])
    with pytest.raises(StoryError, match="one sentence per part"):
        build_story(book, Path("nowhere"), cut="chapter", captions=True)
    with pytest.raises(StoryError, match="not lunii"):
        build_story(book, Path("nowhere"), cut="sentence", player="lunii", captions=True)


def test_empty_chapter_list_is_rejected() -> None:
    with pytest.raises(StoryError):
        build_nodes([])


def test_notes_carry_the_chapter_titles() -> None:
    from daisy2telmi.extractor import Chapter

    book = make_book(chapters=[Chapter("Premier chapitre", ()), Chapter("Second", ())])
    notes = build_notes(book)
    assert notes["m0"] == {"title": "m0", "notes": "Premier chapitre"}
    assert notes["s0"] == {"title": "Premier chapitre", "notes": ""}


def test_metadata_contains_the_required_keys() -> None:
    metadata = build_metadata(make_book())
    for key in ("title", "uuid", "image", "version", "age", "category"):
        assert key in metadata
    assert metadata["title"] == "ごんぎつね"
    assert metadata["image"] == "cover.png"
    assert metadata["version"] == 0
    assert metadata["age"] == 0


def test_uuid_is_stable_for_the_same_book() -> None:
    assert story_uuid(make_book()) == story_uuid(make_book())


def test_uuid_differs_between_books() -> None:
    assert story_uuid(make_book()) != story_uuid(make_book(identifier="other"))


def test_description_lists_the_chapters() -> None:
    from daisy2telmi.extractor import Chapter

    book = make_book(chapters=[Chapter("Gon", ()), Chapter("The hunter", ())])
    description = build_metadata(book)["description"]
    assert "- Gon (1/2)" in description
    assert "- The hunter (2/2)" in description
    assert "Author: 新美南吉" in description


def test_dir_name_matches_a_real_published_story() -> None:
    """Pinned to the folder name Telmi-Sync produced for a published pack."""
    assert story_dir_name("La reine des pirates", "fffffd-19d14f92f57", 0, "") == (
        "_00_La reine des pirates_fffffd-19d14f92f57"
    )


def test_dir_name_strips_accents_and_odd_characters() -> None:
    name = story_dir_name("L'été de l'île", "8f2c-44", 7)
    assert name == "_07_L ete de l ile_8f2c-44"


def test_dir_name_pads_a_single_digit_age_and_caps_the_title() -> None:
    name = story_dir_name("a" * 50, "u", 12)
    assert name.startswith("_12_" + "a" * 32 + "_")


def test_dir_name_keeps_a_readable_word_when_the_title_normalizes_to_nothing() -> None:
    """`ごんぎつね` leaves nothing behind, and a blank before the uuid reads as a
    broken pack in the player's list."""
    assert story_dir_name("ごんぎつね", "8f2c-44", 0, "", "gon-ruby-mp3") == "_00_gon-ruby-mp3_8f2c-44"
    assert story_dir_name("ごんぎつね", "8f2c-44") == "_00_story_8f2c-44"


def test_placeholder_png_is_a_valid_640x480_rgb_image() -> None:
    data = placeholder_png()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert data[12:16] == b"IHDR"
    width, height, depth, color_type = struct.unpack(">IIBB", data[16:26])
    assert (width, height, depth, color_type) == (640, 480, 8, 2)
    assert data.endswith(b"IEND\xaeB`\x82")


def test_card_png_is_a_valid_image_that_differs_per_part() -> None:
    for number in (1, 12):
        data = card_png(f"{number}/12")
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (640, 480)
    assert card_png("1/12") != card_png("2/12")


def test_a_card_can_carry_spoken_audio_and_autoplay() -> None:
    nodes = build_nodes(SAMPLE, ["c0.mp3", "c1.mp3", "c2.mp3"], card_autoplay=True)
    assert validate_nodes(nodes) == []
    assert nodes["stages"]["m1"]["audio"] == "c1.mp3"
    assert nodes["stages"]["m1"]["control"]["autoplay"] is True


def test_cards_stay_silent_by_default() -> None:
    nodes = build_nodes(SAMPLE)
    assert all(stage["audio"] is None for name, stage in nodes["stages"].items() if name.startswith("m"))
    assert all(stage["control"]["autoplay"] is False for name, stage in nodes["stages"].items() if name.startswith("m"))


def test_a_story_cut_to_chapters_stops_at_every_one_of_them() -> None:
    parts = [Chapter(f"Ch {k}", (), group=k) for k in range(3)]
    assert pause_points(parts, "chapter", "never") == [False, False, False]
    assert pause_points(parts, "chapter", "sentence") == [True, True, True]
    assert pause_points(parts, "chapter", "chapter") == [True, True, True]


def test_a_finer_cut_keeps_its_chapters_as_the_places_it_stops() -> None:
    """A book cut to sentences still knows which chapter each sentence came from."""
    parts = [Chapter("", (), group=group) for group in (0, 0, 1, 2, 2)]
    assert pause_points(parts, "sentence", "chapter") == [True, False, True, True, False]
    assert pause_points(parts, "sentence", "paragraph") == [True, False, True, True, False]
    assert pause_points(parts, "sentence", "sentence") == [True] * 5
    assert pause_points(parts, "sentence", "never") == [False] * 5


def test_a_cut_that_matches_the_pause_stops_at_every_part() -> None:
    parts = [Chapter("", (), group=group) for group in (0, 0, 1)]
    assert pause_points(parts, "paragraph", "paragraph") == [True, True, True]


def test_only_the_stopping_cards_get_a_card(tmp_path, monkeypatch) -> None:
    from daisy2telmi import audio
    from daisy2telmi.extractor import AudioSegment
    from daisy2telmi.telmi_builder import build_snippets

    monkeypatch.setattr(audio, "render", lambda segments, dest, limit=None: "cut")
    parts = [Chapter("", (AudioSegment(tmp_path / "p.mp3"),)) for _ in range(4)]
    assert build_snippets(parts, tmp_path, pauses=[True, False, False, True]) == ["c0.mp3", None, None, "c3.mp3"]


def test_a_card_that_runs_on_does_not_wait_for_ok() -> None:
    nodes = build_nodes(SAMPLE, card_autoplay=[False, True, True])
    assert validate_nodes(nodes) == []
    assert [nodes["stages"][f"m{k}"]["control"]["autoplay"] for k in range(3)] == [False, True, True]


def test_a_card_snippet_is_the_opening_seconds_of_its_part(tmp_path, monkeypatch) -> None:
    from daisy2telmi import audio
    from daisy2telmi.extractor import AudioSegment, Chapter
    from daisy2telmi.telmi_builder import CARD_SNIPPET_SECONDS, build_snippets

    asked: list = []

    def render(segments, dest, limit=None):
        asked.append((dest.name, limit))
        return "cut"

    monkeypatch.setattr(audio, "render", render)
    parts = [Chapter("", (AudioSegment(tmp_path / f"p{k}.mp3"),)) for k in range(3)]
    assert build_snippets(parts, tmp_path) == ["c0.mp3", "c1.mp3", "c2.mp3"]
    assert asked == [(f"c{k}.mp3", CARD_SNIPPET_SECONDS) for k in range(3)]


def test_a_part_shorter_than_the_clip_gives_its_card_the_part_itself(tmp_path, monkeypatch) -> None:
    """Cut to sentences, many parts are under three seconds: a second copy would be pure size."""
    from daisy2telmi import audio
    from daisy2telmi.extractor import AudioSegment, Chapter
    from daisy2telmi.telmi_builder import CARD_SNIPPET_SECONDS, build_snippets

    asked: list = []
    monkeypatch.setattr(audio, "render", lambda segments, dest, limit=None: asked.append(dest.name) or "cut")
    parts = [
        Chapter("", (AudioSegment(tmp_path / "p.mp3", 0.0, CARD_SNIPPET_SECONDS - 1.0),)),
        Chapter("", (AudioSegment(tmp_path / "p.mp3", 0.0, CARD_SNIPPET_SECONDS + 30.0),)),
    ]
    assert build_snippets(parts, tmp_path) == ["s0.mp3", "c1.mp3"]
    assert asked == ["c1.mp3"]


def test_the_card_audio_length_is_the_one_asked_for(tmp_path, monkeypatch) -> None:
    from daisy2telmi import audio
    from daisy2telmi.extractor import AudioSegment, Chapter
    from daisy2telmi.telmi_builder import build_snippets

    asked: list = []
    monkeypatch.setattr(audio, "render", lambda segments, dest, limit=None: asked.append(limit) or "cut")
    parts = [
        Chapter("", (AudioSegment(tmp_path / "p.mp3", 0.0, 4.0),)),
        Chapter("", (AudioSegment(tmp_path / "p.mp3", 0.0, 40.0),)),
    ]
    assert build_snippets(parts, tmp_path, seconds=6.0) == ["s0.mp3", "c1.mp3"]
    assert asked == [6.0]


def test_one_failing_snippet_leaves_every_card_silent(tmp_path, monkeypatch) -> None:
    from daisy2telmi import audio
    from daisy2telmi.extractor import AudioSegment, Chapter
    from daisy2telmi.telmi_builder import build_snippets

    def refuse(segments, dest, limit=None):
        raise audio.AudioError("no audio in that range")

    monkeypatch.setattr(audio, "render", refuse)
    parts = [Chapter("", (AudioSegment(tmp_path / "p.mp3"),)) for _ in range(2)]
    assert build_snippets(parts, tmp_path) == [None, None]


def test_the_description_lists_only_the_first_parts() -> None:
    """A book cut to the sentence level has hundreds of them; Telmi-Sync shows this list."""
    from daisy2telmi.extractor import Chapter
    from daisy2telmi.telmi_builder import LISTED_PARTS

    book = make_book(chapters=[Chapter(f"Part {k}", ()) for k in range(LISTED_PARTS + 5)])
    description = build_metadata(book)["description"]
    assert f"- Part {LISTED_PARTS - 1} ({LISTED_PARTS}/{LISTED_PARTS + 5})" in description
    assert "... 5 more" in description
    assert f"Part {LISTED_PARTS}" not in description


def test_an_announcement_phrase_carries_the_chapter_number() -> None:
    assert Announce(language="fr").phrase(3) == "Chapitre 3 Écouter le chapitre suivant ?"
    assert Announce(mode="chapter", language="nl").phrase(2) == "Hoofdstuk 2"
    assert Announce(mode="off").phrase(1) is None


def test_a_card_can_read_the_part_it_introduces() -> None:
    """The DAISY outline names its parts, and that name is what a child recognises."""
    assert Announce(mode="title").phrase(2, 7, "La fee des bois") == "La fee des bois"
    assert Announce(mode="number_title", language="fr").phrase(2, 7, "La fee") == "2. La fee"
    assert Announce(mode="title_number", language="fr").phrase(2, 7, "La fee") == "La fee. 2"
    assert Announce(mode="number").phrase(2, 7, "La fee") == "2"
    assert Announce(mode="number_of", language="fr").phrase(2, 7) == "2 sur 7"


def test_a_pack_whose_parts_have_no_names_falls_back_to_the_number_and_the_total() -> None:
    """Sentence-level cuts and packs without synced text have nothing to read out."""
    assert Announce(mode="title").phrase(4, 9, "") == "Chapter 4 of 9"
    assert Announce(mode="number_title", language="fr").phrase(4, 9, "") == "Chapitre 4 sur 9"
    assert Announce(mode="chapter").phrase(4, 9, "La fee") == "Chapter 4"
    assert Announce(mode="number_of").phrase(4) == "Chapter 4"


def test_a_label_that_only_repeats_the_chapter_word_is_not_a_title() -> None:
    """An outline that names its parts "Chapter 1" gives the card nothing to add."""
    assert part_title("Chapitre 1", "fr") == ""
    assert part_title("CHAPITRE IV", "fr") == ""
    assert part_title("Chapitre 1er", "fr") == ""
    assert part_title("Part 2", "en") == ""
    assert part_title("第 1 章", "zh") == ""
    assert part_title("الفصل 2", "ar") == ""
    assert part_title("Kapitel 3", "de") == ""


def test_a_label_with_a_word_beside_the_number_is_the_part_s_name() -> None:
    assert part_title("Chapitre 1 : Le lapin", "fr") == "Chapitre 1 : Le lapin"
    assert part_title("La fee des bois", "fr") == "La fee des bois"
    assert part_title("Kapitel 3 - Der Wald", "de") == "Kapitel 3 - Der Wald"


def test_the_titles_reach_the_speech_engine(tmp_path, monkeypatch) -> None:
    heard: list[list[str]] = []

    def fake_synth(phrases, lang_code, dests, **options):
        heard.append(list(phrases))
        for dest in dests:
            Path(dest).write_bytes(b"mp3")

    monkeypatch.setattr(tts, "synthesize_all", fake_synth)
    build_card_audio(2, Announce(mode="number_title", language="fr"), tmp_path, None,
                     ["Le lapin", "La foret"])
    assert heard == [["1. Le lapin", "2. La foret"]]


def test_one_failing_engine_leaves_every_card_silent(tmp_path) -> None:
    absent = next((e.name for e in tts.engines() if not e.found), None)
    if absent is None:
        pytest.skip("this machine has every speech engine")
    cards, spoken = build_card_audio(3, Announce(engine=absent), tmp_path)
    assert cards == [None, None, None]
    assert spoken == 0


def test_each_spoken_card_is_named_after_its_chapter(tmp_path, monkeypatch) -> None:
    def fake_synth(phrases, lang_code, dests, **options):
        assert [Path(dest).name for dest in dests] == ["c0.mp3", "c1.mp3", "c2.mp3"]
        for dest in dests:
            Path(dest).write_bytes(b"mp3")

    monkeypatch.setattr(tts, "synthesize_all", fake_synth)
    cards, spoken = build_card_audio(3, Announce(language="fr"), tmp_path)
    assert cards == ["c0.mp3", "c1.mp3", "c2.mp3"]
    assert spoken == 3


def test_the_book_text_can_be_read_by_another_voice_than_the_cards() -> None:
    """A card says a few words; a chapter is a page. The voice wanted for one is
    not always the voice wanted for the other."""
    split = Announce(voice="card", clip="card.wav", speed="slow", read_voice="reader", read_speed="fast")
    assert split.reading() == ("reader", "card.wav", "fast")


def test_the_book_text_falls_back_to_the_card_settings() -> None:
    assert Announce(voice="card", clip="card.wav", speed="slow").reading() == ("card", "card.wav", "slow")


def test_the_reading_voice_is_the_one_the_book_is_spoken_with(tmp_path, monkeypatch) -> None:
    asked: dict = {}

    def fake_narrate(text, lang_code, dest, **options):
        asked.update(options)
        Path(dest).write_bytes(b"mp3")

    monkeypatch.setattr(tts, "narrate", fake_narrate)
    narrate("Une phrase.", Announce(voice="card", read_voice="reader"), tmp_path / "s0.mp3")
    assert asked["voice"] == "reader", "the card's voice is not the one a chapter is read with"


def test_the_book_is_spoken_with_the_expressiveness_the_cards_chose(tmp_path, monkeypatch) -> None:
    asked: dict = {}

    def fake_narrate(text, lang_code, dest, **options):
        asked.update(options)
        Path(dest).write_bytes(b"mp3")

    monkeypatch.setattr(tts, "narrate", fake_narrate)
    narrate("Une phrase.", Announce(expression="expressive"), tmp_path / "s0.mp3")
    assert asked["expression"] == "expressive"
