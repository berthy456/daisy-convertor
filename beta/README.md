# Beta

Windows builds from runs of the `build` workflow, before they are a release. Each build keeps its own folder, numbered up from `beta1`; an older beta is never overwritten.

| Beta | Commit | Build run | Joined size |
| :--- | :--- | :--- | :--- |
| `beta1` | `cdf5add` | [45655770154](https://github.com/berthy456/daisy-convertor/actions/runs/45655770154) | 162535356 bytes |
| `beta2` | `b46df2f` | [45693256180](https://github.com/berthy456/daisy-convertor/actions/runs/45693256180) | 162551078 bytes |
| `beta3` | `5e3224b` | [45711277806](https://github.com/berthy456/daisy-convertor/actions/runs/45711277806) | 162548979 bytes |

The other builds of the same commit (macOS, .deb, AppImage) are the artifacts of the run named beside it.

Each folder holds `daisy-convertor-1.5-windows-x86_64.zip` stored in parts: the file is bigger than GitHub's 100 MB per-file limit, and neither part is a zip on its own — 7-Zip fails on each until they are joined. Run `join.bat` in the folder, or join them yourself:

```
copy /b daisy-convertor-1.5-windows-x86_64.zip.part* daisy-convertor-1.5-windows-x86_64.zip
```

A joined file is the size in its own row of the table above; anything smaller is a part that was missing.

Unzip it and run `daisy-convertor.exe`. Double-clicked it opens the window with no log; started from a terminal it writes one:

```
daisy-convertor.exe --verbose
daisy-convertor.exe "C:\path\book.zip" C:\path\story --find-cover -v
```

The first opens the window with the detailed log in it. The second converts from the command line and writes the log into the terminal it was started from.
