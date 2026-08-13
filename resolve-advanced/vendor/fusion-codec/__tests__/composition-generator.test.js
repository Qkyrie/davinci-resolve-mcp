/**
 * Coverage for two things found while adding the reels-occlusion template:
 *
 * 1. isConnectionRef bugfix — a literal string Input value that contains a
 *    dot (a file path, most commonly) was previously misidentified as a
 *    "ToolName.OutputName" connection reference by both serializeInput
 *    (the .comp text path) and specToApiCalls (the live-apply path), which
 *    silently dropped the value entirely on the live-apply path. No prior
 *    template happened to pass such a value, so this went undetected.
 * 2. All built-in templates, including the new one, still generate without
 *    throwing and list correctly.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const gen = require('../composition-generator');

test('literal string input containing a dot is preserved as a Value, not treated as a connection', () => {
  const spec = {
    nodes: [
      { type: 'MediaIn', name: 'MediaIn1', inputs: {} },
      {
        type: 'Loader',
        name: 'MatteLoader',
        inputs: { Clip: '/Users/qds/analysis/mattes/frame_0000.png' },
      },
      {
        type: 'MediaOut',
        name: 'MediaOut1',
        inputs: {},
        connections: { Input: 'MatteLoader.Output' },
      },
    ],
  };

  const compText = gen.generateComp(spec);
  assert.match(compText, /Clip = Input \{ Value = "\/Users\/qds\/analysis\/mattes\/frame_0000\.png" \}/);
  assert.doesNotMatch(compText, /Clip = Input \{ SourceOp/);

  const calls = gen.specToApiCalls(spec);
  const clipCall = calls.find(
    (c) => c.action === 'set_input' && c.params.tool_name === 'MatteLoader' && c.params.input_name === 'Clip'
  );
  assert.ok(clipCall, 'set_input for Clip must be emitted, not silently dropped');
  assert.equal(clipCall.params.value, '/Users/qds/analysis/mattes/frame_0000.png');

  // Real connection refs (bare "ToolName.OutputName", no path separators)
  // must still be recognized and routed through connect, not set_input.
  const connectCall = calls.find((c) => c.action === 'connect' && c.params.target_tool === 'MediaOut1');
  assert.ok(connectCall);
  assert.equal(connectCall.params.source_tool, 'MatteLoader');
  assert.equal(connectCall.params.output_name, 'Output');
});

test('all built-in templates list and generate without throwing', () => {
  const templates = gen.listTemplates();
  const names = templates.map((t) => t.name);
  assert.ok(names.includes('reels-occlusion'));

  for (const name of names) {
    const params = name === 'reels-occlusion' ? { maskClipPath: '/some/path/frame_0000.png' } : {};
    assert.doesNotThrow(() => gen.generateFromTemplate(name, params), `${name} should generate cleanly`);
  }
});

test('reels-occlusion requires maskClipPath', () => {
  assert.throws(() => gen.generateFromTemplate('reels-occlusion', {}), /maskClipPath/);
});

test('reels-occlusion wires MaskChannel=0 on the subject-cutout merge', () => {
  const result = gen.generateFromTemplate('reels-occlusion', {
    maskClipPath: '/some/path/frame_0000.png',
  });
  const calls = gen.specToApiCalls(
    require('../templates/reels-occlusion').generate({ maskClipPath: '/some/path/frame_0000.png' })
  );
  const maskChannelCall = calls.find(
    (c) => c.action === 'set_input' && c.params.tool_name === 'SubjectCutout' && c.params.input_name === 'MaskChannel'
  );
  assert.ok(maskChannelCall, 'MaskChannel must be explicitly set — a plain image Loader on EffectMask '
    + 'otherwise defaults to a constant-opaque channel and silently produces an unmasked passthrough');
  assert.equal(maskChannelCall.params.value, 0);
  assert.ok(result.nodeCount > 0);
});
