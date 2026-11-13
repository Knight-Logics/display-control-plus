import unittest

from screen_grab import crop_box_for_virtual_desktop, normalize_bbox


class ScreenGrabCoordinateTests(unittest.TestCase):
    def test_monitor_left_of_primary_crops_inside_the_virtual_desktop(self):
        crop = crop_box_for_virtual_desktop((-1920, 0, 0, 1080), (-1920, 0))
        self.assertEqual(crop, (0, 0, 1920, 1080))

    def test_monitor_above_primary_crops_inside_the_virtual_desktop(self):
        crop = crop_box_for_virtual_desktop((0, -1200, 1920, 0), (0, -1200))
        self.assertEqual(crop, (0, 0, 1920, 1200))

    def test_monitor_left_and_above_primary(self):
        crop = crop_box_for_virtual_desktop((-1920, -1080, 0, 0), (-1920, -1080))
        self.assertEqual(crop, (0, 0, 1920, 1080))
        self.assertGreaterEqual(min(crop), 0)

    def test_primary_monitor_is_unchanged_when_origin_is_zero(self):
        bbox = (0, 0, 2560, 1440)
        self.assertEqual(crop_box_for_virtual_desktop(bbox, (0, 0)), bbox)

    def test_second_monitor_to_the_right_keeps_its_offset(self):
        crop = crop_box_for_virtual_desktop((1920, 0, 3840, 1080), (-1920, 0))
        self.assertEqual(crop, (3840, 0, 5760, 1080))

    def test_empty_and_inverted_regions_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_bbox((-1920, 0, -1920, 1080))
        with self.assertRaises(ValueError):
            normalize_bbox((10, 40, 0, 20))

    def test_region_outside_the_reported_virtual_origin_is_rejected(self):
        with self.assertRaises(ValueError):
            crop_box_for_virtual_desktop((-1920, 0, 0, 1080), (0, 0))


if __name__ == "__main__":
    unittest.main()
