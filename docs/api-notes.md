# Live 12.4.6 API notes (probed 2026-10-10, Suite, macOS)

Probed with `scripts/smoke.py --probe` and the `runtime_info` command.

## Runtime
- Embedded Python **3.11.6**. Remote Script must be stdlib-only and 3.11-compatible.
- `update_display` ticks about every **300 ms**, which made every request wait about 300 ms.
- `Live.Base.Timer(callback=, interval=ms, repeat=True)` runs on the main thread and fires about every
  10 ms. Intervals below 10 ms didn't lower latency.
- Measured ping round trips: socket thread + `update_display` ≈ 300 ms; socket thread + Timer ≈ 40 ms
  (background threads wait for Live's main thread to release the GIL); **no threads, non-blocking socket
  polled from the Timer ≈ 10 ms** (v0.2.0, current design).
- Hot reload works: the `reload` command reloads `lom.py` and `handlers.py` in place.
- Live keeps imported modules for the whole session. Toggling the control surface creates a new
  instance from the cached modules, so in 0.1.0/0.1.1 edits to `surface.py` only loaded after a Live restart.
  Since 0.1.2, `create_instance` reloads every module itself, so toggling the surface picks up edits.

## Available (all present)
| Object | Members |
|---|---|
| Song | `begin_undo_step`/`end_undo_step`, `root_note`, `scale_name`, `scale_mode`, `capture_midi`, `duplicate_track`, `tempo_follower_enabled` |
| Track | `insert_device`, `delete_device`, `arrangement_clips`, `duplicate_clip_to_arrangement`, `create_midi_clip`, `create_audio_clip` |
| Song.View | `selected_track`, `select_device` |
| Browser | `load_item`, `instruments`, `audio_effects`, `user_library`, `plugins` |

Device-specific classes exposed in `Live`: `Eq8Device`, `CompressorDevice`, `RoarDevice`, `DriftDevice`,
`MeldDevice`, `WavetableDevice`, `SimplerDevice`, `HybridReverbDevice`, `SpectralResonatorDevice`, `ShifterDevice`, `MaxDevice`, …

## Devices and browser (probed for Phase 5)
- `Track.insert_device(name)` loads stock devices by display name ("Drift", "EQ Eight") and returns the device.
  With an index argument, Live still enforces one instrument per chain ("Device chains cannot have more than one instrument").
- Availability follows the running edition: in **Live 12 Standard.app**, "Wavetable" raises "Device Wavetable not available".
  Both Standard and Suite apps are installed on this Mac, so check which one is running (`ps`) when a device fails.
- Drum kits and presets load through the browser: select the track (`song.view.selected_track`), then
  `app.browser.load_item(item)`. `app.browser.drums.children` lists 147 items (`505/606/707/808/909 Core Kit.adg`, …).
- Pad names come from the kit (e.g. 909 Core Kit: "Bass Drum", "Snare Drum", "Hand Clap", "Closed Hi Hat").
- `song.scale_intervals` gives the current scale's intervals. Setting `scale_name` to an unknown name
  ("Aeolian", "Natural Minor") **silently falls back to Major**, so names are validated in `set_song`.

## Automation (probed for Phase 5)
- Clips have `automation_envelope(param)`, `create_automation_envelope(param)`, `clear_envelope(param)` and `clear_all_envelopes()`.
  An envelope is only returned from a method call (it has no LOM path), so the `envelope` handler works with it inside Live.
- `Envelope.create_event(Live.Envelope.EnvelopeEvent(time, value[, control_coefficients]))` adds a breakpoint;
  Live interpolates **linearly in the raw 0..1 range** between breakpoints. (Midpoints of a dB ramp aren't the dB midpoint.)
  `EnvelopeEventControlCoefficients` (x1, y1, x2, y2) can bend a segment; not used yet.
- `insert_step(time, length, value)` exists too, but stepped shapes are written as breakpoint pairs instead.
- `events_in_range` reports values in natural units (Hz), `value_at_time` in raw 0..1.
- **No track-level arrangement automation:** Track has no envelope methods, and DeviceParameter only has
  `automation_state` / `re_enable_automation`. Arrangement automation goes into arrangement clips
  (`automate(arrangement_clip=...)`). The only other route is recording parameter moves
  (`begin_gesture`/`end_gesture`) during an arrangement recording; that was rejected as fragile.
- Parameter names differ per device: Drift "LP Freq", Wavetable "Flt 1 Freq", Auto Filter "Frequency".
  `get_device(filter=...)` matches common abbreviations.
- `runtime_info(module="Envelope.Envelope")` prints the docstrings of Live's Python classes, handy for exploring.

## Arrangement editing (probed for Phase 8)
- No insert/delete-time API. Moving material = `Track.duplicate_clip_to_arrangement(clip, time)` then
  `Track.delete_clip(clip)`; `create_midi_clip(start, length)` makes exact-length clips. Clips can't be
  split, so edits through a clip are refused. Track automation lanes don't move with clips.
- **Trimming by overwrite (observed, Live 12.4):** `duplicate_clip_to_arrangement` onto a spot that
  an existing clip covers makes Live cut that clip there; the part under the copy is replaced.
  Copying a longer clip of the same track to beat `e` and then deleting the copy leaves the earlier
  clip ending at `e`. Setting `end_marker` or calling `crop()` doesn't change an arrangement clip's
  `end_time`.
- Arrangement clip indexes shift as clips are added or removed, so the `arrangement_ops` handler
  addresses clips by (track, start time) and runs a whole edit as one undo step.
- **Locators:** the only API is `Song.set_or_delete_cue()` at the playhead. A new `current_song_time`
  takes effect a tick later. In the same tick, the toggle hits the *old* position and can
  **delete the wrong locator** (observed). So locator ops run as separate, verified calls from the
  server: move the playhead, confirm it, check `is_cue_point_selected()`, toggle, verify.
- `CuePoint.name` is writable; `CuePoint.time` is not (moving = delete + add).
- **Playback start:** `Song.start_playing()` begins at `Song.start_time` (the play-start marker), *not*
  at `current_song_time`. Setting only the playhead and pressing play starts from the old marker
  (observed). To play from a position, set `start_time` (both, to move the playhead too).
  Both are refused past the song length ("Cannot set the Songtime behind the Songlength").

## Implications for later phases
- Phase 5: use `Track.insert_device` first; the browser search is only a fallback (e.g. for presets and plugins).
- Phase 8: `create_midi_clip`/`create_audio_clip` and `duplicate_clip_to_arrangement` allow building arrangement sections.
- Use `begin_undo_step`/`end_undo_step` (via `batch(undo_name=...)`) so a multi-step tool undoes in one step.

## Known gaps
- Group tracks can't be created through the API.
