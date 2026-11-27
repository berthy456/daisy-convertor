# Beta

Builds from runs of the `build` workflow, before they are a release. Each build keeps its own folder, numbered up from `beta1`; an older beta is never overwritten.

| Beta | Commit | Build run | Joined size |
| :--- | :--- | :--- | :--- |
| `beta1` | `cdf5add` | [45655770154](https://github.com/berthy456/daisy-convertor/actions/runs/45655770154) | 162535356 bytes |
| `beta2` | `b46df2f` | [45693256180](https://github.com/berthy456/daisy-convertor/actions/runs/45693256180) | 162551078 bytes |
| `beta3` | `5e3224b` | [45711277806](https://github.com/berthy456/daisy-convertor/actions/runs/45711277806) | 162548979 bytes |
| `beta4` | `6e4685a` | [45790528738](https://github.com/berthy456/daisy-convertor/actions/runs/45790528738) | 162564634 bytes |
| `beta5` | `abb3750` | [45878975449](https://github.com/berthy456/daisy-convertor/actions/runs/45878975449) | 142644817 bytes |
| `beta6` | `7cafdf9` | [45956814893](https://github.com/berthy456/daisy-convertor/actions/runs/45956814893) | 142652807 bytes |

`beta1` to `beta3` hold the Windows build only; the other builds of those commits (macOS, .deb, AppImage) are the artifacts of the run named beside it. `beta4` to `beta6` hold four builds each:

| File in `beta4` | Size |
| :--- | :--- |
| `daisy-convertor-1.5-windows-x86_64.zip` | 162564634 bytes, in two parts |
| `daisy-convertor-0.0.0-dev-6e4685a-x86_64.AppImage` | 125789376 bytes, in two parts |
| `daisy-convertor-0.0.0-dev-6e4685a-macos-arm64.dmg` | 53249719 bytes |
| `daisy-convertor-0.0.0-dev-6e4685a-macos-x86_64.dmg` | 55609717 bytes |

| File in `beta5` | Size |
| :--- | :--- |
| `daisy-convertor-1.6-setup.exe` | 142644817 bytes, in two parts |
| `daisy-convertor-1.6-x86_64.AppImage` | 125793472 bytes, in two parts |
| `daisy-convertor-1.6-macos-arm64.dmg` | 53257124 bytes |
| `daisy-convertor-1.6-macos-x86_64.dmg` | 55609056 bytes |

| File in `beta6` | Size |
| :--- | :--- |
| `daisy-convertor-1.6-setup.exe` | 142652807 bytes, in two parts |
| `daisy-convertor-0.0.0-dev-7cafdf9-x86_64.AppImage` | 125805760 bytes, in two parts |
| `daisy-convertor-0.0.0-dev-7cafdf9-macos-arm64.dmg` | 53270982 bytes |
| `daisy-convertor-0.0.0-dev-7cafdf9-macos-x86_64.dmg` | 55609405 bytes |

A build of a commit that is not a release tag carries `0.0.0-dev-<commit>` in its name instead of a version; the app inside still reports the version it was built with. `beta5` is the `v1.6` build, and `beta6`'s Windows setup keeps that name because the app inside still reports 1.6.

A file bigger than GitHub's 100 MB per-file limit is stored in parts, and neither part opens on its own — 7-Zip fails on each until they are joined. Join them with the script in the folder:

```
sh join.sh
join.bat
```

or join them yourself:

```
copy /b daisy-convertor-1.6-setup.exe.part* daisy-convertor-1.6-setup.exe
cat daisy-convertor-1.6-x86_64.AppImage.part* > daisy-convertor-1.6-x86_64.AppImage
```

A joined file is the size in its own row of the table above; anything smaller is a part that was missing.

`beta4`'s Windows build is a zip: unzip it and run `daisy-convertor.exe`. Double-clicked it opens the window with no log; started from a terminal it writes one:

```
daisy-convertor.exe --verbose
daisy-convertor.exe "C:\path\book.zip" C:\path\story --find-cover -v
```

The first opens the window with the detailed log in it. The second converts from the command line and writes the log into the terminal it was started from.

`beta5`'s Windows build is a setup: it installs the app into `%LOCALAPPDATA%\daisy2telmi\app`, adds a Start Menu entry, and offers Python 3.11. From that folder the installed app answers to the same two commands as `daisy-convertor.exe`.
