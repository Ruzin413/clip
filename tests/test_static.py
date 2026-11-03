import re
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from clipstudio.app import app

client = TestClient(app)

PAGES = ["/"]
IMPORT = re.compile(r"""^\s*(?:import|export)\b[^'"]*?from\s+['"](\./[^'"]+)['"]""", re.M)


class PageScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets: list[str] = []
        self.inline: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "style":
            self.inline.append("<style>")
        if "style" in attrs:
            self.inline.append(f"<{tag} style=...>")
        self.inline += [f"<{tag} {name}=...>" for name in attrs if name.startswith("on")]
        if tag == "link" and attrs.get("rel") == "stylesheet" and attrs["href"].startswith("/"):
            self.assets.append(attrs["href"])
        if tag == "script":
            self._in_script = True
            if attrs.get("src"):
                self.assets.append(attrs["src"])

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.inline.append("<script> body")


def _scan(page: str) -> PageScan:
    res = client.get(page)
    assert res.status_code == 200, page
    scan = PageScan()
    scan.feed(res.text)
    return scan


def _with_imports(assets: list[str]) -> set[str]:
    seen, todo = set(), list(assets)
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        res = client.get(path)
        assert res.status_code == 200, f"{path} is referenced but not served"
        if path.endswith(".js"):
            base = path.rsplit("/", 1)[0]
            todo += [f"{base}/{name[2:]}" for name in IMPORT.findall(res.text)]
    return seen


@pytest.mark.parametrize("page", PAGES)
def test_page_and_every_asset_it_loads_are_served(page):
    served = _with_imports(_scan(page).assets)
    assert any(p.endswith(".css") for p in served) and any(p.endswith(".js") for p in served)


@pytest.mark.parametrize("page", PAGES)
def test_page_is_markup_only(page):
    assert _scan(page).inline == []
