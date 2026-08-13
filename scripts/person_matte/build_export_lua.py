#!/usr/bin/env python3
"""Build the Lua source for a frame-accurate still-image export loop.

Why this exists: neither `timeline.grab_frames` (Color-page thumbnail cache)
nor an independent `ffmpeg` decode are reliable for per-frame accuracy —
grab_frames can silently return a stale cached thumbnail for every requested
frame in a session, and ffmpeg's decode order can drift from Resolve's own
presentation order on footage with irregular timestamps (e.g. some phone
recordings). See docs/guides/person-mask-matte-guide.md for how both were
diagnosed.

The one proven-reliable path is Project.ExportCurrentFrameAsStill after
Timeline.SetCurrentTimecode, run in a tight loop *inside Resolve's own Fusion
process* via the script_plugin `run_inline` (language="lua") action — a fresh
Python subprocess cannot reconnect to Resolve when external scripting isn't
available (e.g. the in-app bridge / non-Studio setups), but Lua run via
`fusion:GetResolve()` reuses the live connection Resolve already holds.

Usage:
    python3 build_export_lua.py --start 108000 --end 108164 --fps 30 \\
        --out-dir /path/to/durable/frames > export.lua

Then feed the stdout content as `source` to:
    script_plugin(action="run_inline", params={"language": "lua", "timeout": 280,
                                                "source": "<script contents>"})

The MCP call may itself time out around 60s while Resolve keeps exporting in
the background (RunScript is async) — poll the output directory / read
manifest.txt rather than trusting the tool call's own timeout.

Caveat: uses non-drop-frame timecode math (HH:MM:SS:FF via plain frame/fps
division). Confirm the project's start timecode uses ':' separators (not
';') before relying on this — drop-frame NTSC timecode needs different math.
"""
import argparse
import sys


LUA_TEMPLATE = """local resolve = fusion:GetResolve()
local pm = resolve:GetProjectManager()
local project = pm:GetCurrentProject()
local timeline = project:GetCurrentTimeline()

local frames = {{{frame_list}}}
local fps_nominal = {fps}
local out_dir = "{out_dir}/"
os.execute('mkdir -p "' .. out_dir .. '"')

local manifest = io.open(out_dir .. "manifest.txt", "w")
local fail_count = 0
for idx, f in ipairs(frames) do
    local i = idx - 1
    local total_seconds = math.floor(f / fps_nominal)
    local fr = f % fps_nominal
    local hours = math.floor(total_seconds / 3600)
    local rem = total_seconds % 3600
    local minutes = math.floor(rem / 60)
    local seconds = rem % 60
    local tc = string.format("%02d:%02d:%02d:%02d", hours, minutes, seconds, fr)
    local ok = timeline:SetCurrentTimecode(tc)
    local path = string.format(out_dir .. "frame_%04d.png", i)
    local exported = project:ExportCurrentFrameAsStill(path)
    if not ok or not exported then fail_count = fail_count + 1 end
    manifest:write(string.format("%d %d %s %s %s\\n", i, f, tc, tostring(ok), tostring(exported)))
end
manifest:close()
print("DONE total", #frames, "fails", fail_count)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=int, required=True, help="first absolute timeline frame (inclusive)")
    parser.add_argument("--end", type=int, required=True, help="last absolute timeline frame (inclusive)")
    parser.add_argument("--fps", type=int, default=30, help="nominal project fps rounded to an integer (e.g. 30 for 29.97, 24 for 23.976)")
    parser.add_argument("--out-dir", required=True, help="durable output directory (NOT a session-ephemeral scratch path — the Fusion Loader will reference these files after the session ends)")
    args = parser.parse_args()

    frames = list(range(args.start, args.end + 1))
    frame_list = ", ".join(str(f) for f in frames)
    print(LUA_TEMPLATE.format(frame_list=frame_list, fps=args.fps, out_dir=args.out_dir))
    print(f"# {len(frames)} frames, output to {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
