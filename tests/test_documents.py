"""Unit tests for tender document download helpers."""

import unittest

from scraper.documents import safe_dir_name


class TestSafeDirName(unittest.TestCase):

    def test_spaces_become_underscores(self):
        """The portal's 'TNA2600106 EOI' must not create a folder with a trailing space."""
        self.assertEqual(safe_dir_name("TNA2600106 EOI"), "TNA2600106_EOI")

    def test_slashes_are_replaced(self):
        self.assertEqual(safe_dir_name("PU/2026/001"), "PU_2026_001")

    def test_path_separators_cannot_escape_the_destination(self):
        """A hostile tender number must not resolve outside the documents directory."""
        self.assertNotIn("/", safe_dir_name("../../etc/passwd"))
        self.assertNotIn("..", safe_dir_name(".."))

    def test_empty_input_falls_back_to_a_usable_name(self):
        self.assertEqual(safe_dir_name("   "), "tender")


if __name__ == "__main__":
    unittest.main()
