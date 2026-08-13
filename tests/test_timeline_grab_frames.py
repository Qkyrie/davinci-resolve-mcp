"""timeline grab_frames — agent eyes for the current timeline.

One call parks the playhead on each requested frame, grabs the graded frame
(Color-page thumbnail first, Project.ExportCurrentFrameAsStill fallback — the
gallery ExportStills path is banned by its api_truth entry), and writes small
vision-ready PNGs plus an optional before/after diff pair.
"""
import base64
import os
import struct
import tempfile
import unittest
import zlib
from unittest import mock

import src.server as s


def _png_from_rows(width, height, rows, color_type=2, bit_depth=8, interlace=0):
    """Build a PNG from pre-filtered scanlines (each row: filter byte + data)."""
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, interlace)
    body = zlib.compress(b"".join(rows))
    return (
        b"\x89PNG\r\n\x1a\n"
        + s._png_chunk(b"IHDR", ihdr)
        + s._png_chunk(b"IDAT", body)
        + s._png_chunk(b"IEND", b"")
    )


class PngDecodeTest(unittest.TestCase):
    def test_roundtrip_of_own_encoder(self):
        raw = bytes(range(2 * 2 * 3))
        png = s._rgb_to_png_bytes(2, 2, raw)
        self.assertEqual(s._png_decode_rgb(png), (2, 2, raw))

    def test_decodes_sub_filtered_row(self):
        # Row of two RGB pixels: (10,20,30), (11,22,33); Sub stores deltas.
        row = bytes([1, 10, 20, 30, 1, 2, 3])
        png = _png_from_rows(2, 1, [row])
        self.assertEqual(s._png_decode_rgb(png), (2, 1, bytes([10, 20, 30, 11, 22, 33])))

    def test_decodes_up_filtered_row(self):
        rows = [
            bytes([0, 10, 20, 30]),
            bytes([2, 5, 5, 5]),  # Up: adds the row above
        ]
        png = _png_from_rows(1, 2, rows)
        self.assertEqual(s._png_decode_rgb(png), (1, 2, bytes([10, 20, 30, 15, 25, 35])))

    def test_decodes_average_and_paeth_rows(self):
        rows = [
            bytes([0, 10, 20, 30, 40, 50, 60]),
            bytes([3, 10, 10, 10, 10, 10, 10]),  # Average
        ]
        png = _png_from_rows(2, 2, rows)
        w, h, raw = s._png_decode_rgb(png)
        self.assertEqual((w, h), (2, 2))
        self.assertEqual(raw[:6], bytes([10, 20, 30, 40, 50, 60]))
        # Average: raw = filt + (left + up) // 2
        self.assertEqual(raw[6:9], bytes([10 + 5, 10 + 10, 10 + 15]))

        rows = [
            bytes([0, 10, 20, 30]),
            bytes([4, 1, 1, 1]),  # Paeth: predictor is up for the first pixel
        ]
        png = _png_from_rows(1, 2, rows)
        self.assertEqual(s._png_decode_rgb(png)[2], bytes([10, 20, 30, 11, 21, 31]))

    def test_rgba_alpha_is_dropped(self):
        row = bytes([0, 1, 2, 3, 255, 4, 5, 6, 128])
        png = _png_from_rows(2, 1, [row], color_type=6)
        self.assertEqual(s._png_decode_rgb(png), (2, 1, bytes([1, 2, 3, 4, 5, 6])))

    def test_rejects_16_bit_and_interlaced(self):
        row = bytes([0]) + bytes(1 * 6)
        with self.assertRaises(ValueError):
            s._png_decode_rgb(_png_from_rows(1, 1, [row], bit_depth=16))
        row = bytes([0, 1, 2, 3])
        with self.assertRaises(ValueError):
            s._png_decode_rgb(_png_from_rows(1, 1, [row], interlace=1))

    def test_rejects_non_png_bytes(self):
        with self.assertRaises(ValueError):
            s._png_decode_rgb(b"not a png at all")


class ScaleRgbTest(unittest.TestCase):
    def test_downscales_to_max_width(self):
        # 8x4 image, each pixel value = its column index (grayscale-ish RGB).
        raw = bytes(c for y in range(4) for x in range(8) for c in (x, x, x))
        w, h, out = s._scale_rgb_to_max_width(8, 4, raw, 4)
        self.assertEqual((w, h), (4, 2))
        self.assertEqual(len(out), 4 * 2 * 3)

    def test_noop_when_already_small(self):
        raw = bytes(2 * 2 * 3)
        self.assertEqual(s._scale_rgb_to_max_width(2, 2, raw, 640), (2, 2, raw))


class RgbAbsDiffTest(unittest.TestCase):
    def test_identical_frames_have_zero_diff(self):
        raw = bytes(range(12))
        diff_raw, stats = s._rgb_abs_diff(2, 2, raw, raw)
        self.assertEqual(set(diff_raw), {0})
        self.assertEqual(stats["mean_abs_diff"], 0.0)
        self.assertEqual(stats["changed_pct"], 0.0)

    def test_changed_pixels_are_counted(self):
        a = bytes(12)
        b = bytes([100, 0, 0] + [0] * 9)  # one of four pixels changed
        _, stats = s._rgb_abs_diff(2, 2, a, b)
        self.assertEqual(stats["changed_pct"], 25.0)
        self.assertAlmostEqual(stats["mean_abs_diff"], 100 / 12)


class _FakeResolve:
    def __init__(self, page="edit", switch_ok=True):
        self.page = page
        self.switch_ok = switch_ok
        self.opened = []

    def GetCurrentPage(self):
        return self.page

    def OpenPage(self, page):
        self.opened.append(page)
        if self.switch_ok:
            self.page = page
        return self.switch_ok


def _thumb(width, height, raw):
    return {
        "width": width,
        "height": height,
        "format": "RGB 8 bit",
        "data": base64.b64encode(raw).decode("ascii"),
    }


class _Harness:
    """Fake Resolve/timeline/project wired the way _timeline_grab_frames sees them."""

    def __init__(self, thumbs=None, still_png=None, still_ok=True, page="edit", switch_ok=True):
        self.resolve = _FakeResolve(page=page, switch_ok=switch_ok)
        self.thumbs = thumbs or {}
        self.still_png = still_png
        self.still_ok = still_ok
        self.set_timecodes = []
        self.exported_paths = []

        self.tl = mock.Mock()
        self.tl.GetName.return_value = "TL"
        self.tl.GetCurrentTimecode.return_value = "01:00:00:00"
        self.tl.SetCurrentTimecode.side_effect = self._set_timecode
        self.tl.GetCurrentClipThumbnailImage.side_effect = self._thumbnail

        self.proj = mock.Mock()
        self.proj.ExportCurrentFrameAsStill.side_effect = self._export_still
        self._current = None

    def _set_timecode(self, timecode):
        self.set_timecodes.append(timecode)
        self._current = timecode
        return True

    def _thumbnail(self):
        if self.resolve.page != "color":
            return None
        return self.thumbs.get(self._current)

    def _export_still(self, path):
        self.exported_paths.append(path)
        if not self.still_ok:
            return False
        with open(path, "wb") as handle:
            handle.write(self.still_png)
        return True

    def run(self, params):
        root = {"success": True, "project_root": tempfile.mkdtemp(prefix="eyes_")}
        with mock.patch.object(s, "get_resolve", return_value=self.resolve), \
             mock.patch.object(s, "resolve_media_analysis_output_root", return_value=root), \
             mock.patch.object(s, "_timeline_frame_id_to_timecode",
                               side_effect=lambda tl, f: (f"tc-{f}", None)), \
             mock.patch.object(s, "_marker_display_frame", side_effect=lambda tl, f: f):
            return s._timeline_grab_frames(self.proj, self.tl, params)


class GrabFramesTest(unittest.TestCase):
    def test_grabs_thumbnails_and_restores_page_and_playhead(self):
        raw = bytes([200] * (2 * 2 * 3))
        h = _Harness(thumbs={"tc-0": _thumb(2, 2, raw), "tc-10": _thumb(2, 2, raw)})
        out = h.run({"frames": [0, 10]})
        self.assertTrue(out.get("success"), out)
        self.assertEqual(len(out["frames"]), 2)
        for entry in out["frames"]:
            self.assertEqual(entry["source"], "thumbnail")
            self.assertTrue(os.path.exists(entry["path"]), entry)
            self.assertEqual((entry["width"], entry["height"]), (2, 2))
        self.assertEqual(out["frames"][0]["timecode"], "tc-0")
        # Page went to color and came back; playhead restored last.
        self.assertEqual(h.resolve.opened, ["color", "edit"])
        self.assertEqual(h.set_timecodes[-1], "01:00:00:00")
        self.assertFalse(h.proj.ExportCurrentFrameAsStill.called)

    def test_frames_are_downscaled_to_max_width(self):
        raw = bytes(8 * 4 * 3)
        h = _Harness(thumbs={"tc-0": _thumb(8, 4, raw)})
        out = h.run({"frames": [0], "max_width": 4})
        entry = out["frames"][0]
        self.assertEqual((entry["width"], entry["height"]), (4, 2))
        self.assertEqual(s._png_decode_rgb(open(entry["path"], "rb").read())[:2], (4, 2))

    def test_falls_back_to_export_current_frame_as_still(self):
        still = s._rgb_to_png_bytes(4, 2, bytes(4 * 2 * 3))
        h = _Harness(thumbs={}, still_png=still)
        out = h.run({"frames": [5]})
        self.assertTrue(out.get("success"), out)
        entry = out["frames"][0]
        self.assertEqual(entry["source"], "still_export")
        self.assertTrue(os.path.exists(entry["path"]))
        self.assertEqual((entry["width"], entry["height"]), (4, 2))

    def test_fallback_can_be_disabled(self):
        h = _Harness(thumbs={}, still_png=b"")
        out = h.run({"frames": [5], "allow_still_fallback": False})
        self.assertFalse(out.get("success"))
        self.assertFalse(h.proj.ExportCurrentFrameAsStill.called)
        self.assertIn("error", out["frames"][0])

    def test_reports_error_when_both_paths_fail(self):
        h = _Harness(thumbs={}, still_ok=False)
        out = h.run({"frames": [5]})
        self.assertFalse(out.get("success"))
        self.assertIn("error", out["frames"][0])

    def test_diff_pair_writes_diff_png_with_stats(self):
        a = bytes([0] * 12)
        b = bytes([120, 0, 0] + [0] * 9)
        h = _Harness(thumbs={"tc-0": _thumb(2, 2, a), "tc-10": _thumb(2, 2, b)})
        out = h.run({"frames": [0, 10], "diff_pair": [0, 10]})
        self.assertTrue(out.get("success"), out)
        diff = out["diff"]
        self.assertTrue(os.path.exists(diff["path"]))
        self.assertEqual(diff["frame_a"], 0)
        self.assertEqual(diff["frame_b"], 10)
        self.assertEqual(diff["changed_pct"], 25.0)
        self.assertGreater(diff["mean_abs_diff"], 0)

    def test_diff_pair_frames_need_not_be_listed_in_frames(self):
        a = bytes([0] * 12)
        b = bytes([255] * 12)
        h = _Harness(thumbs={"tc-3": _thumb(2, 2, a), "tc-7": _thumb(2, 2, b)})
        out = h.run({"frames": [], "diff_pair": [3, 7]})
        self.assertTrue(out.get("success"), out)
        self.assertEqual(out["diff"]["changed_pct"], 100.0)
        self.assertEqual({e["frame"] for e in out["frames"]}, {3, 7})

    def test_frames_must_be_a_list(self):
        h = _Harness()
        out = h.run({"frames": 3})
        self.assertFalse(out.get("success"))
        self.assertIn("error", out)

    def test_diff_pair_must_be_two_frames(self):
        h = _Harness()
        out = h.run({"frames": [], "diff_pair": [1]})
        self.assertFalse(out.get("success"))
        self.assertIn("error", out)

    def test_grab_frames_is_a_timeline_action(self):
        self.assertIn("grab_frames", s._TIMELINE_ACTIONS)


if __name__ == "__main__":
    unittest.main()
