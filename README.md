# daisy-convertor

Turn an audiobook into a story for a **Telmi**, a **Lunii**, a **Flam** or a **Merlin** story-teller.

Give it a book — a DAISY audiobook, an EPUB, or a folder holding either — and it gives back a zip the machine understands: one part per chapter, the book's own audio, and a picture for each part.

Nothing is uploaded anywhere. The web page converts the book inside your browser; the app converts it on your machine.

## Get it

| Your machine | File to download |
| :--- | :--- |
| Windows | `daisy-convertor-<version>-windows-x86_64.exe` |
| Linux | `daisy-convertor-<version>-x86_64.AppImage`, or the `.deb` |
| Mac, Apple silicon | `daisy-convertor-<version>-macos-arm64.dmg` |
| Mac, Intel | `daisy-convertor-<version>-macos-x86_64.dmg` |

from the [releases page](https://github.com/berthy456/daisy-convertor/releases/latest). Everything the converter needs is inside the file: no ffmpeg, no Python to install.

The Mac archive is not signed by Apple, so the first run needs right-click → **Open** instead of a double-click.

**Or use nothing at all:** the same converter runs in a browser tab at <https://berthy456.github.io/daisy-convertor/>. It asks the same questions in the same order as the app, and hands you the same zip.

## Start here

The steps below name the app's buttons; the web page has the same ones under the same words.

### 1. Open the book

Type the book's path in the **DAISY pack** line, or use **Browse zip...** for a `.zip` or `.epub` file and **Browse folder...** for a book that is already unzipped. The web page has **Choose a zip** and **Choose a folder**, and takes a book dropped onto the page.

The story is written next to the book, so you find it where the book already is.

### 2. Say which machine the story is for

**Story format**: Telmi, Lunii (B/W), Commercial PK, Flam, or Merlin (beta). Choose this before the cover: it decides the shape of every screen the pictures have to fit, and the pictures are drawn to match.

Only the plain Lunii's screen has no colour, so only next to it does a **Negative cards** tick appear: it turns the picture inside out, as an old film's negative reads.

### 3. Give it a cover

Audiobook packs rarely carry one, so you can: **Choose image...** picks a picture from your machine, and **Search online...** looks for the print book the audiobook was read from. The app searches by itself as soon as you pick the book, and leaves a cover you typed alone.

Without a cover the cards still work: they get a plain background and the chapter number.

### 4. Frame the cover

Under **Image crop to fit** is a picture: that box is exactly what the machine will show, at that machine's shape. A tall book cover does not fit a wide screen, and which part survives is easier to see than to describe.

Drag the picture to move it. The **Zoom** slider, the mouse wheel, or the arrow keys scale it: at **0** the whole cover shows and the card's own colour fills the room a differently-shaped screen leaves, at **1** the cover fills the screen, and past that it grows and the frame cuts it, which also opens room to move sideways. **Top**, **Center** and **Bottom** jump back to the three standard framings; **Reset** starts over.

### 5. Choose how the book is cut

**Chapters** follows the book's own outline. **Paragraphs** and **Sentences** make many short parts instead, which is how a long chapter becomes something a child listens to in one sitting. The book's own marks decide how many parts each gives, and the app only offers the levels that book can actually give.

### 6. Listen, and drop what is not the story

**Chapters** opens the list. Select a line and press **Listen**, or double-click it, to hear a chapter before deciding. One part plays at a time, and the button becomes **Stop** while it plays, so a part you have heard enough of is over when you say so. Untick the ones that are not part of the story — the legal notice, the table of contents, the "end of book" announcement — and what is left is renumbered, so the cards still count from 1.

### 7. Decide what the cards say

**Options** holds the interface language (English, French, Chinese, Arabic, Turkish, German, Italian, Spanish, Portuguese, Dutch) and what each chapter card says when it appears: nothing, the number, the number and how many parts there are, the part's own name, "Chapter 4", or the number and a question inviting the listener to continue. A box next to it decides whether the card also *shows* how many parts there are — `4/12` or just `4`. The web page asks the same three things under **The chapter cards**: show how many parts there are, say the word chapter, say how many parts there are.

The app can read the cards in a real voice. On the first conversion it offers to install that voice; say no and the machine's own voice reads them, or the cards stay silent. The web page uses recorded announcements instead, which are already in the page.

### 8. Save the zip

**Save the zip**. The log below the buttons shows what is happening and what was found; each run starts on an empty log and ends with a report that repeats its errors in red. When it finishes, the zip is next to the book.

### 9. Play it first

**Emulator** opens the story the last run wrote and plays it with the machine's own buttons: the wheel, OK, Home, and Stop to silence the card that is playing. A Lunii story is opened from its archive, so its cards show the greys that screen keeps. The same emulator runs in a browser tab at <https://berthy456.github.io/daisy-convertor/emulator.html>, where it also reads a `.pk` card.

### 10. Put it on the machine

| Machine | What to do with the zip |
| :--- | :--- |
| Telmi | Telmi Sync → **Import → zip** |
| Lunii (B/W) | the zip in Lunii STUdio; its cards are drawn in the sixteen greys that screen keeps |
| Commercial PK | the `.pk` in Lunii.QT or open-story-teller, which writes it onto a story card. The same story in the layout the card keeps, and each picture brings its own sixteen colours |
| Flam | the same Telmi import; the Flam's own screen sizes are already used |
| Merlin | the unzipped folder onto the micro-SD card; `IMPORT.txt` inside says which files to copy and which never to delete |

## Small things that come up

**The Mac says the app cannot be opened.** It is not signed by Apple. Right-click it and choose **Open** once; after that it opens normally.

**The AppImage does not start.** Make it executable first: right-click → Properties, or `chmod +x daisy-convertor-*.AppImage` in a terminal.

**No cover was found.** Not every audiobook names the print book it came from. Pick a picture yourself, or paste a link to one.

**The cards say nothing.** Either the cards were set to silent, or the voice was not installed. **Options** says which voice is being used.

**Nothing happens when a chapter is clicked.** The chapters window plays through the machine's own audio player; if the machine has none, the line still shows the chapter's length.

**"already holds a story: nothing was written".** A story with that name was made before, and the log says so in red. Tick **Overwrite an existing story folder** to replace it.

## For developers

[manual.md](manual.md) holds the command line, every option, the story formats and how they are written, the browser converter's limits, the tests, and how to build the binaries yourself.

## Licence

GPL-3.0-or-later, see `LICENSE`. Copyright 2026 berthy456 and qwen-flash-next.
