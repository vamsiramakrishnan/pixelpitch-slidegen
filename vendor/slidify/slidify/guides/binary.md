# Running slidify as a packaged binary

slidify has three runtime dependencies the OS must provide:

| Dep              | Why                                              |
|------------------|--------------------------------------------------|
| Chromium         | Renders HTML pixel-accurately (Playwright)       |
| LibreOffice      | Renders the produced PPTX for the SSIM oracle    |
| Tesseract OCR    | Per-slide OCR-recall check                       |
| poppler-utils    | LibreOffice → image fallback path                |
| Inter font       | Default deck typeface (also embedded in PPTX)    |

A pure-Python binary that bundles only Python deps is therefore not enough.
slidify ships **two** packaging modes:

## 1. Docker image — recommended

The cleanest way to ship slidify with every dependency pinned:

```bash
docker build -f packaging/Dockerfile -t slidify:latest .
docker run --rm -v "$PWD":/work slidify:latest \
       convert /work/deck.html /work/deck.pptx --json
```

The image is ~1.6 GB (LibreOffice dominates). Multi-stage build keeps the
runtime layer minimal.

## 2. PyInstaller onefile — for offline distribution

```bash
./packaging/build-binary.sh
# → dist/slidify       (single ELF, ~80 MB; no Python runtime needed)
```

The ELF bundles:

* Python 3.11 + every Python dependency
* Playwright driver + Chromium (auto-extracted on first run)
* slidify package data (patterns, guides, fonts)

It does **not** bundle LibreOffice, Tesseract, or poppler-utils — those must
exist on the host. Run `./dist/slidify doctor` to verify.

For fully air-gapped environments use the Docker image; the PyInstaller
binary is only "Python is missing" coverage, not full self-containment.

## 3. Hybrid: binary + companion deps tarball

For private fleets:

```bash
./packaging/build-binary.sh --with-deps
# → dist/slidify-bundle.tar.gz
#   ├── slidify           (ELF)
#   ├── deps/             (.deb files: libreoffice-core, tesseract, poppler)
#   └── install.sh        (idempotent installer for the .debs)
```

`install.sh` runs `dpkg -i` on the deps then symlinks slidify into
`/usr/local/bin`. Suitable for on-prem nodes that can't apt-get.

## Verifying a packaged binary

After install:

```bash
slidify doctor                  # human readout
slidify doctor --json           # machine-readable
slidify version --json          # versions of everything bundled
```

`doctor` exits non-zero if a required binary is missing on `$PATH`.
