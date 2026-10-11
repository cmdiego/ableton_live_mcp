#!/usr/bin/env bash
# Install the AbletonMCP Remote Script into Live's User Library.
#   scripts/install_remote_script.sh          symlink (edits apply after `reload` or reselecting the surface)
#   scripts/install_remote_script.sh --copy   copy instead, if Live ignores the symlink
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
src="$repo/remote_script/AbletonMCP"
dest_dir="$HOME/Music/Ableton/User Library/Remote Scripts"
dest="$dest_dir/AbletonMCP"

mkdir -p "$dest_dir"
if [ -e "$dest" ] || [ -L "$dest" ]; then
  echo "Replacing existing $dest"
  rm -rf "$dest"
fi

if [ "${1:-}" = "--copy" ]; then
  cp -R "$src" "$dest"
  echo "Copied $src -> $dest"
else
  ln -s "$src" "$dest"
  echo "Linked $dest -> $src"
fi

# Max for Live analyzer (Live Suite): capture + meter on the master track.
m4l_dir="$HOME/Music/Ableton/User Library/Presets/Audio Effects/Max Audio Effect"
if [ -f "$repo/m4l/AbletonMCP Analyzer.amxd" ]; then
  mkdir -p "$m4l_dir"
  cp "$repo/m4l/AbletonMCP Analyzer.amxd" "$m4l_dir/"
  echo "Copied AbletonMCP Analyzer.amxd -> $m4l_dir"
fi

cat <<EOF

Next:
  1. Restart Ableton Live (it scans Remote Scripts at startup).
  2. Settings > Link, Tempo & MIDI > Control Surface: choose "AbletonMCP" (Input/Output: None).
  3. Live's status bar should say "AbletonMCP ... listening on port 9877".
  4. Optional (Suite): ask Claude to run analyzer(action='install') to put the analyzer on the master.
EOF
