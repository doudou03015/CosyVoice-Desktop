# BSD codec notices bundled with SoundFile

These are unmodified copyright and license texts fetched from the official Xiph
repositories at the corresponding release tags. `sources.json` records each
upstream URL and SHA-256 of the included notice.

| Bundled libsndfile | SoundFile | libFLAC | libOpus | libVorbis | libOgg |
| --- | --- | --- | --- | --- | --- |
| 1.2.0 (Windows x64 runtime DLL) | 0.12.1 | 1.3.4 | 1.3.1 | 1.3.7 | 1.3.5 |
| 1.2.2 (Windows x64 GUI DLL) | 0.14.0 | 1.4.3 | 1.5.2 | 1.3.7 | 1.3.5 |

FLAC, Opus and Vorbis versions were checked against version strings embedded in
the distributed DLLs. Ogg 1.3.5 is pinned by both corresponding vcpkg port records.
Only the libFLAC library is used; its applicable notice is `COPYING.Xiph`, rather
than the licenses of the separate FLAC command-line programs.

SoundFile's official binary submodule states that these codecs, along with MP3
encoding and decoding libraries, are statically incorporated in libsndfile:
<https://github.com/bastibe/libsndfile-binaries>.

SoundFile release 0.12.1 pins binary repository commit
`d9887ef926bb11cf1a2526be4ab6f9dc690234c0`; release 0.14.0 pins
`a3e6f9769d0c7e91d2d036cf0fdbe5b4bbf18b87`.
The upstream licensing notes also identify the BSD components:
<https://github.com/bastibe/python-soundfile/blob/0.14.0/licensing/license_notes.md>.

The LGPL libsndfile, LAME and mpg123 notices, corresponding sources and build
recipes are provided separately in the release licensing materials.
