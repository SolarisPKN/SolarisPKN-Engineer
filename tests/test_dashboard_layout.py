"""Structural regression checks for compact Engineer workspace layout."""
from pathlib import Path
from html.parser import HTMLParser
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=[]
        self.tabs=[]
        self.panes=[]
        self.stack=[]
        self.closing_errors=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if attrs.get("id"):self.ids.append(attrs["id"])
        if attrs.get("data-tab"):self.tabs.append(attrs["data-tab"])
        if attrs.get("data-pane"):self.panes.append(attrs["data-pane"])
        if tag in ("section","main","nav","aside","div"):
            self.stack.append(tag)
    def handle_endtag(self, tag):
        if tag in ("section","main","nav","aside","div"):
            if not self.stack or self.stack[-1]!=tag:
                self.closing_errors.append((tag, self.stack[-1] if self.stack else "empty"))
            elif self.stack:
                self.stack.pop()


class LayoutTests(unittest.TestCase):
    def test_main_sections_and_panes(self):
        document=(ROOT/"dashboard_v2.html").read_text(encoding="utf-8")
        parser=Structure()
        parser.feed(document.split("<script>",1)[0])
        self.assertFalse(parser.closing_errors,parser.closing_errors)
        self.assertFalse(parser.stack,parser.stack)
        self.assertEqual(len(set(parser.ids)),len(parser.ids),"Duplicate HTML element IDs")
        self.assertEqual(set(parser.tabs),{"projects","logs","integrations","hardware"})
        self.assertEqual(set(parser.panes),{"tree","inspect","scope"})
        for name in ("map","tree","tree-only","details","scope","scan","register",
                     "work-layout","toggle-sidebar","focus-map","scan-options"):
            self.assertIn(name,parser.ids)

    def test_visualizer_group_navigation(self):
        document=(ROOT/"visualizer.html").read_text(encoding="utf-8")
        for feature in ('id="page-prev"','id="page-next"','id="fit"',
                        'id="status-filter"','function drawOverview()',
                        'function focusFile(','function enterGroup('):
            self.assertIn(feature,document)


if __name__=="__main__":
    unittest.main()
