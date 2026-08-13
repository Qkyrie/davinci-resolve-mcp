# Person Mask / Matte Guide (ML Roto over MCP)

How to build a per-frame, ML-tracked person cutout on a timeline item's
Fusion comp — a free, local alternative to Studio's Magic Mask, built from
primitives the scripting API actually exposes. Helper scripts live in
`scripts/person_matte/`.

## Why not Magic Mask directly

Magic Mask's ML person-segmentation model isn't exposed through the
scripting API at all. What *is* exposed is the underlying Fusion node graph
Magic Mask itself writes to — a mask driving a `Merge`'s `EffectMask`. This
guide builds that graph by hand, using a locally-run open-source
segmentation model (`rembg`, ONNX-based) instead of Resolve's own model.

## Pipeline overview

1. **Export frames from Resolve, frame-accurately.** Use
   `scripts/person_matte/build_export_lua.py` to generate a Lua script, then
   run it via `script_plugin(action="run_inline", params={"language": "lua", ...})`.
   See "Frame export reliability" below for why this specific path is the
   only one that's actually reliable.
2. **Generate mattes.** `scripts/person_matte/setup_env.sh` once, then
   `generate_mattes.py <frames_dir> <mattes_dir>` (uses the `u2net_human_seg`
   rembg model, grayscale `only_mask` output).
3. **Store mattes somewhere durable**, not a session scratch directory —
   see "Durable storage" below.
4. **Wire the Fusion graph.** Two options:
   - **Plain mask only** — via `fusion_comp`: add a `Loader` pointed at
     `frame_0000.png` of the matte sequence, connect its `Output` to the
     target `Merge`'s `EffectMask`, and **set `MaskChannel` to `0`** — see
     "The MaskChannel gotcha" below, this is the step that's easy to skip
     and silently produces an unmasked passthrough.
   - **Full "text behind subject" reels composite** — use the
     `reels-occlusion` Fusion template (`resolve-advanced` `fusion` tool,
     `generate_from_template` / `to_api_calls`; see below) instead of
     hand-wiring the graph. It builds the whole background → text → masked
     subject stack in one call, including the `MaskChannel` fix already
     baked in.

## The `reels-occlusion` template

`resolve-advanced/vendor/fusion-codec/templates/reels-occlusion.js` builds
the full three-layer stack from a single source read: plain background
video, a `TextPlus` layer, and the masked subject cutout composited on top
so it occludes the text — the "text behind the person" look. Apply it via
the `fusion` tool (offline authoring server):

```
fusion(action="generate_from_template", params={
  templateName: "reels-occlusion",
  params: {
    maskClipPath: "/path/to/durable/mattes/frame_0000.png",  // required
    text: "like and subscribe",
    position: "center",       // top-left|top-center|top-right|bottom-left|bottom-center|bottom-right|center
    width: 1080, height: 1920 // defaults to vertical/reels
  }
})
```

or get the ordered `fusion_comp` calls directly via
`fusion(action="to_api_calls", params={spec: ...})` and replay them with
`safe_add_tool` / `safe_set_inputs` / `safe_connect_tools`.

Read the node structure in the template source before assuming it fits —
it composites the person over the SAME source clip's own plain read
(`MediaIn1` used twice), not over a second duplicated timeline clip. That's
deliberate: see "Duplicating the clip for a layered composite" below for
why a duplicated-track approach was abandoned.

## Frame export reliability

Two tempting shortcuts are both unreliable for this:

- **`timeline.grab_frames`** uses `GetCurrentClipThumbnailImage()` (a
  Color-page thumbnail cache). In practice this can return a **byte-identical
  cached thumbnail for every requested frame** within a session, regardless
  of the actual timeline position — verified by pixel-diffing outputs
  requested at three different comp positions and finding zero difference.
  Don't trust it for per-frame source data; it's fine for a quick one-off
  visual check.
- **An independent `ffmpeg` decode** of the source file can drift from
  Resolve's own presentation-frame order on footage with irregular
  timestamps (phone recordings especially — watch for ffmpeg's "non
  monotonically increasing dts" warnings). A decode that *looks* fine can
  still hand you frame N+2 when you asked for frame N.

The reliable path: `Project.ExportCurrentFrameAsStill()` after
`Timeline.SetCurrentTimecode()`, run **inside Resolve's own Fusion process**
via `script_plugin(action="run_inline", language="lua")` — not a fresh
Python subprocess, which can't reconnect to Resolve at all when external
scripting is unavailable (in-app bridge / non-Studio setups; `resolve` comes
back `None`). Lua reached via `fusion:GetResolve()` reuses the live
connection the app already holds.

`build_export_lua.py` generates this loop for a given absolute-frame range.
The MCP `run_inline` call itself may time out around 60s on a large frame
count — `RunScript` is async, so Resolve keeps exporting in the background.
Poll the output directory / read `manifest.txt` rather than trusting the
tool call's own timeout to mean failure.

## Durable storage

Point the Fusion Loader at a path that outlives the session — e.g. the
project's own analysis output root (matches where `grab_frames` and other
analysis tools already write:
`<analysis_root>/<project-slug>/person-mask-mattes/`), never a
`/private/tmp/...` scratch path. A scratch-path Loader breaks the mask the
next time the project opens.

## The MaskChannel gotcha

Connecting a plain `Loader` (RegID `Loader`, outputs type `Image`) directly
to a `Merge`'s `EffectMask` (type `Mask`) **succeeds with no error** — the
low-level scripting `connect` call just wires the pointer, it doesn't
replicate whatever adapter Fusion's UI silently inserts on a drag-and-drop
connection of the same kind. The connection *looks* fine on inspection
(`get_inputs` shows `EffectMask` connected to your Loader) and the graph
renders — but the whole clip stays fully visible, uncropped, because
`Merge.MaskChannel` defaults to reading a channel (observed default: `3`)
that's constant-opaque for a plain 8-bit grayscale PNG. Set it explicitly:

```
fusion_comp(action="safe_set_inputs", params={
    ..., "tool_name": "<merge-tool-name>",
    "inputs": {"MaskChannel": 0}   # 0 = luminance
})
```

Also don't reach for a `MatteControl` tool as an "Image → Mask converter" —
it's a compositor (Background/Foreground image inputs, `Image` output), not
a type adapter, and won't help here.

## Duplicating the clip for a layered composite (bg / text / person)

For a "person occludes text, text sits over background" reels-style effect,
the natural approach is two timeline tracks. In practice, **`duplicate_clips`
and `copy_clips` can both mis-derive the duplicate's duration** on footage
with irregular source timing — observed on one clip: original edit 165
timeline frames, both duplication actions produced 183 frames for an
identical source range (≈ `165 × timeline_fps/source_fps`, suggesting the
frame-rate conversion gets applied twice under some conform paths). A
duration mismatch between two supposedly-stacked copies means they drift out
of sync over the clip's length — the mask on one layer stops lining up with
the video on the other.

If you hit this, the robust fallback is compositing **within a single
Fusion comp** instead of duplicating the timeline item: read the source once
(`MediaIn1`), build the masked cutout as before, then `Merge` it back over a
second, unmasked read of the same `MediaIn1` as the background layer. This
is frame-accurate by construction (one shared comp-time domain) and leaves
room for a middle `Merge` layer (e.g. `TextPlus`) between the background and
the person cutout. Less flexible for independent per-layer track edits
later, but immune to the duplication bug. Worth revisiting whether
`duplicate_clips`/`copy_clips` can be fixed for VFR-ish sources; a dry-run
duration check against the source item before trusting the result is a
reasonable guard either way.

## Isolated Python environment

`rembg` pulls in `numpy`/`pillow`/`scipy` at versions that can conflict with
whatever's already installed in a shared interpreter (observed: bumping
these in a system Anaconda env broke pins for unrelated packages like
`streamlit`, `gensim`, `contourpy`). `setup_env.sh` creates a throwaway venv
under `scripts/person_matte/.venv` instead — always invoke
`generate_mattes.py` through that venv's `bin/python`, not whatever
`python3`/`pip3` happen to resolve to on the host (which can themselves be
two different interpreters, as observed on macOS with both an Anaconda
install and a separate venv on `PATH`).
