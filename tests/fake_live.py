"""A tiny stand-in for Live's Python API, just enough to exercise the stack."""

from __future__ import annotations

import math
import sys
import types


class MidiNoteSpecification:
    def __init__(self, pitch, start_time, duration, velocity=100.0, mute=False, probability=1.0,
                 velocity_deviation=0.0, release_velocity=64.0):
        self.pitch, self.start_time, self.duration = pitch, start_time, duration
        self.velocity, self.mute, self.probability = velocity, mute, probability
        self.velocity_deviation, self.release_velocity = velocity_deviation, release_velocity
        self.note_id = None


class EnvelopeEvent:
    def __init__(self, time, value, control_coefficients=None):
        self.time, self.value = time, value


class Envelope:
    def __init__(self):
        self.events: list[EnvelopeEvent] = []

    def create_event(self, ev):
        self.events.append(ev)
        self.events.sort(key=lambda e: e.time)

    def value_at_time(self, t):
        evs = self.events
        if t <= evs[0].time:
            return evs[0].value
        for a, b in zip(evs, evs[1:]):
            if a.time <= t <= b.time:
                f = (t - a.time) / (b.time - a.time) if b.time > a.time else 1.0
                return a.value + (b.value - a.value) * f
        return evs[-1].value


def install_fake_live_module() -> None:
    live = types.ModuleType("Live")
    live.Clip = types.SimpleNamespace(MidiNoteSpecification=MidiNoteSpecification)
    live.Envelope = types.SimpleNamespace(EnvelopeEvent=EnvelopeEvent)
    sys.modules["Live"] = live


class Parameter:
    def __init__(self, name, value=0.0, min=0.0, max=1.0, unit=""):
        self.name, self.value, self.min, self.max, self.unit = name, value, min, max, unit
        self.is_quantized = False

    def str_for_value(self, v):
        return f"{v:.2f}{self.unit}"

    def __str__(self):
        return self.str_for_value(self.value)


class VolumeParameter(Parameter):
    """Monotonic dB taper like Live's: 0 -> -inf, 0.85 -> 0 dB, 1.0 -> +6 dB (approximately)."""

    def str_for_value(self, v):
        if v <= 0.0005:
            return "-inf dB"
        db = 6 * math.log10(v / 0.85) / math.log10(1 / 0.85)  # 0.85 -> 0 dB, 1.0 -> +6 dB
        return f"{db + 0.0:.1f} dB".replace("-0.0", "0.0")


class PanParameter(Parameter):
    def str_for_value(self, v):
        steps = round(v * 50)
        return "C" if steps == 0 else f"{abs(steps)}{'L' if steps < 0 else 'R'}"


class FreqParameter(Parameter):
    """20 Hz .. 20 kHz on a log scale, like Live's filter cutoffs."""

    def str_for_value(self, v):
        hz = 20 * (1000 ** v)
        return f"{hz / 1000:.2f} kHz" if hz >= 1000 else f"{hz:.0f} Hz"


class ChoiceParameter(Parameter):
    def __init__(self, name, items):
        super().__init__(name, 0.0, 0.0, float(len(items) - 1))
        self.value_items, self.is_quantized = list(items), True

    def str_for_value(self, v):
        return self.value_items[int(round(v))]


class Mixer:
    def __init__(self, sends=2):
        self.volume = VolumeParameter("Track Volume", 0.85)
        self.panning = PanParameter("Track Panning", 0.0, -1.0, 1.0)
        self.sends = [VolumeParameter(f"Send {chr(65 + i)}", 0.0) for i in range(sends)]


class Device:
    INSTRUMENTS = {"Drift", "Wavetable", "Operator", "Simpler", "Drum Rack"}

    def __init__(self, name, class_name="OriginalSimpler"):
        self.name, self.class_name, self.class_display_name = name, class_name, name
        self.type = 1 if name in self.INSTRUMENTS else 2
        self.is_active, self.can_have_chains = True, False
        self.parameters = [ChoiceParameter("Device On", ["Off", "On"]), FreqParameter("Flt 1 Freq", 1.0),
                           Parameter("Flt 1 Res", 0.0, 0.0, 100.0, " %"), ChoiceParameter("Osc 1 Wave", ["Sine", "Saw", "Square"])]
        self.parameters[0].value = 1.0


class DrumPad:
    def __init__(self, note, name, filled):
        self.note, self.name = note, name
        self.chains = [object()] if filled else []


class DrumRack(Device):
    def __init__(self):
        super().__init__("Drum Rack", "DrumGroupDevice")
        names = {36: "Kick", 38: "Snare", 42: "Closed Hat"}
        self.drum_pads = [DrumPad(n, names.get(n, "Empty"), n in names) for n in range(128)]


class LinearParameter(Parameter):
    def __init__(self, name, value, lo, hi, fmt):
        super().__init__(name, value, lo, hi)
        self._fmt = fmt

    def str_for_value(self, v):
        return self._fmt.format(v)


class EqEight(Device):
    TYPES = ["High Pass 48dB", "High Pass 12dB", "Low Shelf", "Bell", "Notch", "High Shelf", "Low Pass 12dB", "Low Pass 48dB"]

    def __init__(self):
        super().__init__("EQ Eight", "Eq8")
        self.type = 2
        self.parameters = [ChoiceParameter("Device On", ["Off", "On"])]
        self.parameters[0].value = 1.0
        for n in range(1, 9):
            on = ChoiceParameter(f"{n} Filter On A", ["Off", "On"])
            on.value = 1.0  # like Live: a fresh EQ Eight has its bands switched on
            kind = ChoiceParameter(f"{n} Filter Type A", self.TYPES)
            kind.value = 3.0
            self.parameters += [on, kind, FreqParameter(f"{n} Frequency A", 0.5),
                                LinearParameter(f"{n} Gain A", 0.0, -15.0, 15.0, "{:.2f} dB"),
                                LinearParameter(f"{n} Q A", 0.71, 0.1, 18.0, "{:.2f}")]


class ReleaseParameter(LinearParameter):
    def str_for_value(self, v):
        return "A" if v >= self.max - 1e-9 else f"{v:g}"


def master_device(name):
    """Glue Compressor / Utility / Limiter with Live 12.4's parameter names and display formats."""
    dev = Device(name, name.replace(" ", ""))
    dev.type = 2
    on = ChoiceParameter("Device On", ["Off", "On"])
    on.value = 1.0
    if name == "Glue Compressor":
        dev.parameters = [on, LinearParameter("Threshold", 0.0, -40.0, 0.0, "{:.2f} dB"),
                          LinearParameter("Range", 70.0, 0.0, 70.0, "{:.1f} dB"),
                          LinearParameter("Output", 0.0, 0.0, 20.0, "{:.2f} dB"),
                          LinearParameter("Attack", 1.0, 0.01, 30.0, "{:g}"), LinearParameter("Ratio", 4.0, 2.0, 10.0, "{:g}"),
                          ReleaseParameter("Release", 0.6, 0.1, 1.3, "{:g}"),
                          LinearParameter("Dry/Wet", 100.0, 0.0, 100.0, "{:.0f} %")]
    elif name == "Utility":
        dev.parameters = [on, LinearParameter("Stereo Width", 100.0, 0.0, 400.0, "{:.0f} %"),
                          ChoiceParameter("Bass Mono", ["Off", "On"]), LinearParameter("Bass Freq", 120.0, 50.0, 500.0, "{:.0f} Hz"),
                          ChoiceParameter("DC Filter", ["Off", "On"])]
    else:  # Limiter
        mode = ChoiceParameter("Mode", ["Standard", "Soft Clip", "True Peak"])
        dev.parameters = [on, LinearParameter("Input Gain", 0.0, -24.0, 24.0, "{:.1f} dB"),
                          LinearParameter("Ceiling", -0.3, -24.0, 0.0, "{:.1f} dB"), ChoiceParameter("Auto", ["Off", "On"]),
                          ChoiceParameter("Lookahead", ["1.5 ms", "3 ms", "6 ms"]), mode,
                          ChoiceParameter("Maximize On", ["Off", "On"])]
    return dev


class Clip:
    _next_id = 1

    def __init__(self, length=4.0, midi=True, name=""):
        self.name, self.length, self.is_midi_clip = name, length, midi
        self.is_audio_clip = not midi
        self.is_playing = self.is_recording = self.muted = False
        self.looping, self.loop_start, self.loop_end = True, 0.0, length
        self.start_marker, self.end_marker = 0.0, length
        self.start_time, self.end_time = 0.0, length
        self.color = 0xFF0000
        self.notes: list[MidiNoteSpecification] = []
        self.envelopes: dict[int, Envelope] = {}

    def automation_envelope(self, param):
        return self.envelopes.get(id(param))

    def create_automation_envelope(self, param):
        return self.envelopes.setdefault(id(param), Envelope())

    def clear_envelope(self, param):
        self.envelopes.pop(id(param), None)

    def get_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        return [n for n in self.notes if from_pitch <= n.pitch < from_pitch + pitch_span
                and from_time <= n.start_time < from_time + time_span]

    def add_new_notes(self, specs):
        ids = []
        for s in specs:
            s.note_id = Clip._next_id
            Clip._next_id += 1
            self.notes.append(s)
            ids.append(s.note_id)
        return ids

    def remove_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        keep = set(map(id, self.get_notes_extended(from_pitch, pitch_span, from_time, time_span)))
        self.notes = [n for n in self.notes if id(n) not in keep]

    def remove_notes_by_id(self, ids):
        self.notes = [n for n in self.notes if n.note_id not in set(ids)]


class ClipSlot:
    def __init__(self):
        self.clip = None
        self.fired = False
        self.is_playing = False

    @property
    def has_clip(self):
        return self.clip is not None

    def create_clip(self, length):
        if self.clip is not None:
            raise RuntimeError("slot not empty")
        self.clip = Clip(length)

    def delete_clip(self):
        self.clip = None

    def fire(self):
        self.fired = True

    def duplicate_clip_to(self, target):
        src = self.clip
        target.clip = Clip(src.length, src.is_midi_clip, src.name)


class Track:
    def __init__(self, name, midi=True, scenes=4, sends=2):
        self.name, self.color = name, 0x3366FF
        self.has_midi_input, self.has_audio_input = midi, not midi
        self.is_foldable = self.is_grouped = False
        self.group_track = None
        self.mute = self.solo = self.arm = False
        self.fold_state = 0
        self.mixer_device = Mixer(sends)
        self.devices: list[Device] = []
        self.clip_slots = [ClipSlot() for _ in range(scenes)]
        self.arrangement_clips: list[Clip] = []
        self.output_meter_left = self.output_meter_right = 0.0

    def stop_all_clips(self):
        pass

    def insert_device(self, name, index=-1):
        known = Device.INSTRUMENTS | {"EQ Eight", "Auto Filter", "Glue Compressor", "Limiter", "Utility"}
        if name not in known:
            raise ValueError(f"Device {name} not available.")
        if name in Device.INSTRUMENTS and any(d.type == 1 for d in self.devices):
            raise RuntimeError(f"Can not insert device '{name}': Device chains cannot have more than one instrument each.")
        if name == "EQ Eight":
            dev = EqEight()
        elif name in ("Glue Compressor", "Utility", "Limiter"):
            dev = master_device(name)
        else:
            dev = Device(name, name.replace(" ", ""))
        self.devices.insert(index if index >= 0 else len(self.devices), dev)
        return dev

    def delete_device(self, index):
        del self.devices[index]

    def duplicate_clip_to_arrangement(self, clip, time):
        new = Clip(clip.length, clip.is_midi_clip, clip.name)
        new.notes = [MidiNoteSpecification(n.pitch, n.start_time, n.duration, n.velocity) for n in clip.notes]
        new.start_time, new.end_time = time, time + clip.length
        self.arrangement_clips.append(new)
        self.arrangement_clips.sort(key=lambda c: c.start_time)
        return new

    def delete_clip(self, clip):
        self.arrangement_clips.remove(clip)

    def create_midi_clip(self, start_time, length):
        clip = Clip(length)
        clip.start_time, clip.end_time = start_time, start_time + length
        self.arrangement_clips.append(clip)
        self.arrangement_clips.sort(key=lambda c: c.start_time)
        return clip


class ReturnTrack(Track):
    @property
    def arm(self):
        raise RuntimeError("Return tracks can't be armed")

    @arm.setter
    def arm(self, v):
        pass


class Scene:
    def __init__(self, name=""):
        self.name, self.color, self.tempo, self.tempo_enabled = name, 0, 120.0, False
        self.is_empty = True
        self.fired = False

    def fire(self):
        self.fired = True


class CuePoint:
    def __init__(self, name, time):
        self.name, self.time = name, time


class View:
    def __init__(self, song):
        self._song = song
        self._selected = None

    @property
    def selected_track(self):
        return self._selected or self._song.tracks[0]

    @selected_track.setter
    def selected_track(self, track):
        self._selected = track

    @property
    def selected_scene(self):
        return self._song.scenes[0]


class Song:
    def __init__(self):
        self.tempo, self.signature_numerator, self.signature_denominator = 120.0, 4, 4
        self.root_note, self.scale_name = 9, "Minor"
        self.scale_intervals = [0, 2, 3, 5, 7, 8, 10]
        self.is_playing, self.current_song_time, self.song_length = False, 0.0, 64.0
        self.loop, self.loop_start, self.loop_length = False, 0.0, 16.0
        self.start_time = 0.0
        self.metronome, self.record_mode, self.session_record = False, False, False
        self.scenes = [Scene(f"Scene {i + 1}") for i in range(4)]
        self.tracks = [Track("Drums"), Track("Bass"), Track("Vox", midi=False)]
        self.tracks[0].devices.append(DrumRack())
        self.tracks[1].clip_slots[0].clip = Clip(8.0, name="Bassline")
        self.tracks[1].arrangement_clips.append(Clip(16.0, name="Bass Arr"))
        self.tracks[1].arrangement_clips[0].start_time = 0.0
        self.tracks[1].arrangement_clips[0].end_time = 16.0
        self.return_tracks = [ReturnTrack("A-Reverb"), ReturnTrack("B-Delay")]
        self.master_track = Track("Main", midi=False)
        self.master_track.has_audio_input = False
        self.cue_points = [CuePoint("Intro", 0.0), CuePoint("Drop", 32.0)]
        self.view = View(self)
        self.undo_count = 0
        self.can_undo, self.can_redo = True, False

    def create_midi_track(self, index):
        self._insert(Track("MIDI", True, len(self.scenes)), index)

    def create_audio_track(self, index):
        self._insert(Track("Audio", False, len(self.scenes)), index)

    def _insert(self, track, index):
        if index < 0:
            self.tracks.append(track)
        else:
            self.tracks.insert(index, track)

    def create_return_track(self):
        self.return_tracks.append(ReturnTrack(f"{chr(65 + len(self.return_tracks))}-Return"))

    def delete_track(self, index):
        del self.tracks[index]

    def delete_return_track(self, index):
        del self.return_tracks[index]

    def duplicate_track(self, index):
        src = self.tracks[index]
        self.tracks.insert(index + 1, Track(src.name + " copy", src.has_midi_input, len(self.scenes)))

    def create_scene(self, index):
        scene = Scene()
        self.scenes.insert(index if index >= 0 else len(self.scenes), scene)
        for t in self.tracks:
            t.clip_slots.insert(index if index >= 0 else len(t.clip_slots), ClipSlot())

    def duplicate_scene(self, index):
        self.scenes.insert(index + 1, Scene(self.scenes[index].name))

    def delete_scene(self, index):
        del self.scenes[index]

    def start_playing(self):
        self.is_playing = True

    def stop_playing(self):
        self.is_playing = False

    def continue_playing(self):
        self.is_playing = True

    def stop_all_clips(self):
        pass

    def undo(self):
        self.undo_count += 1
        self.can_undo = False

    undo_depth = 0
    undo_groups = 0

    def begin_undo_step(self):
        self.undo_depth += 1

    def end_undo_step(self):
        self.undo_depth -= 1
        self.undo_groups += 1

    def is_cue_point_selected(self):
        return any(abs(c.time - self.current_song_time) < 1e-6 for c in self.cue_points)

    def set_or_delete_cue(self):
        here = [c for c in self.cue_points if abs(c.time - self.current_song_time) < 1e-6]
        if here:
            self.cue_points.remove(here[0])
        else:
            self.cue_points.append(CuePoint(str(len(self.cue_points) + 1), self.current_song_time))
            self.cue_points.sort(key=lambda c: c.time)


class BrowserItem:
    def __init__(self, name, children=(), loadable=True):
        self.name, self.children = name, list(children)
        self.is_folder = bool(children)
        self.is_loadable = loadable and not children
        self.uri = "query:" + name


class Browser:
    def __init__(self, song):
        self._song = song
        self.instruments = BrowserItem("Instruments", [BrowserItem("Drift"), BrowserItem("Wavetable")])
        self.audio_effects = BrowserItem("Audio Effects", [BrowserItem("EQ Eight"), BrowserItem("Reverb")])
        self.midi_effects = BrowserItem("MIDI Effects", [BrowserItem("Arpeggiator")])
        self.drums = BrowserItem("Drums", [BrowserItem("Drum Rack"), BrowserItem("909 Core Kit.adg")])
        self.sounds = BrowserItem("Sounds", [BrowserItem("Piano & Keys", [BrowserItem("Grand Piano.adg"),
                                                                           BrowserItem("Grand Piano Single Sample.adv")])])
        self.samples = BrowserItem("Samples", [BrowserItem("Grand Piano C3 f.aif")])
        for name in ("plugins", "max_for_live", "user_library", "packs", "clips"):
            setattr(self, name, BrowserItem(name, [BrowserItem("placeholder", loadable=False)]))
        self.loaded = []

    def load_item(self, item):
        self.loaded.append(item.name)
        self._song.view.selected_track.devices.append(Device(item.name.rsplit(".", 1)[0], "InstrumentGroupDevice"))


class Application:
    def __init__(self, song=None):
        self.browser = Browser(song) if song else None

    def get_major_version(self):
        return 12

    def get_minor_version(self):
        return 4

    def get_bugfix_version(self):
        return 6
