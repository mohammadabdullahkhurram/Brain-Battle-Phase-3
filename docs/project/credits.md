# Credits & licences

**What this page covers:** every third-party asset bundled with Brain
Battle — where it came from, its licence, and (for the round bell) exactly
how it was processed — so attribution stays correct and traceable as the
app changes.

## Round bell

| | |
|---|---|
| Title | "Boxing Bell.wav" |
| Author | Benboncan |
| Source | [freesound.org/people/Benboncan/sounds/66951/](https://freesound.org/people/Benboncan/sounds/66951/) |
| Licence | [Creative Commons Attribution 4.0 (CC-BY 4.0)](https://creativecommons.org/licenses/by/4.0/) |

The untouched original (Freesound's own high-quality preview encode — the
source `.wav` requires a Freesound login to download) is kept at
`assets/sounds/source/boxing_bell_benboncan_66951_preview-hq.mp3`.
`tools/make_round_bell.py` turns it into the WAV the app actually plays,
`assets/sounds/round_bell.wav`: downmixed to mono, trimmed to start right on
the strike and keep ~2 s of natural ring with a gentle fade-out, a high-pass
at 120 Hz, a high-shelf cut above 4 kHz to soften the top end, and loudness
normalised to about −20 LUFS (true peak ≤ −3 dBTP), 44.1 kHz / 16-bit / mono.
See the script for the exact filter chain and every constant — it documents
itself and can be re-run if the source or the target levels ever change.

`sync_beep.py` plays this file through `QSoundEffect` for the "Ring a bell
when each round starts" preference in [Settings](../user-guide/settings.md).

## Inter (UI typeface)

| | |
|---|---|
| Font | Inter |
| Author | The Inter Project Authors |
| Source | [github.com/rsms/inter](https://github.com/rsms/inter) |
| Licence | SIL Open Font License 1.1 (SIL OFL) |

Bundled as `assets/fonts/Inter-{Regular,Medium,SemiBold,Bold}.ttf`, with the
full licence text at `assets/fonts/Inter-OFL.txt`, and registered at startup
by `fonts.py` so the UI looks the same regardless of what's installed on the
machine.
