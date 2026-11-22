"""Read a story back the way the machine reads it, and walk it with its buttons.

The story folder is already the machine's own: `nodes.json` says where each button
leads, `images/` and `audios/` hold what a stage shows and plays. Reading it back
is how the app answers "what will the teller do" without a teller.

A Lunii run writes the story twice over: the folder above, in colour, and a STUdio
archive whose `story.json` holds the sixteen greys that machine's screen shows. The
emulator reads whichever it is handed, so the picture it draws is the one the teller
will show. The commercial card's own records are not read here.
"""

from __future__ import annotations

import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

# The two graphs a run leaves: the teller's `nodes.json`, and STUdio's `story.json`.
GRAPHS = ("nodes.json", "story.json")


class EmulatorError(Exception):
    """A folder that is not a story this app wrote."""


@dataclass(frozen=True)
class Controls:
    """The switches a stage leaves on: the machine ignores a button that is off."""

    ok: bool = True
    home: bool = True
    wheel: bool = False
    autoplay: bool = False


@dataclass(frozen=True)
class Stage:
    id: str
    image: Path | None
    audio: Path | None
    ok: str | None
    home: str | None
    wheel: tuple[str, ...]
    controls: Controls


@dataclass(frozen=True)
class Story:
    title: str
    machine: str
    start: str
    stages: dict[str, Stage]
    folder: Path


def find_story(where: Path) -> Path | None:
    """The newest story under `where`, which is where a run just left one.

    A run leaves a folder and an archive, and writes the archive last, so the newest
    of the two is the one the last run finished with.
    """
    if _is_story(where):
        return where
    try:
        children = sorted(where.iterdir(), key=lambda child: child.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return next((child for child in children if _is_story(child)), None)


def _is_story(path: Path) -> bool:
    if path.is_dir():
        return any((path / name).is_file() for name in GRAPHS)
    return path.suffix == ".zip" and _zip_graph(path) is not None


def _zip_graph(archive: Path) -> str | None:
    """The entry holding the story graph, which sits at the top of an archive or one
    folder in. Reading an archive's list of entries costs nothing: it is the tail of
    the file."""
    try:
        with zipfile.ZipFile(archive) as packed:
            names = packed.namelist()
    except (OSError, zipfile.BadZipFile):
        return None
    return next(
        (name for name in names if name.rsplit("/", 1)[-1] in GRAPHS and name.count("/") <= 1),
        None,
    )


def read_story(folder: Path) -> Story:
    root = find_story(folder)
    if root is None:
        raise EmulatorError(f"{folder} holds no story graph: it is not a story this app wrote")
    if root.is_file():
        root = _unpack(root)
    graph = next(root / name for name in GRAPHS if (root / name).is_file())
    return _read_studio(root, graph) if graph.name == "story.json" else _read_telmi(root, graph)


def _unpack(archive: Path) -> Path:
    """An archive's story, let out into a folder of its own. The app plays a clip by
    handing a path to a player, so the media has to be on disk to be heard."""
    where = Path(tempfile.mkdtemp(prefix="daisy2telmi-emulator-"))
    try:
        with zipfile.ZipFile(archive) as packed:
            for member in packed.infolist():
                name = Path(member.filename)
                # The archive is whatever folder the user pointed the emulator at, so
                # an entry that climbs out of the folder it is let into is refused.
                if name.is_absolute() or ".." in name.parts:
                    raise EmulatorError(f"{archive.name} holds {member.filename}, which is outside the story")
                packed.extract(member, where)
    except (OSError, zipfile.BadZipFile) as exc:
        raise EmulatorError(f"{archive.name}: {exc}")
    root = find_story(where)
    if root is None:
        raise EmulatorError(f"{archive.name} holds no story graph")
    return root


def _json(graph: Path) -> dict:
    try:
        loaded = json.loads(graph.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EmulatorError(f"{graph}: {exc}")
    return loaded if isinstance(loaded, dict) else {}


def _read_telmi(root: Path, graph: Path) -> Story:
    nodes = _json(graph)

    runs = {
        action: [option.get("stage") for option in options]
        for action, options in (nodes.get("actions") or {}).items()
        if isinstance(options, list)
    }

    def target(move: dict | None) -> str | None:
        # The graph is a file on disk, so a move that points past the end of its
        # run is a move nowhere, not a crash in the middle of reading the story.
        if not move:
            return None
        run = runs.get(move.get("action") or "")
        at = move.get("index") or 0
        return run[at] if run and 0 <= at < len(run) else None

    stages: dict[str, Stage] = {}
    for stage_id, stage in (nodes.get("stages") or {}).items():
        control = stage.get("control") or {}
        stages[stage_id] = Stage(
            id=stage_id,
            image=root / "images" / stage["image"] if stage.get("image") else None,
            audio=root / "audios" / stage["audio"] if stage.get("audio") else None,
            ok=target(stage.get("ok")),
            home=target(stage.get("home")),
            # The wheel scrolls the run of stages one action holds, and the stage
            # is somewhere in it.
            wheel=next((tuple(run) for run in runs.values() if stage_id in run), (stage_id,)),
            controls=Controls(
                ok=control.get("ok", True),
                home=control.get("home", True),
                wheel=control.get("wheel", False),
                autoplay=bool(control.get("autoplay")),
            ),
        )

    start = target(nodes.get("startAction"))
    if start not in stages:
        raise EmulatorError("nodes.json says where to start, and this one says nowhere")
    return Story(
        title=_title(root) or root.name,
        machine="telmi",
        start=start,
        stages=stages,
        folder=root,
    )


def _read_studio(root: Path, graph: Path) -> Story:
    """STUdio's graph: stages named by uuid, media by hash under `assets/`, and the
    cover card marked out as the place to start. Its buttons are switched on one by
    one, where the teller's graph leaves them on unless it says otherwise."""
    story = _json(graph)
    runs = {
        str(action.get("id")): [
            option for option in action.get("options") or [] if isinstance(option, str)
        ]
        for action in story.get("actionNodes") or []
    }

    def target(move: dict | None) -> str | None:
        if not move:
            return None
        run = runs.get(str(move.get("actionNode") or ""))
        at = move.get("optionIndex") or 0
        return run[at] if run and 0 <= at < len(run) else None

    cards = [node for node in story.get("stageNodes") or [] if isinstance(node, dict) and node.get("uuid")]
    stages: dict[str, Stage] = {}
    for card in cards:
        stage_id = str(card["uuid"])
        control = card.get("controlSettings") or {}
        stages[stage_id] = Stage(
            id=stage_id,
            image=root / "assets" / card["image"] if card.get("image") else None,
            audio=root / "assets" / card["audio"] if card.get("audio") else None,
            ok=target(card.get("okTransition")),
            home=target(card.get("homeTransition")),
            wheel=next((tuple(run) for run in runs.values() if stage_id in run), (stage_id,)),
            controls=Controls(
                ok=bool(control.get("ok")),
                home=bool(control.get("home")),
                wheel=bool(control.get("wheel")),
                autoplay=bool(control.get("autoplay")),
            ),
        )

    cover = next((card for card in cards if card.get("squareOne")), cards[0] if cards else {})
    start = str(cover.get("uuid") or "")
    if start not in stages:
        raise EmulatorError("story.json says which card to start on, and this one says none")
    return Story(
        title=str(story.get("title") or root.name),
        machine="lunii",
        start=start,
        stages=stages,
        folder=root,
    )


def _title(root: Path) -> str:
    try:
        listed = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(listed.get("title") or "")


def walk(story: Story, stage_id: str, button: str) -> str | None:
    """Where a button leads. `next` is not a button: it is the end of an autoplayed
    clip, which forces the OK transition whether or not the OK button is switched on.
    """
    stage = story.stages.get(stage_id)
    if stage is None:
        return None
    if button == "next":
        return stage.ok
    if button == "ok":
        return stage.ok if stage.controls.ok else None
    if button == "home":
        return stage.home if stage.controls.home else None
    if button in ("up", "down"):
        if not stage.controls.wheel or stage_id not in stage.wheel:
            return None
        step = 1 if button == "down" else -1
        at = stage.wheel.index(stage_id) + step
        return stage.wheel[at] if 0 <= at < len(stage.wheel) else None
    return None
