# Automated test report

Run on 2026-11-12 with Python 3.10.11 on Windows.

Command:

```text
python -m unittest discover -s tests -v
```

Result: **23 tests passed, 0 failed, 0 skipped**.

The suite covers known SHA-256 vectors and content changes; relative and absolute paths; Chinese and space-containing names; case-insensitive Windows comparison; traversal, device namespace, alternate data stream, trailing-dot/space and reserved-device-name rejection; symbolic-link/junction escape and reparse ancestors; whitelist changes, additions and removals; and manifest creation, duplicate rejection, missing files and hash/size mismatch.

The synthetic manifest example was also verified successfully with `python examples/verify_sample.py`.

The reparse tests used Windows directory junctions because this environment does not grant ordinary symbolic-link creation privileges. The code rejects both symlinks and Windows reparse points.
