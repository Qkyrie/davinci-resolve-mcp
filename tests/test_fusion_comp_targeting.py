"""Regression tests for fusion_comp timeline targeting helpers."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import server


class FakeFusion:
    def __init__(self, comp):
        self._comp = comp

    def GetCurrentComp(self):
        return self._comp


class FakeResolve:
    def __init__(self, comp):
        self._fusion = FakeFusion(comp)

    def Fusion(self):
        return self._fusion


class FakeTimelineItem:
    def __init__(self, unique_id, comp_count=1):
        self._unique_id = unique_id
        self._comp_count = comp_count
        self.requested_comp_index = None

    def GetUniqueId(self):
        return self._unique_id

    def GetFusionCompCount(self):
        return self._comp_count

    def GetFusionCompByIndex(self, comp_index):
        self.requested_comp_index = comp_index
        return {"comp_index": comp_index}

    def GetFusionCompByName(self, comp_name):
        return {"comp_name": comp_name}


class FakeTimeline:
    def __init__(self, tracks):
        self._tracks = tracks

    def GetTrackCount(self, track_type):
        return len(self._tracks.get(track_type, {}))

    def GetItemListInTrack(self, track_type, track_index):
        return self._tracks.get(track_type, {}).get(track_index, [])


class FakeSplineTool:
    """The BezierSpline modifier tool, where keyframe deletion actually lives.

    Measured live on 21.0.3.7: `DeleteKeyFrames(time)` removes exactly the key
    at that time and silently no-ops (returns None) when there is none.
    """

    def __init__(self, inp):
        self._inp = inp
        self.deleted = []

    def DeleteKeyFrames(self, time):
        self.deleted.append(time)
        self._inp.keyframe_values.pop(float(time), None)
        return None


class FakeSplineOutput:
    """What `Input.GetConnectedOutput()` returns once a modifier is attached."""

    def __init__(self, spline_tool):
        self._spline_tool = spline_tool

    def GetTool(self):
        return self._spline_tool


class FakeFusionInput:
    """Minimal stand-in for a Fusion Input object.

    `inp[time] = value` records a keyframe only when a spline modifier is
    attached; in real Fusion it sets a STATIC value otherwise.
    """

    def __init__(self, connected_output=None, keyframe_values=None, static_value=1.0):
        self._connected_output = connected_output
        self.assignments = {}
        # frame_position -> value, modelling existing keyframes on the input.
        self.keyframe_values = dict(keyframe_values or {})
        self.static_value = static_value

    def __bool__(self):
        return True

    def GetConnectedOutput(self):
        return self._connected_output

    def __setitem__(self, time, value):
        self.assignments[time] = value
        if self._connected_output is not None:
            self.keyframe_values[float(time)] = value

    def GetKeyFrames(self):
        # Mirror Fusion: {1-based index: frame_position}, sorted by frame.
        frames = sorted(self.keyframe_values)
        return {i + 1: frame for i, frame in enumerate(frames)} or None


class FakeFusionTool:
    #: Comp CurrentTime, where Fusion seeds a keyframe when a static input is
    #: first converted to a spline (measured live on 21.0.3.7).
    seed_time = 95.0

    def __init__(self, inputs):
        self._inputs = inputs
        self.modifiers_added = []

    def __getitem__(self, name):
        return self._inputs.get(name)

    def GetInput(self, name, frame):
        inp = self._inputs.get(name)
        return inp.keyframe_values.get(frame) if inp is not None else None

    def AddModifier(self, input_name, modifier_type):
        self.modifiers_added.append((input_name, modifier_type))
        # Mirror Fusion: once a modifier is attached the input is connected to
        # the spline tool's output, and the fresh spline is seeded with a key
        # at the comp's CurrentTime carrying the old static value.
        inp = self._inputs.get(input_name)
        if inp is not None:
            inp._connected_output = FakeSplineOutput(FakeSplineTool(inp))
            inp.keyframe_values[float(self.seed_time)] = inp.static_value
        return True


class FakeFusionComp:
    def __init__(self, tools):
        self._tools = tools
        self.lock_count = 0
        self.unlock_count = 0

    def FindTool(self, name):
        return self._tools.get(name)

    def Lock(self):
        self.lock_count += 1

    def Unlock(self):
        self.unlock_count += 1


class FusionAddKeyframeTests(unittest.TestCase):
    def _run(self, comp, params):
        with patch.object(server, "_resolve_fusion_comp", return_value=(comp, None)):
            return server.fusion_comp("add_keyframe", params)

    def test_attaches_bezierspline_on_virgin_input(self):
        inp = FakeFusionInput(connected_output=None)
        tool = FakeFusionTool({"Size": inp})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Size", "time": 0, "value": 1.0,
        })

        self.assertTrue(result.get("success"))
        self.assertEqual(tool.modifiers_added, [("Size", "BezierSpline")])
        self.assertEqual(inp.assignments, {0: 1.0})
        self.assertEqual((comp.lock_count, comp.unlock_count), (1, 1))

    def test_skips_modifier_when_already_animated(self):
        inp = FakeFusionInput(connected_output=object())
        tool = FakeFusionTool({"Size": inp})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Size", "time": 75, "value": 1.4,
        })

        self.assertTrue(result.get("success"))
        self.assertEqual(tool.modifiers_added, [])
        self.assertEqual(inp.assignments, {75: 1.4})

    def test_honors_custom_modifier_param(self):
        inp = FakeFusionInput(connected_output=None)
        tool = FakeFusionTool({"Center": inp})
        comp = FakeFusionComp({"Transform1": tool})

        self._run(comp, {
            "tool_name": "Transform1", "input_name": "Center",
            "time": 0, "value": [0.5, 0.5], "modifier": "Path",
        })

        self.assertEqual(tool.modifiers_added, [("Center", "Path")])

    def test_missing_input_returns_error_and_unlocks(self):
        tool = FakeFusionTool({})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Nope", "time": 0, "value": 1.0,
        })

        self.assertIn("error", result)
        self.assertEqual(tool.modifiers_added, [])
        # comp must be unlocked even on the error path.
        self.assertEqual((comp.lock_count, comp.unlock_count), (1, 1))

    def test_removes_the_seed_keyframe_fusion_adds_at_current_time(self):
        # Converting a static input to a BezierSpline seeds a stray key at the
        # comp's CurrentTime holding the old static value (measured live on
        # 21.0.3.7). Nothing asked for that key; add_keyframe must remove it.
        inp = FakeFusionInput(connected_output=None, static_value=1.0)
        tool = FakeFusionTool({"Size": inp})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Size", "time": 10, "value": 2.0,
        })

        self.assertTrue(result.get("success"))
        self.assertEqual(inp.keyframe_values, {10.0: 2.0})
        self.assertEqual(inp._connected_output.GetTool().deleted, [95.0])

    def test_seed_keyframe_at_the_requested_time_is_kept(self):
        inp = FakeFusionInput(connected_output=None, static_value=1.0)
        tool = FakeFusionTool({"Size": inp})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Size",
            "time": FakeFusionTool.seed_time, "value": 2.0,
        })

        self.assertTrue(result.get("success"))
        self.assertEqual(inp.keyframe_values, {95.0: 2.0})
        self.assertEqual(inp._connected_output.GetTool().deleted, [])

    def test_existing_keyframes_are_never_swept_on_an_animated_input(self):
        spline_holder = FakeFusionInput(keyframe_values={0.0: 1.0})
        spline_holder._connected_output = FakeSplineOutput(FakeSplineTool(spline_holder))
        tool = FakeFusionTool({"Size": spline_holder})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Size", "time": 50, "value": 2.0,
        })

        self.assertTrue(result.get("success"))
        self.assertEqual(spline_holder.keyframe_values, {0.0: 1.0, 50.0: 2.0})
        self.assertEqual(spline_holder._connected_output.GetTool().deleted, [])

    def test_custom_modifier_skips_seed_cleanup(self):
        # Seed behaviour is only measured for BezierSpline; a custom modifier
        # (e.g. Path) keeps whatever keys Fusion created.
        inp = FakeFusionInput(connected_output=None, static_value=1.0)
        tool = FakeFusionTool({"Center": inp})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Center",
            "time": 10, "value": [0.5, 0.5], "modifier": "Path",
        })

        self.assertTrue(result.get("success"))
        self.assertEqual(inp._connected_output.GetTool().deleted, [])
        self.assertIn(95.0, inp.keyframe_values)


class FusionDeleteKeyframeTests(unittest.TestCase):
    def _run(self, comp, params):
        with patch.object(server, "_resolve_fusion_comp", return_value=(comp, None)):
            return server.fusion_comp("delete_keyframe", params)

    def _animated_input(self, keyframe_values):
        inp = FakeFusionInput(keyframe_values=keyframe_values)
        inp._connected_output = FakeSplineOutput(FakeSplineTool(inp))
        return inp

    def test_deletes_via_the_spline_tool_not_the_input(self):
        # Fusion Inputs have no RemoveKeyFrame (measured live on 21.0.3.7);
        # deletion goes GetConnectedOutput() -> GetTool() -> DeleteKeyFrames.
        # The fake input has no RemoveKeyFrame either, so calling it would blow
        # up this test rather than pass silently.
        inp = self._animated_input({0.0: 1.0, 95.0: 1.2, 129.0: 1.3})
        tool = FakeFusionTool({"Size": inp})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Size", "time": 95,
        })

        self.assertTrue(result.get("success"))
        self.assertEqual(inp.keyframe_values, {0.0: 1.0, 129.0: 1.3})
        self.assertEqual(inp._connected_output.GetTool().deleted, [95])
        self.assertEqual((comp.lock_count, comp.unlock_count), (1, 1))

    def test_static_input_is_a_clear_error(self):
        inp = FakeFusionInput(connected_output=None)
        tool = FakeFusionTool({"Size": inp})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Size", "time": 10,
        })

        self.assertIn("error", result)
        self.assertIn("not animated", result["error"]["message"])
        self.assertEqual((comp.lock_count, comp.unlock_count), (1, 1))

    def test_missing_keyframe_is_an_error_not_a_silent_noop(self):
        # DeleteKeyFrames on a key-less time silently no-ops in Fusion, so the
        # handler must check existence itself instead of reporting success.
        inp = self._animated_input({0.0: 1.0})
        tool = FakeFusionTool({"Size": inp})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Size", "time": 50,
        })

        self.assertIn("error", result)
        self.assertIn("50", result["error"]["message"])
        self.assertEqual(inp._connected_output.GetTool().deleted, [])

    def test_a_deletion_fusion_ignored_is_reported_not_swallowed(self):
        class StubbornSpline(FakeSplineTool):
            def DeleteKeyFrames(self, time):
                self.deleted.append(time)
                return None  # pretends, but removes nothing

        inp = FakeFusionInput(keyframe_values={10.0: 2.0})
        inp._connected_output = FakeSplineOutput(StubbornSpline(inp))
        tool = FakeFusionTool({"Size": inp})
        comp = FakeFusionComp({"Transform1": tool})

        result = self._run(comp, {
            "tool_name": "Transform1", "input_name": "Size", "time": 10,
        })

        self.assertIn("error", result)
        self.assertEqual(inp._connected_output.GetTool().deleted, [10])


class FusionGetKeyframesTests(unittest.TestCase):
    def test_returns_frame_positions_and_values(self):
        # GetKeyFrames yields {index: frame}; the handler must report the frame
        # position as `time` and the GetInput(frame) result as `value`.
        inp = FakeFusionInput(
            connected_output=object(),
            keyframe_values={0.0: 1.0, 75.0: 1.4},
        )
        tool = FakeFusionTool({"Size": inp})
        comp = FakeFusionComp({"Transform1": tool})

        with patch.object(server, "_resolve_fusion_comp", return_value=(comp, None)):
            result = server.fusion_comp(
                "get_keyframes", {"tool_name": "Transform1", "input_name": "Size"}
            )

        self.assertEqual(
            result["keyframes"],
            [{"time": 0.0, "value": 1.0}, {"time": 75.0, "value": 1.4}],
        )

    def test_no_keyframes_returns_empty_list(self):
        inp = FakeFusionInput(connected_output=None, keyframe_values={})
        tool = FakeFusionTool({"Size": inp})
        comp = FakeFusionComp({"Transform1": tool})

        with patch.object(server, "_resolve_fusion_comp", return_value=(comp, None)):
            result = server.fusion_comp(
                "get_keyframes", {"tool_name": "Transform1", "input_name": "Size"}
            )

        self.assertEqual(result["keyframes"], [])


class FusionCompTargetingTests(unittest.TestCase):
    def test_active_comp_fallback_does_not_require_timeline(self):
        active_comp = object()

        with patch.object(server, "get_resolve", return_value=FakeResolve(active_comp)), patch.object(
            server,
            "_get_tl",
            side_effect=AssertionError("_get_tl should not be called without timeline scope"),
        ):
            comp, err = server._resolve_fusion_comp({})

        self.assertIs(comp, active_comp)
        self.assertIsNone(err)

    def test_bulk_set_inputs_requires_timeline_scope_per_op(self):
        with patch.object(
            server,
            "_resolve_fusion_comp",
            side_effect=AssertionError("_resolve_fusion_comp should not be called for unscoped bulk ops"),
        ):
            result = server._fusion_comp_bulk_set_inputs(
                {"ops": [{"tool_name": "Text1", "input_name": "StyledText", "value": "Hello"}]}
            )

        self.assertEqual(result["op_count"], 1)
        self.assertIn("timeline scope is required", result["results"][0]["error"])

    def test_find_timeline_item_by_id_scans_timeline_tracks(self):
        wanted = FakeTimelineItem("target")
        timeline = FakeTimeline({
            "video": {1: [FakeTimelineItem("video-1")]},
            "audio": {1: [wanted]},
        })

        self.assertIs(server._find_timeline_item_by_id(timeline, "target"), wanted)

    def test_comp_index_defaults_to_first_comp_and_validates_range(self):
        item = FakeTimelineItem("clip-1", comp_count=2)

        comp, err = server._get_fusion_comp_on_timeline_item(item, {})
        self.assertEqual(comp, {"comp_index": 1})
        self.assertIsNone(err)

        comp, err = server._get_fusion_comp_on_timeline_item(item, {"comp_index": 3})
        self.assertIsNone(comp)
        self.assertIn("item has 2 comp(s)", (err["error"].get("message","") if isinstance(err["error"], dict) else err["error"]))


if __name__ == "__main__":
    unittest.main()
