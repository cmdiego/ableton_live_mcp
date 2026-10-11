"""Phase 6: analyze audio files (exports, references, clip sources)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from .. import resolve
from ..app import tool
from ..audio import report as rep
from ..audio.compare import compare
from ..live_client import LiveError, get_client
from ..util import compact
from .. import workspace


def _clip_file(track, slot, arrangement_clip) -> Path:
    client = get_client()
    path = resolve.any_clip_path(client, track, slot, arrangement_clip)
    info = client.get(path, "is_audio_clip", "file_path", "name")
    if not info["is_audio_clip"]:
        raise LiveError(f"{path} is a MIDI clip; read_clip reads MIDI")
    if not info["file_path"]:
        raise LiveError(f"Live reports no file for clip {info['name']!r}")
    return Path(info["file_path"])


@tool(read_only=True)
def list_audio(folder: Literal["exports", "references", "captures", "all"] = "exports",
               contains: str | None = None) -> dict:
    """Audio files in the agent folder, newest first: exports (your mixes and stems),
    references (tracks to compare against), captures (recorded by the analyzer device).
    Export from Live into agent/exports/ (File > Export Audio/Video; WAV, no normalization)."""
    folders = ["exports", "references", "captures"] if folder == "all" else [folder]
    files = rep.list_files(folders, contains)
    return {"agent_folder": str(workspace.agent_dir()), "files": files[:100], "count": len(files)}


@tool(read_only=True)
def analyze_audio(path: str | None = None, track: str | int | None = None, slot: int | None = None,
                  arrangement_clip: int | None = None, reference: str | None = None,
                  detail: Literal["summary", "full"] = "summary", threshold_db: float = 1.5) -> dict:
    """Measure an audio file: loudness (integrated/short-term LUFS, LRA), true peak, PLR, RMS,
    clipping, DC offset, tonal balance (bands relative to the whole mix, tilt, centroid), stereo
    (correlation, width, mono compatibility), silence, a loudness curve, streaming playback gain,
    and technical issues. A JSON report goes to agent/reports/.

    path: 'latest' (default: newest in agent/exports), a name in agent/, or an absolute path; or
    track + slot/arrangement_clip for an audio clip's source file (clip gain/warp/effects not applied).
    reference: compare against a reference track instead (agent/references name or path): gain to
    match loudness, dynamics, tonal differences per band and 1/3 octave (centered, so +3 dB = 3 dB
    more than the typical band difference), tilt, width, plus plain-language findings above
    threshold_db. detail='full' adds 1/3-octave bands and a 1 s loudness curve."""
    file = _clip_file(track, slot, arrangement_clip) if track is not None else rep.resolve_path(path)
    report = rep.analyze_file(file)
    if reference:
        ref = rep.analyze_file(rep.resolve_path(reference))
        result = compare(report, ref, threshold_db)
        out = workspace.folder("reports") / f"{Path(report['file']).stem}__vs__{Path(ref['file']).stem}.json"
        out.write_text(json.dumps(result, indent=1))
        result["report_file"] = str(out.relative_to(workspace.agent_dir()))
        return compact(result)
    return compact(report if detail == "full" else rep.summary(report))
