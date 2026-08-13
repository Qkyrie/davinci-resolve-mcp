/**
 * Reels Occlusion Template — text sandwiched between background and a masked subject
 *
 * The "text behind the person" look common in short-form video: the plain
 * source video on the bottom, a text layer in the middle, and an ML-masked
 * cutout of the subject on top so they visually occlude the text.
 *
 * Requires a pre-built per-frame subject matte sequence (grayscale, white =
 * subject) — see scripts/person_matte/ and docs/guides/person-mask-matte-guide.md
 * for how to generate one from a live timeline item. This template only
 * assembles the compositing graph; it does not generate the matte itself.
 *
 * Reads the source clip (MediaIn1) once and derives both the plain
 * background pass and the masked subject cutout from it, so the two layers
 * can never drift out of sync with each other — unlike stacking two
 * separately-duplicated timeline clips, which was observed to mismatch
 * duration on footage with irregular source timing (see the guide's
 * "Duplicating the clip for a layered composite" section).
 */

module.exports = {
  label: 'Reels Occlusion (subject over text over background)',
  description: 'Composites background video -> text -> ML-masked subject cutout, from a single source read.',
  parameters: {
    maskClipPath: { type: 'string', default: '', description: 'REQUIRED: path to frame_0000.png of the subject matte sequence (a Fusion Loader auto-detects the numbered siblings).' },
    text: { type: 'string', default: 'YOUR TEXT HERE', description: 'Text to display' },
    font: { type: 'string', default: 'Arial', description: 'Font family' },
    fontSize: { type: 'number', default: 0.08, description: 'Font size (0-1)' },
    position: { type: 'string', default: 'center', description: 'Preset: top-left, top-center, top-right, bottom-left, bottom-center, bottom-right, center' },
    textColor: { type: 'object', default: { r: 1, g: 1, b: 1 }, description: 'Text color RGB' },
    textOpacity: { type: 'number', default: 1, description: 'Text layer opacity (0-1)' },
    width: { type: 'number', default: 1080, description: 'Frame width (default: vertical/reels)' },
    height: { type: 'number', default: 1920, description: 'Frame height (default: vertical/reels)' },
  },

  generate(params = {}) {
    const {
      maskClipPath = '',
      text = 'YOUR TEXT HERE',
      font = 'Arial',
      fontSize = 0.08,
      position = 'center',
      textColor = { r: 1, g: 1, b: 1 },
      textOpacity = 1,
      width = 1080,
      height = 1920,
    } = params;

    if (!maskClipPath) {
      throw new Error(
        'reels-occlusion requires maskClipPath (path to frame_0000.png of a subject matte '
        + 'sequence). Generate one first — see docs/guides/person-mask-matte-guide.md.'
      );
    }

    const positionMap = {
      'top-left':      { x: 0.08, y: 0.92, hJust: 0 },
      'top-center':    { x: 0.5,  y: 0.92, hJust: 1 },
      'top-right':     { x: 0.92, y: 0.92, hJust: 2 },
      'bottom-left':   { x: 0.08, y: 0.08, hJust: 0 },
      'bottom-center': { x: 0.5,  y: 0.08, hJust: 1 },
      'bottom-right':  { x: 0.92, y: 0.08, hJust: 2 },
      'center':        { x: 0.5,  y: 0.5,  hJust: 1 },
    };
    const pos = positionMap[position] || positionMap['center'];

    const nodes = [
      {
        type: 'MediaIn',
        name: 'MediaIn1',
        inputs: {},
        viewX: 0, viewY: 0,
      },

      // Subject cutout: transparent background + full source, cropped to the
      // subject via the matte sequence. MaskChannel must be 0 (luminance) —
      // a plain image Loader connected to EffectMask defaults to a channel
      // that reads constant-opaque on an 8-bit grayscale PNG, silently
      // producing an unmasked passthrough. See the guide's "MaskChannel
      // gotcha" section.
      {
        type: 'Background',
        name: 'TransparentBG',
        inputs: {
          TopLeftAlpha: 0,
          Width: width,
          Height: height,
          UseFrameFormatSettings: 0,
        },
        viewX: 110, viewY: 66,
      },
      {
        type: 'Loader',
        name: 'SubjectMatteLoader',
        inputs: {
          Clip: maskClipPath,
        },
        viewX: 110, viewY: 132,
      },
      {
        type: 'Merge',
        name: 'SubjectCutout',
        inputs: {
          MaskChannel: 0,
        },
        connections: {
          Background: 'TransparentBG.Output',
          Foreground: 'MediaIn1.Output',
        },
        effectMask: 'SubjectMatteLoader.Output',
        viewX: 220, viewY: 66,
      },

      // Text layer, composited directly over the plain (unmasked) source —
      // this is the layer the subject cutout will occlude.
      {
        type: 'TextPlus',
        name: 'Text_Overlay',
        inputs: {
          StyledText: text,
          Font: font,
          Style: 'Bold',
          Size: fontSize,
          Center: { x: pos.x, y: pos.y },
          Red1: textColor.r,
          Green1: textColor.g,
          Blue1: textColor.b,
          HorizontalJustificationNew: pos.hJust,
          VerticalJustificationNew: 1,
          Width: width,
          Height: height,
        },
        viewX: 110, viewY: 0,
      },
      {
        type: 'Merge',
        name: 'Merge_Text',
        inputs: {
          Blend: textOpacity,
        },
        connections: {
          Background: 'MediaIn1.Output',
          Foreground: 'Text_Overlay.Output',
        },
        viewX: 220, viewY: 0,
      },

      // Final stack: background+text, subject cutout on top.
      {
        type: 'Merge',
        name: 'Merge_Final',
        inputs: {},
        connections: {
          Background: 'Merge_Text.Output',
          Foreground: 'SubjectCutout.Output',
        },
        viewX: 330, viewY: 0,
      },
    ];

    nodes.push({
      type: 'MediaOut',
      name: 'MediaOut1',
      inputs: {},
      connections: {
        Input: 'Merge_Final.Output',
      },
      viewX: 440, viewY: 0,
    });

    return { nodes, keyframes: [] };
  },
};
