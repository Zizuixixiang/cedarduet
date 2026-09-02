import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
IOS_INPUT_RULE = """@supports (-webkit-touch-callout: none) {
  @media (hover: none) and (pointer: coarse) {
    input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="hidden"]),
    textarea,
    select {
      font-size: 16px !important;
    }
  }
}"""


class IOSInputZoomTests(unittest.TestCase):
    def test_pages_keep_user_zoom_available(self):
        for filename in ("index.html", "chips.html"):
            with self.subTest(filename=filename):
                html = (STATIC / filename).read_text(encoding="utf-8")
                viewport = re.search(
                    r'<meta\s+name="viewport"\s+content="([^"]+)"', html
                )
                self.assertIsNotNone(viewport)
                content = viewport.group(1).lower()
                self.assertNotIn("user-scalable=no", content)
                self.assertNotIn("maximum-scale=1", content)

    def test_ios_touch_controls_use_non_zooming_font_size(self):
        for filename in ("styles.css", "chips.css"):
            with self.subTest(filename=filename):
                css = (STATIC / filename).read_text(encoding="utf-8")
                self.assertIn(IOS_INPUT_RULE, css)


if __name__ == "__main__":
    unittest.main()
