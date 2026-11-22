"""Reading a story back, and walking it the way the machine's buttons do."""

from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path

import pytest

from daisy2telmi import emulator


def write_story(
    root: Path,
    stages: dict[str, dict],
    actions: dict[str, list[dict]],
    start: dict | None = None,
    title: str = "The book",
) -> Path:
    """A story folder as the converter leaves one: the graph, and its media."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "nodes.json").write_text(
        json.dumps(
            {"startAction": start or {"action": "m", "index": 0}, "stages": stages, "actions": actions}
        ),
        encoding="utf-8",
    )
    (root / "metadata.json").write_text(json.dumps({"title": title}), encoding="utf-8")
    for stage_id, stage in stages.items():
        for folder, key, extension in (("images", "image", "png"), ("audios", "audio", "mp3")):
            if stage.get(key):
                (root / folder).mkdir(exist_ok=True)
                (root / folder / stage[key]).write_bytes(extension.encode())
    return root


# The graph the converter writes: `m<k>` names a chapter, `s<k>` plays it, and the
# last chapter's OK is null rather than a wrap round.
CHAPTERS = {
    "m0": {"image": "m0.png", "audio": None, "ok": {"action": "s0", "index": 0}, "home": None,
           "control": {"ok": True, "home": True, "autoplay": False}},
    "s0": {"image": None, "audio": "c0.mp3", "ok": {"action": "m", "index": 1}, "home": {"action": "m", "index": 0},
           "control": {"ok": False, "home": True, "autoplay": True}},
    "m1": {"image": "m1.png", "audio": None, "ok": {"action": "s1", "index": 0}, "home": None,
           "control": {"ok": True, "home": True, "autoplay": False}},
    "s1": {"image": None, "audio": "c1.mp3", "ok": None, "home": {"action": "m", "index": 1},
           "control": {"ok": False, "home": True, "autoplay": True}},
}
MENU = {"m": [{"stage": "m0"}, {"stage": "m1"}], "s0": [{"stage": "s0"}], "s1": [{"stage": "s1"}]}


def test_a_story_folder_is_read_as_the_machine_sees_it(tmp_path: Path) -> None:
    """The folder a run left behind is the machine's own story: the emulator reads
    the graph back, and finds each stage's picture and clip beside it."""
    story = emulator.read_story(write_story(tmp_path / "story", CHAPTERS, MENU))

    assert story.title == "The book"
    assert story.start == "m0"
    assert set(story.stages) == set(CHAPTERS)
    card = story.stages["m0"]
    assert card.image.name == "m0.png" and card.image.is_file()
    assert card.audio is None
    assert (card.ok, card.home) == ("s0", None)
    assert story.stages["s0"].audio.name == "c0.mp3"
    # A chapter card leaves its own audio out: the clip belongs to the stage after it.
    assert story.stages["s0"].controls.autoplay and not card.controls.autoplay


def test_a_button_the_stage_left_shut_leads_nowhere(tmp_path: Path) -> None:
    """The machine ignores a button its stage switched off, and the emulator has to
    ignore it too: a chapter clip that runs on to the next card is autoplay, not OK."""
    story = emulator.read_story(write_story(tmp_path / "story", CHAPTERS, MENU))

    assert emulator.walk(story, "m0", "ok") == "s0"
    assert emulator.walk(story, "s0", "ok") is None
    assert emulator.walk(story, "s0", "home") == "m0"
    assert emulator.walk(story, "m0", "home") is None
    assert emulator.walk(story, "m0", "up") is None
    assert emulator.walk(story, "nope", "ok") is None


def test_the_end_of_a_clip_forces_the_next_stage(tmp_path: Path) -> None:
    """`next` is not a button: it is the clip running out, which moves on whether or
    not the OK button was switched on."""
    story = emulator.read_story(write_story(tmp_path / "story", CHAPTERS, MENU))
    assert emulator.walk(story, "s0", "next") == "m1"


def test_a_move_past_the_end_of_its_run_leads_nowhere(tmp_path: Path) -> None:
    """The graph is a file on disk, so a move that points past the end of the run it
    names is nowhere: reading the story does not stop on it."""
    stages = {
        "m0": {"image": "m0.png", "audio": None, "ok": {"action": "m", "index": 7}, "home": None,
               "control": {"ok": True, "home": True}}
    }
    story = emulator.read_story(write_story(tmp_path / "story", stages, {"m": [{"stage": "m0"}]}))
    assert story.stages["m0"].ok is None


def test_the_wheel_stops_at_the_ends_of_its_run(tmp_path: Path) -> None:
    """A wheel scrolls the run of stages one action holds, and does not wrap round."""
    stages = {
        f"m{k}": {"image": f"m{k}.png", "audio": None, "ok": None, "home": None,
                  "control": {"ok": False, "home": False, "wheel": True}}
        for k in range(3)
    }
    story = emulator.read_story(write_story(tmp_path / "story", stages, {"m": [{"stage": f"m{k}"} for k in range(3)]}))

    assert story.stages["m1"].wheel == ("m0", "m1", "m2")
    assert emulator.walk(story, "m1", "down") == "m2"
    assert emulator.walk(story, "m2", "down") is None
    assert emulator.walk(story, "m0", "up") is None


def test_a_folder_that_holds_no_story_says_so(tmp_path: Path) -> None:
    """The emulator is opened on a folder the user picked: a wrong one has to be
    named, not left as a window with nothing in it."""
    (tmp_path / "empty").mkdir()
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "nodes.json").write_text("{not json", encoding="utf-8")

    for folder in (tmp_path, tmp_path / "empty", broken):
        with pytest.raises(emulator.EmulatorError):
            emulator.read_story(folder)
    with pytest.raises(emulator.EmulatorError):
        emulator.read_story(write_story(tmp_path / "nowhere", CHAPTERS, MENU, start={"action": "gone"}))


def test_the_story_a_run_just_wrote_is_the_one_played(tmp_path: Path) -> None:
    """The output folder keeps every story written to it; the newest is the one the
    emulator is asked for."""
    older = write_story(tmp_path / "older", CHAPTERS, MENU)
    newer = write_story(tmp_path / "newer", CHAPTERS, MENU)
    stamp = time.time()
    os.utime(older, (stamp - 60, stamp - 60))
    os.utime(newer, (stamp, stamp))

    assert emulator.find_story(tmp_path) == newer
    assert emulator.find_story(newer) == newer
    assert emulator.find_story(tmp_path / "nothing here") is None


def write_studio(root: Path, cards: list[dict], actions: list[dict], title: str = "The book") -> Path:
    """A STUdio story as the Lunii pack writes one: the graph by uuid, and its media
    by hash under `assets/`."""
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "story.json").write_text(
        json.dumps(
            {"format": "storyteller", "version": 1, "title": title,
             "stageNodes": cards, "actionNodes": actions}
        ),
        encoding="utf-8",
    )
    for card in cards:
        for key, token in (("image", b"png"), ("audio", b"mp3")):
            if card.get(key):
                (root / "assets" / card[key]).write_bytes(token)
    return root


# STUdio's own graph for a story: the cover card is marked out as the start, and each
# card switches its buttons on one by one rather than leaving them on.
STUDIO_CARDS = [
    {"uuid": "c0ver", "type": "pause", "name": "Cover", "image": "9f.png", "audio": None,
     "okTransition": {"actionNode": "menu", "optionIndex": 1}, "homeTransition": None,
     "controlSettings": {"ok": True, "home": False, "wheel": True, "pause": False, "autoplay": False},
     "squareOne": True},
    {"uuid": "ch1pter", "type": "wheel", "name": "One", "image": "a2.png", "audio": "b3.mp3",
     "okTransition": None, "homeTransition": {"actionNode": "menu", "optionIndex": 0},
     "controlSettings": {"ok": False, "home": True, "wheel": True, "pause": True, "autoplay": True}},
]
STUDIO_ACTIONS = [{"id": "menu", "name": "Menu", "options": ["c0ver", "ch1pter"]}]


def test_a_studio_story_is_read_as_the_lunii_sees_it(tmp_path: Path) -> None:
    """The Lunii pack writes its own graph, and the greys that machine's screen shows
    live only inside it: reading it back is how the emulator draws that screen."""
    story = emulator.read_story(write_studio(tmp_path / "pack", STUDIO_CARDS, STUDIO_ACTIONS))

    assert story.machine == "lunii"
    assert story.title == "The book"
    assert story.start == "c0ver", "the cover card is the one marked out to start on"
    card = story.stages["c0ver"]
    assert card.image.parts[-2:] == ("assets", "9f.png") and card.image.is_file()
    assert (card.ok, card.home) == ("ch1pter", None)
    assert story.stages["ch1pter"].audio.name == "b3.mp3"


def test_a_studio_button_is_on_only_when_its_card_says_so(tmp_path: Path) -> None:
    """STUdio switches a button on per card, where the teller's graph leaves buttons
    on unless the card says otherwise."""
    story = emulator.read_story(write_studio(tmp_path / "pack", STUDIO_CARDS, STUDIO_ACTIONS))

    assert emulator.walk(story, "c0ver", "home") is None
    assert emulator.walk(story, "c0ver", "down") == "ch1pter"
    assert emulator.walk(story, "ch1pter", "next") is None, "the last card's OK is null"
    assert emulator.walk(story, "ch1pter", "home") == "c0ver"


def test_the_archive_written_last_is_the_story_played(tmp_path: Path) -> None:
    """A Lunii run leaves the colour folder and the archive of greys in the same
    folder, and writes the archive last: that is the story the emulator opens."""
    folder = write_story(tmp_path / "the book", CHAPTERS, MENU)
    pack = write_studio(tmp_path / "pack", STUDIO_CARDS, STUDIO_ACTIONS)
    stamp = time.time()
    os.utime(folder, (stamp - 60, stamp - 60))
    os.utime(pack, (stamp - 30, stamp - 30))
    archive = tmp_path / "the book-lunii.zip"
    with zipfile.ZipFile(archive, "w") as packed:
        for part in sorted(pack.rglob("*")):
            packed.write(part, part.relative_to(pack))

    assert emulator.find_story(tmp_path) == archive
    story = emulator.read_story(tmp_path)
    assert story.machine == "lunii"
    assert story.stages[story.start].image.parts[-2:] == ("assets", "9f.png")
    assert story.folder != pack, "the archive is let out, so a clip has a path to play from"


def test_an_archive_that_climbs_out_of_its_folder_is_refused(tmp_path: Path) -> None:
    """The emulator can be pointed at any folder the user picks, so an archive whose
    entries climb out of it is refused: nothing is written outside the story."""
    archive = tmp_path / "climber.zip"
    with zipfile.ZipFile(archive, "w") as packed:
        packed.writestr("story.json", json.dumps({"stageNodes": [], "actionNodes": []}))
        packed.writestr("../escaped.png", b"png")

    with pytest.raises(emulator.EmulatorError):
        emulator.read_story(archive)
    assert not (tmp_path.parent / "escaped.png").exists()
