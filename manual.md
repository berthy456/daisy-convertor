# daisy-convertor manual

How to use the tool: the command line, every flag, the files each format writes, the tests, and how to build the binaries. For the app and the web page as a listener meets them, see [README.md](README.md).

Inputs: **DAISY 2.02, DAISY 3, text-only DAISY, EPUB** — a `.zip`, an `.epub` or an extracted folder.
Outputs: **Telmi** story folder, **Lunii** STUdio zip, **Commercial PK** (`.plain.pk`), **Flam** pictures, **Merlin** card (beta).

`berthy456/daisy2telmi` is this tool under its old name, left at v3.0.1.

## Source install

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

Python 3.11+. ffmpeg must be on `PATH` (`winget install Gyan.FFmpeg`, `brew install ffmpeg`, `apt install ffmpeg`); the built binaries bundle their own copy, so only a source install needs it. Tkinter for the GUI: `pip install -e .[gui]` adds drag-and-drop; on macOS, Homebrew's Python needs `brew install python-tk@3.15`.

## Command line

```bash
daisy-convertor <pack> [output-folder]
```

No arguments opens the GUI. The output folder is a parent: inside it the story gets Telmi's `<category>_<age>_<title>_<uuid>` name, unless the path already has that name.

| Exit | Meaning |
| :--- | :--- |
| 0 | written |
| 1 | pack unreadable, or ffmpeg failed |
| 2 | bad arguments, not a DAISY pack, output folder not empty |

### Flags

| Flag | Values | Default |
| :--- | :--- | :--- |
| `-v`, `--verbose` | | logs every ffmpeg call |
| `-f`, `--force` | | writes into a non-empty output folder |
| `--format` | `telmi` `lunii` `lunii-pk` `flam` `merlin` | `telmi` |
| `--merlin-merge` | a `playlist.bin` copied off a Merlin card | |
| `--version` | | |

Before converting:

| Flag | Values | Notes |
| :--- | :--- | :--- |
| `--list` | | prints one line per part (number, length, title, clip count) and stops; needs no output folder |
| `--cut` | `chapter` `paragraph` `sentence` | where the book is cut; `chapter` follows the pack's own outline |
| `--preview FOLDER` | | writes one playable MP3 per part into FOLDER and stops |
| `--drop CHAPTERS` | `2`, `2,4-6` | leaves those parts out, by the numbers `--list` prints; what is left is renumbered. A number outside the book, or dropping every part, exits 2 |

Cover:

| Flag | Values | Notes |
| :--- | :--- | :--- |
| `--cover IMAGE` | | draws the cards over this picture instead of the placeholder |
| `--find-cover` | | asks Open Library for the print book named in `dc:source` (`dc:identifier` is the DAISY edition's own number, which no cover service knows); falls back to a title and author search with no ISBN |
| `--cover-crop` | `top` `center` `bottom` | `center` |
| `--cover-zoom FACTOR` | 0–4 | 0 shows the whole picture and fills the frame's empty room with the card's own second colour, 1 fills the frame, more than 1 enlarges it past the frame so it can move sideways |

Chapter card speech:

| Flag | Values | Notes |
| :--- | :--- | :--- |
| `--announce` | `off` `number` `number_of` `title` `number_title` `title_number` `chapter` `prompt` | what the card says; `prompt` is the default. A pattern needing a name falls back to `chapter` when the parts carry no synced text |
| `--card-total` / `--no-card-total` | | whether the drawn card counts the parts: `4/12` or `4` |
| `--card-negative` / `--no-card-negative` | | draws the black-and-white Lunii cards light-for-dark. Only those cards have a negative |
| `--language` | `ar de en es fr it nl pt tr zh` | the phrase and the voice; wording in `src/daisy2telmi/i18n.py` |
| `--autoplay` | | a part advances by itself instead of waiting for OK. A card's audio plays as soon as the card is drawn either way |
| `--tts` | `chatterbox` `auto` `espeak` `say` `sapi` `command` `off` | `chatterbox`; `auto` is espeak-ng, then macOS `say`, then Windows speech. Asking for Chatterbox where it is missing logs a warning and uses `auto` |
| `--tts-voice` | | voice name |
| `--tts-clip` | | a few seconds of someone speaking, which Chatterbox copies as a voice |
| `--tts-python` | | the interpreter that has `chatterbox-tts` |
| `--tts-device` | `auto` `cpu` `mps` `cuda` | `auto` picks the fastest and logs it; a forced device the machine lacks stops the run |
| `--tts-command` | | a template with `{text}` `{out}` `{lang}` `{locale}` `{voice}` |

Cards get a spoken number only when the cut is by chapter. At paragraph or sentence level a card plays the opening seconds of its own part instead, which is the only way to tell hundreds of cards apart.

## Formats

| `--format` | Archive written | |
| :--- | :--- | :--- |
| `telmi` | `<story folder>.zip` | for Telmi Sync's **Import → zip** |
| `lunii` | `<story>-lunii.zip` | Lunii STUdio; cards drawn in the screen's sixteen greys |
| `lunii-pk` | `<Title>.<8 hex>.plain.pk` | the layout a Lunii story card keeps itself; Lunii.QT or open-story-teller writes it onto a card |
| `flam` | `<story folder>.zip` | the Telmi archive, at the Flam's own screen sizes (320×212 scene, 104×136 thumbnail) |
| `merlin` | `<story>_merlin.zip` | beta |

The Telmi story folder is always written as well; the archive is made from it.

### Telmi story folder

```
stories/_00_Wesh la fee_292b74ca-d8be-53f9-a5d5-5224a36bbd02/
  metadata.json
  nodes.json
  notes.json
  title.mp3      the pack's spoken title, played on the story's title screen
  title.png      640x480: the cover, or a placeholder
  cover.png      512x512 copy of the same, referenced by metadata.json
  audios/
    s0.mp3 ... sN.mp3      one per part
  images/
    m0.png ... mN.png      640x480: the cover with the card text over it, or the number on a placeholder
```

The zip nests the folder one level, because `findDirectory` in Telmi-Sync's `ConvertZip.js` counts `metadata.json`, `nodes.json`, `title.mp3` and `title.png` in a single zip directory and requires exactly four. Telmi-Sync renames the folder on import, so the name is for readability before import.

`nodes.json` uses the same shape as Telmi-Sync's own audio-list importer:

```
startAction → m[0]
m<k>   menu card: image m<k>.png, no audio, autoplay off, ok → s<k>
s<k>   chapter:   audio s<k>.mp3, autoplay on, ok → m[k+1], home → m[k]
```

The last chapter's `ok` is `null`, which is how the published sample packs end a story.

A Telmi story has no night-mode flag: the player's night mode takes the story already open in front of it. What decides whether a chapter joins that playlist is the graph's own `control.autoplay` and `control.ok`, set so a chapter runs on by itself. A Lunii archive carries one word, `nightModeAvailable`, and a converted audiobook always sets it, because it never asks for anything but the next chapter.

### Lunii `.plain.pk`

The graph is flattened into fixed-size records instead of a `story.json`; the media is renamed to the eight hex characters the card reads. Stored, not deflated.

| File | |
| :--- | :--- |
| `nm` | no bytes; its presence is what offers night mode |
| `ni` | a 0x200 header (format version, story version, header size, node size, and the node, `ri` and `si` counts), then one 0x2C record per stage: its picture and sound as `ri`/`si` indices, an OK and a home press each as `(base, count, pick)` into `li`, then the wheel, ok, home, pause and autoplay flags as 16-bit words. `-1` is the device's own "nowhere to go" |
| `li.plain` | the stage indices a choice leads to, four bytes each. One action node owns one unbroken run of them, which is what a transition's `base` and `count` point at |
| `ri.plain`, `si.plain` | one 12-character name per picture and per sound: `000\` then eight hex characters |
| `rf/000/<8 hex>.bmp` | the card: 320×240, four bits a pixel, BI_RLE4, sixteen colours, lines stored top line first, `biClrUsed` left at zero and `biClrImportant` at 16 |
| `sf/000/<8 hex>.mp3` | MP3, at least 44100 Hz, mono. Anything else is re-encoded on the way in |
| `uuid.bin` | the story's 16 uuid bytes |
| `_metadata.json`, `_thumbnail.png` | the title and picture Lunii.QT lists the story under. Neither reaches a card |

The `bt` authorisation file a card really carries needs that card's own key, which is why only Lunii.QT writes it, as it copies a `.plain.pk` onto one.

### Merlin (beta)

No screen, no story graph: the micro-SD card holds one flat folder of `.mp3` and `.jpg` files named with a uuid, and a `playlist.bin` listing them as 152-byte records — the tree, the titles and the order are all in that file. `--format merlin` writes one record for the story and one per part, each MP3 at 44.1 kHz stereo 128 kb/s with no ID3 tag, each picture a 128×128 JPEG. The device never reads a record's title aloud, so each part's audio starts with its chapter card spoken.

`--merlin-merge PLAYLIST.bin` writes a file holding the card's own records plus this story under its root; without it the file lists only this story, and the card's own content stops showing until its original file is put back. `IMPORT.txt` in the archive says which files to copy and which ones (`sleep.cfg`, `wifi.cfg`, the `default` folder) never to delete.

The format is undocumented and nothing here has been read back off a real Merlin. Telmi, Lunii and Flam are the stable outputs.

### Contract, taken from the reference implementations

`DantSu/Telmi-Sync` (`ConvertFolder.js`, `Stories.js`, `Strings.js`, `FFmpegCommand.js`) and `DantSu/Telmi-story-teller` (`stories_reader.h`):

- A story folder must contain **all six** of `audios/`, `images/`, `metadata.json`, `nodes.json`, `title.mp3`, `title.png`. `readStoryMetadata` deletes a folder missing any of them, and `storyFormatDetector` reports "Invalid story format".
- `metadata.json` needs `title`, `uuid`, `image`, `version`; `age`, `category` and `description` are optional.
- Audio is accepted unconverted only as MP3, 44100 Hz, mono or stereo, 64–192 kbps. Telmi-Sync re-encodes otherwise, but **only on the conversion import paths**: `ConvertFolderTelmi.js`, which handles a pack already in Telmi format, is a plain `copyFileSync` of every file. A Telmi-format pack must arrive with audio already inside the range.
- The player is not that strict: `mp3_reader.h` calls `mpg123_format(handle, 44100, 2, …)`, so mpg123 resamples any source rate. A 22050 Hz mono MP3 plays; it stays at source quality.
- A stage's `image` and `audio` may be `null`. `image` is loaded from `images/`, `audio` from `audios/`.
- An action is a **list** of `{stage}` options; a transition's `index` selects one. With no `index`, the player picks at random.
- A `null`-audio stage with `autoplay: true` advances immediately — Telmi-Sync's own idiom for a skipped stage.

## Cover

ffmpeg crops the cover to `640x480` for `title.png` and every card, and to `512x512` for `cover.png`. The card text is drawn by the converter, not by ffmpeg: Homebrew's ffmpeg has no `drawtext`, so `src/daisy2telmi/images.py` carries a 5x7 bitmap font and ffmpeg only composites the result. The font is Latin, so Chinese and Arabic cards show the number alone; the spoken card still says the word.

The GUI looks a cover up by itself as soon as a pack is picked or dropped, and never replaces one already typed. The web page does the same once it has read the pack. Both keep a link that opens a Google image search for the pack's title, and both take a cover from a file or from a link. Both draw one frame at the chosen player's ratio over the cover, moved by dragging, by the slider, the wheel or the arrow keys. The frame's place travels with the conversion: `images.crop_view` carries it as a focus point, and the desktop keeps it until the cover, the player or the zoom changes. Open Library is asked rather than isbndb.com because it needs no key and answers browser requests.

## Speech

Chatterbox is not bundled — the binaries carry no torch — so daisy-convertor runs `src/daisy2telmi/chatterbox_speak.py` in an interpreter of your own: `pip install chatterbox-tts` first, and `--tts-python` picks the interpreter if the `python3` on `PATH` is not the one that has it. The app offers that install instead of asking you to type it: on Save, a machine without the package gets one dialog, OK runs the install in the interpreter named in the options and streams pip's output into the log, Cancel uses the machine's own voice. The options page says whether the package is in the interpreter it is pointed at and has an **Install Chatterbox** button.

One process speaks the whole batch, because loading the model costs more than saying a chapter number. Chatterbox leaves the GPU alone unless asked, so `--tts-device mps` takes the Apple GPU and `cuda` an Nvidia one. Chatterbox is retired upstream: as of August 2026 the repository is an unmaintained MIT archive.

Where `python3 -m venv` cannot bootstrap pip — Debian ships `venv` without `ensurepip` unless `python3-venv` is installed — the app fetches `get-pip.py`; where that cannot be fetched either, it rebuilds the environment with `--system-site-packages` so it can run the machine's own pip, which still installs inside the environment.

The built binaries carry espeak-ng, its shared libraries and its language data, so speech works with nothing installed. Windows builds use the Windows engine.

## GUI

`daisy-convertor` with no arguments opens a Tkinter window: pick or drop a pack, pick an output folder and an optional cover, watch the same log the CLI writes. The conversion runs on a worker thread; each run starts on an empty log and ends with a report repeating its errors.

**Options** holds the interface language, what the cards say, whether the drawn card counts the parts, autoplay, the speech engine and the voice, and the Chatterbox rows: which Python runs it, which device, the voice clip, and whether the package is in that Python. Closing it writes `~/.config/daisy2telmi/config.json` (`%APPDATA%\daisy2telmi\config.json` on Windows), which the CLI reads too, so a flag only overrides what is already saved.

Drag-and-drop needs the optional `tkinterdnd2`; without it the browse buttons work and the window says so. `build_binary.py` passes `--collect-all tkinterdnd2` when it is installed.

## Web page

`web/` is a static converter that runs entirely in the browser, deployed to GitHub Pages from the `web` job. It shares no code with the Python package, so `tests/web/converter.test.mjs` pins it to the same naming, uuid and outline behaviour, and `tests/test_web_converter.py` runs the browser modules under Node and validates their output with the same validator as the CLI.

Once a pack has been read, the page lists the parts with their lengths, each with a box: untick one to leave it out. The numbers belong to the chosen cut level, so changing level redraws the list; past 400 parts the page only says how many there are. **The chapter cards** holds three boxes: show how many parts there are on the drawn card, say the word chapter, say how many parts there are. The choices are kept in `localStorage` and travel to the drawn card text and to the card audio.

`web/voices/<language>/01.mp3` … `50.mp3` are the pre-recorded announcements ("Chapter 4"), `n-01.mp3` … `n-50.mp3` the numbers alone, and `of-pre.mp3`, `of-mid.mp3` and `of-post.mp3` the words the language puts around the total, split out of its own `{n} of {total}` pattern so a language that names the part before the number, or after both, is recorded as it speaks. `tools/make_web_voices.py` generates them all, from Chatterbox (`--engine chatterbox --python <interpreter with chatterbox-tts>`) or from espeak-ng, which is the default and needs no download, and levels every clip to the loudness the app levels its cards to. The chapter clips committed are the Chatterbox ones; the bare numbers and the fragments are espeak-ng, because Chatterbox 0.1.7 loses its place on a one-word phrase and answers "of" with 23 seconds of noise. A browser tab can play speech but cannot capture it into a file, so the clips are committed rather than synthesised during conversion; past chapter 50 the cards stay silent.

The page concatenates MP3 frame data instead of re-encoding. That is safe because the player resamples, but it means no clip trimming and no bitrate normalisation.

`web/emulator.html` plays a pack back before it is printed: drop in the Telmi zip, the STUdio zip or the `.plain.pk`, and the page draws the machine, its screen and its five keys, a key lit only where the pack switched it on. `web/emulator.js` reads the three archives into one model — a stage, its media, and where OK, home and the wheel lead — and walks it the way the firmware does, including the card's run-length bitmaps; `web/emulator-app.js` is the player on top of it. The keyboard follows the machine: arrows for the wheel, Enter or Space for OK, Backspace or `h` for home. A Telmi story has no wheel, so there the two side buttons seek ten seconds back and forward inside the clip.

## Tests

```bash
pip install -e .[dev]
pytest
node --test tests/web/*.test.mjs
```

385 Python tests, 110 for the browser modules. The integration tests skip themselves if `gon-ruby-mp3.zip` or `ffprobe` is absent. `tests/test_published_pack.py` checks a real published Telmi story against the same validator; it skips unless `TELMI_SAMPLE` points at one. `tests/test_lunii_pk.py` re-encodes the pictures of a real `.plain.pk`; it skips unless `LUNII_SAMPLE` points at one. `tests/test_gui.py` skips where there is no Tk or no display.

## Build a binary

`build_binary.py` produces a `--onefile` executable for the machine it runs on. PyInstaller cannot cross-compile, so build on the target OS:

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
python build_binary.py
```

Produces `dist\daisy-convertor.exe` (`dist/daisy-convertor` on macOS and Linux). `lxml.etree` and `lxml._elementpath` are passed as hidden imports; PyInstaller's own lxml hook does the rest.

`assets/icon.svg` is the one drawing; `tools/make_icons.py` redraws `assets/icon.ico`, `assets/icon.icns` and the three PNGs in `src/daisy2telmi/assets` from it, and those are committed because no build runner has an SVG rasteriser. `build_binary.py` passes the `.icns` on macOS and the `.ico` on Windows, and unpacks `icon.png` next to `gui.py`, which is where the window looks for it; `tools/make_appimage.py` uses it for the desktop file. Tk cannot scale a photo image, so the header's 64-pixel mark and the status bar's 16-pixel one are separate files.

`build_binary.py` locates ffmpeg on the build machine and embeds it with `--add-binary`. On Linux it copies ffmpeg's whole library closure first: PyInstaller follows ffmpeg's own dependencies but leaves `libdrm` and `libwayland` for the distribution, and a machine without them cannot crop a cover. If ffmpeg is missing the script stops rather than shipping something that needs a separate install. `DAISY2TELMI_FFMPEG` overrides the search. At runtime the converter looks for the bundled copy (`sys._MEIPASS`, then the executable's own folder) before falling back to PATH.

The Linux and macOS builds also bundle espeak-ng, the libraries it links and its language data: `build_binary.py` copies them to `dist/speech/` and rewrites the macOS load commands to `@loader_path`; at runtime `tts.py` points `ESPEAK_DATA_PATH` at the copied data and, on Linux, `LD_LIBRARY_PATH` at the copied libraries. Without a data folder the script says so and ships without speech — on Debian and Ubuntu that data is a separate package, `espeak-ng-data`, which `espeak-ng` only recommends.

### CI

`.github/workflows/build.yml` builds on push to `main`, each on its own runner, and attaches the artifacts to a GitHub Release on a `v*` tag:

| Artifact | Built on | Notes |
| :--- | :--- | :--- |
| `daisy-convertor.exe` | `windows-latest` | ffmpeg bundled; speech uses the Windows engine |
| `daisy-convertor_<version>_<arch>.deb` | `ubuntu-latest` | installs to `/usr/local/bin/daisy-convertor`, declares `ffmpeg` as a dependency rather than duplicating it. Written by `tools/make_deb.py` using only the standard library, so it can be produced and checked on any OS without `dpkg` or `fakeroot`. The job removes the `espeak-ng` package before its smoke test so the run has to use the bundled copy |
| `daisy-convertor-<version>-x86_64.AppImage` | `ubuntu-latest` | the same binary, packaged by `tools/make_appimage.py`; `appimagetool` is unpacked with `--appimage-extract` because runners have no FUSE |
| `daisy-convertor-<version>-macos-<arch>.dmg` | `macos-latest` (arm64) and `macos-15-intel` (x86_64) | **unsigned and un-notarised**: Gatekeeper blocks the first run, so open it with right-click → Open or `xattr -d com.apple.quarantine daisy-convertor` |

The release notes are the tag's own message, so the tag has to be annotated: `git tag -a -F notes.md v1.2`. A lightweight tag gives the release the commit's message instead.

## What the test pack contains

`gon-ruby-mp3.zip` — ATDO, *ごんぎつね* (Gon, the Fox), 2009, 86 files. It is **not** a textbook DAISY 2.02 pack, and the parser follows the pack rather than the spec.

| Observation | Consequence |
| :--- | :--- |
| There is **no `package.opf`**. Metadata lives in `ncc.html` as `<meta name="dc:*">`. | `ncc.html` alone is enough to accept a pack. `package.opf` is read only if present. |
| `ncc.html` has 26 outline items: 1 `h1 class="title"`, 6 `h1`, 19 `h2`, plus 6 `span class="page-normal"`. `ncc:depth` is 2. | `h1` opens a chapter, `h2`+ fold into it, `h1 class="title"` is front matter, `page-normal` spans are skipped. |
| 26 SMIL files and 26 MP3s, strictly one MP3 per SMIL. | A chapter's audio is a *set* of whole MP3 files, not one. |
| SMIL and MP3 names do **not** match: `hotl000c.smil` → `hotl0012.mp3`. | Never infer audio from filenames; always follow the `<audio src>` references. |
| Each SMIL tiles its MP3 with `<audio clip-begin clip-end>` ranges running 0 → declared duration. | Clip ranges are honoured, and adjacent clips of the same file are merged into one range. |
| A chapter's own SMIL covers only its heading audio (chapter 一 = 29.8 s). The body is in its `h2` children, each with its own MP3. | A chapter is its own SMIL **plus every descendant SMIL**, concatenated. Ignoring this would drop 95% of the book. |
| All 26 MP3s are 22050 Hz, mono, 56 kbps. | Re-encode is mandatory to reach Telmi's 44100 Hz / 64–192 kbps CBR. |
| `er_book_info.xml` lists a duration per SMIL; total 1376.645 s, matching `ncc:totalTime 00:22:56`. | Used as the independent check that no audio was dropped. |

| Scene | Chapter | SMILs merged | Source MP3s |
| :--- | :--- | ---: | ---: |
| s0 | 一 | 9 | hotl0035, hotl000B, hotl0007, hotl0008, hotl0009, hotl000a, hotl0037, hotl0038, hotl0010 |
| s1 | 二 | 5 | hotl0012, hotl0013, hotl0039, hotl0017, hotl0018 |
| s2 | 三 | 4 | hotl001a, hotl006B, hotl001e, hotl006C |
| s3 | 四 | 3 | hotl006D, hotl006E, hotl006F |
| s4 | 五 | 2 | hotl002a, hotl0070 |
| s5 | 六 | 2 | hotl0071, hotl0072 |
| title.mp3 | front matter | 1 | hotl0002 |

All 26 source MP3s are consumed; none are dropped.

## Assumptions

1. The shallowest heading level in the outline is the chapter level. In this pack that is `h1`; a pack that starts at `h2` splits on `h2`.
2. `class="title"` on a heading means front matter, not a chapter. Its audio becomes `title.mp3`.
3. `span class="page-normal"` / `page-front` / `page-special` are page anchors, not outline items.
4. SMIL document order is playback order.
5. `clip-begin` / `clip-end` are authoritative over the file's real length.
6. The final chapter's `ok` is `null`. The published sample pack does this; Telmi-Sync's own audio-list importer instead wraps around to the first chapter. Null is chosen so the story ends.
7. Stage keys are `m0..mN` (cards) and `s0..sN` (chapters), matching Telmi-Sync's audio-list importer.
8. `uuid` is `uuid5` seeded from `dc:identifier`, so re-converting the same pack gives the same story id. Telmi-Sync generates its own ids as `<prefix>-<hex timestamp>`; any slug-safe value under 36 characters is accepted.
9. Every chapter is re-encoded to 44100 Hz, mono, CBR 128 kbps, even when the source is already compliant.
10. `age` is 0 and `category` is empty: DAISY 2.02 carries no audience-age field, and both feed the folder name.
11. Every top-level outline entry with audio becomes a chapter, including entries such as *Table des matières* or *Fin du livre*. Nothing is dropped on a title guess.

## What was verified

Against `gon-ruby-mp3.zip`:

- 6 chapters, one per `h1` outline entry.
- Each chapter's rendered duration matches the sum of its SMIL durations from `er_book_info.xml` to under 1 ms. Chapters + `title.mp3` = 1376.645 s, exactly `ncc:totalTime`.
- All 7 output MP3s are 44100 Hz, mono, 128 kbps — inside Telmi's accepted range.
- The generated folder passes `validate_story_dir`, which encodes Telmi-Sync's import checks.

Against a second, structurally different pack (`Rachel-Corenblit-Wesh-la-fee.zip`, a French audiobook with 23 top-level entries): 23 chapters, folder `_00_Wesh la fee_292b74ca-…`, also passing `validate_story_dir`.

Against three other books (third-party packs, not shipped in this repo):

- `Are_you_ready_z3986.zip`, a DAISY 3 (Z39.86-2005) pack: `*.opf` + `*.ncx` + per-chapter SMIL, no `ncc.html`, capitalised `dc:` metadata. 9 chapters totalling 3027 s, 33 parts at paragraph level, 569 at sentence level; the folder passes `validate_story_dir`. Its SMIL trims with camelCase `clipBegin`/`clipEnd`; reading only 2.02's `clip-begin` leaves every clip untrimmed, so a part that plays part of a file gets the whole file.
- `WIPO-Treaty-D202Fileset`, a text-only DAISY 2.02 pack: 22 parts, no audio anywhere. espeak-ng spoke 1280 s of scenes plus a spoken title; the folder passes `validate_story_dir`.
- `valentin-hauy.epub`: 9 chapters, 28 paragraphs, 751 sentences. Spoken: 6716 s over 9 scenes plus a spoken title, passing `validate_story_dir`.

Against a real published Telmi story (`_00_La reine des pirates_fffffd-19d14f92f57`):

- `validate_story_dir` reports no problems for it, so the validator matches a pack the player accepts.
- `story_dir_name` reproduces its folder name exactly.
- `story_dir_name` was diffed against Telmi-Sync's real `generateDirNameStory` running in Node over six inputs, including accented, non-Latin, over-long and punctuation-heavy titles: byte-identical.

Against a real published Lunii pack (`Les Aventures Intergalacbriques.72275E37.plain.pk`, not shipped in this repo):

- `validate_plain_pk` reports no problems for it, so the checks it encodes match a pack the device takes.
- Our `ni` is byte-identical to the one Lunii.QT's own writer builds from the STUdio archive of the same story, except the byte after the counts: published packs leave it at zero, Lunii.QT writes one, and nothing reads it back.
- Each of its 15 bitmaps re-encodes through our RLE4 writer to the same palette levels, pixel for pixel.
- The `.plain.pk` written for `gon-ruby-mp3.zip` passes the same checks, and the browser converter's `ni`, `li.plain`, `ri.plain` and `si.plain` for that pack are byte-identical to the CLI's.

The browser converter (`web/`), run under Node against both packs:

- Same folder names as the CLI: `_00_ _fe3ea2d2-…` and `_00_Wesh la fee_292b74ca-…`.
- `storyUuid` reproduces the CLI's uuid5 exactly, so both produce the same story id for the same pack.
- Its output passes `validate_story_dir` and `validate_story_zip`, the same checks the CLI output is held to.
- Concatenated audio decodes as MP3 at the source rate; the 6 chapters total 1372.4 s against the CLI's 1369.4 s. The 3 s difference is encoder padding left at the end of each source file, which the CLI trims.
- Conversion time: 0.5 s for the 22-minute Japanese pack, 2.7 s for the 2 h 18 French one.
- Driven in headless Chrome against `gon-ruby-mp3.zip`: the zip picker and the folder picker each produce a downloadable zip that passes `validate_story_zip` and `validate_story_dir`, with no page errors. The download link is hidden until a story exists, and feeding a converted story back to the page reports that it is already a Telmi story.
- Under Node it reads the same three inputs the app does: a DAISY 3 pack's `*.opf`/`*.ncx` and its camelCase clip points, a text-only pack's parts and their words, an EPUB's outline anchors. It trims clips at MP3 frame boundaries, as the CLI does.

Builds:

- A `--onefile` PyInstaller build runs the whole conversion end to end with lxml bundled, and again with `PATH=/usr/bin:/bin` using the ffmpeg embedded in the bundle. That stripped-`PATH` run also spoke 6 French chapter cards through the bundled espeak-ng, whose load commands point at `@loader_path` and whose data comes from the copied `espeak-ng-data`.
- GitHub Actions builds and smoke-tests `daisy-convertor.exe` on a Windows runner, the `.deb` on Ubuntu and the `.dmg` on macOS.

## Known limitations

- **DAISY 3 has been run against one book.** `*.opf` + `*.ncx` + per-chapter SMIL is parsed, and verified on a single published 3.x pack; other 3.x dialects are unproven.
- **A pack with no recorded audio is only spoken on the desktop.** The web page makes no speech of its own, so it refuses such a pack rather than build a silent story.
- **TTS speaks the cards and any part that has no audio of its own.** The default voice is Chatterbox, which is not in the binaries; the engine the binaries do carry, espeak-ng, is robotic, and `--tts command` is the way to put any other model behind it.
- **The bundled ffmpeg is the build machine's binary**, unmodified and unsigned.
- **No cover art comes from the pack.** DAISY 2.02 audio packs carry no usable cover. A lookup needs the network, and a pack with no ISBN and no searchable title has nothing to ask with.
- **The drawn card label is uppercase Latin.** The bitmap font in `src/daisy2telmi/images.py` covers the eight Latin-script languages plus accented letters; Chinese and Arabic cards fall back to the bare number.
- **A non-Latin title collapses in the folder name.** `stringNormalizeFileName` keeps only `a-zA-Z0-9-.`, so *ごんぎつね* becomes a single space and the folder is `_00_ _<uuid>`. This is Telmi-Sync's own behaviour, not a converter bug; `metadata.json` keeps the real title.
- **Chapter granularity is fixed.** A pack with a single flat heading level becomes one chapter; there is no option to split on `h2`.
- **No resume or bookmarking.** Telmi has no position concept in this format, so a listener restarts at chapter 1.
- **The web page does not re-encode.** It joins MP3 frame data, so audio keeps the source sample rate and bitrate, and each source file's encoder padding survives (about 0.1 s per file). The player resamples, so it plays. Use the CLI when the audio has to meet the 44100 Hz / 64–192 kbps range exactly.
- **The web page needs a recent browser.** It relies on `DecompressionStream`/`CompressionStream` (Chrome 80+, Firefox 113+, Safari 16.4+) and `crypto.subtle`, which requires a secure context — `https://` or `localhost`.

## Next

A story-teller emulator that plays a converted story, a re-arranged Options page, DAISY import in Telmi-Sync.

## Licence

GPL-3.0-or-later, see `LICENSE`. Copyright 2026 berthy456 and qwen-flash-next.
