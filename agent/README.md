# agent/: the assistant's working folder

| Folder | What goes here | In git? |
|---|---|---|
| `exports/` | Mixes and stems you export from Live for analysis. One subfolder per song, e.g. `exports/night-drive/mix_v3.wav`, `exports/night-drive/stems/bass.wav` | no |
| `references/` | Commercial reference tracks to compare against | no |
| `captures/` | Master recordings from the Max for Live analyzer (Phase 11) | no |
| `reports/` | Analysis and mastering-iteration reports (JSON) | no |
| `presets/` | Mastering chain presets (JSON) | yes |

Export from Live with **File → Export Audio/Video** and choose a folder under `exports/`; Live remembers it.
WAV at the project's sample rate, 24-bit, no dither and **no normalization** gives the most accurate loudness readings.

Set `ABLETON_MCP_AGENT_DIR` to use a different location.
