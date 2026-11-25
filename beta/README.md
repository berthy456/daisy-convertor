# Beta

The Windows build from the latest run of the `build` workflow, before it is a release.

`daisy-convertor-1.5-windows-x86_64.zip` is bigger than GitHub's 100 MB per-file limit, so it is stored in parts. Neither part is a zip on its own: 7-Zip fails on each until they are joined. Put every part in one folder and run `join.bat` there, or join them yourself:

```
copy /b daisy-convertor-1.5-windows-x86_64.zip.part* daisy-convertor-1.5-windows-x86_64.zip
```

A joined file is 162535356 bytes; anything smaller is a part that was missing.

Unzip it and run `daisy-convertor.exe`. Double-clicked it opens the window with no log; started from a terminal it writes one:

```
daisy-convertor.exe --verbose
daisy-convertor.exe "C:\path\book.zip" C:\path\story --find-cover -v
```

The first opens the window with the detailed log in it. The second converts from the command line and writes the log into the terminal it was started from.
