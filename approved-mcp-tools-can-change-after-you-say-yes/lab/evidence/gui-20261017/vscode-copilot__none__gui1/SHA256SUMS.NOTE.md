# Note on SHA256SUMS

`LOG.md` has one line appended after this manifest was first written on 17 October 2026: the last line, a correction saying that
screenshot 01 shows the desktop before VS Code was opened and that the run starts with screenshot 02. The manifest now carries
the hash of the current `LOG.md`. The hash first recorded, `b3b223bf4ad72d8f06598937aaff021cb6a7e0e3e97aeb6748e6a39cab98c0df`, is
the hash of `LOG.md` without its last line:

```bash
head -n -1 LOG.md | sha256sum
```

No other file in this folder changed.
