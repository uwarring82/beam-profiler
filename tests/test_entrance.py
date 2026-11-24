"""Entry routes must work when Pages serves the repository under /beam-profiler/."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
SITE = "https://example.org/beam-profiler/"


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.ids = set()
        self.tags = []
        self.nav_links = []
        self.anchors = {}
        self.in_nav = False
        self.anchor = None
        self.feed(path.read_text(encoding="utf-8"))
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "nav":
            self.in_nav = True
        if tag == "a" and "href" in attrs:
            self.anchor = attrs["href"]
            self.anchors.setdefault(self.anchor, [])
            if self.in_nav:
                self.nav_links.append(self.anchor)

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchors[self.anchor].append(data)

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_nav = False
        if tag == "a":
            self.anchor = None


@pytest.mark.parametrize("tool", ["index.html", "beam-path/index.html"])
def test_tool_navigation_starts_with_entrance(tool):
    first = Page(ROOT / tool).nav_links[0]
    assert urljoin(SITE + tool, first) == SITE + "start/"


def test_landing_actions_and_routes():
    page = Page(ROOT / "start/index.html")
    actions = {attributes["href"] for tag, attributes in page.tags
               if tag == "a" and "primary" in attributes.get("class", "").split()}
    assert actions == {"../beam-path/?stage=lbo#plot"}
    labels = {
        "../beam-path/?stage=lbo#plot": "Move a lens",
        "#routes": "Different task? Choose your route ↓",
        "layer-0.html": "Understand Gaussian beams first",
        "layer-1.html": "Couple a beam into a cavity or fibre",
        "layer-1.html#focus": "Focus a beam onto a target",
        "layer-2.html": "Characterise a laser beam",
        "layer-4.html": "The tools cannot do what I need",
    }
    for href, label in labels.items():
        assert label in page.anchors[href]


@pytest.mark.parametrize("path", sorted((ROOT / "start").glob("*.html")), ids=lambda p: p.name)
def test_static_pages_have_working_local_destinations(path):
    page = Page(path)
    base = SITE + path.relative_to(ROOT).as_posix()
    stylesheets = [attrs["href"] for tag, attrs in page.tags
                   if tag == "link" and attrs.get("rel") == "stylesheet"]
    assert not any(tag == "script" for tag, _ in page.tags)
    byte_count = path.stat().st_size
    for href in [*page.anchors, *stylesheets]:
        url = urlsplit(urljoin(base, href))
        if url.netloc != urlsplit(SITE).netloc:
            assert href not in stylesheets
            continue
        assert url.path.startswith("/beam-profiler/")
        destination = ROOT / url.path.removeprefix("/beam-profiler/")
        if destination.is_dir():
            destination /= "index.html"
        assert destination.is_file(), href
        if url.fragment:
            assert url.fragment in Page(destination).ids, href
        if href in stylesheets:
            byte_count += destination.stat().st_size
    assert byte_count < 200_000
