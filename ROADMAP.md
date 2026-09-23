# Roadmap

Open items after 2.0. Each says what is left and what would prove it done.

## Needs a person at the machine

- **Cursor overlay with a physical mouse** (was #8). The extension can draw
  the agent's pointer; a first version broke clicking with a physical mouse
  (2026-08-04). The fix applies the position once per frame and was measured
  in a nested shell (263 draws for 1992 events, the frame rate), not with a
  real mouse. Off by default; turn on with the flag file
  `~/.local/state/pcbridge/gorunur-imlec`. Done when clicking and scrolling
  with the physical mouse stay normal for a working session with it on.
- **`FocusedWindow` in the real session** (was #10). Measured in a nested
  shell; the real session loads the extension at the next login. Done when
  `pcbridge doctor` reports the 2.0 extension and a `computer_batch` with a
  click succeeds while a window AT-SPI cannot see (a native Wayland game) is
  in front.
- **OCR on this machine** (was #11). `find_text` / `wait_for_text` need
  `sudo apt install tesseract-ocr` (plus `tesseract-ocr-tur` for Turkish).
  Measure: finding and clicking a known word in `gnome-text-editor`, a game
  menu entry, and the time to read a full monitor.

## Engineering

- **Retire the legacy Python desktop backends** (was Faz 8). The native
  helper is the default for capture, input and accessibility; the Python
  paths remain as the fallback when no helper is packaged. Retiring them
  needs the helper on every supported platform build and a release with the
  fallback unused.
- **The pixel-area cap and the Anthropic API.** Whether the API itself
  shrinks a 1536x864 picture (about 1.33 MP) was not measured. If it does,
  `shot=` coordinates carry a small systematic error on the right edge, and
  the default cap should drop to what the API keeps as is.
- **A repeat-click counter across calls.** The third-click stop works within
  one sequence; an agent looping over separate `ui_click` calls is not
  caught (see [docs/dev/desktop-rules.md](docs/dev/desktop-rules.md)).
- **lintian in CI** for the `.deb`.

## Decided and closed

- Windows and macOS backends, and an XDG-portal capture backend, were
  dropped (2026-09-20): the project targets GNOME on Wayland.
- `JARVIS.md`, a proposal to grow pcbridge into a personal assistant, is not
  documentation and was removed in 2.0; git history keeps it.
