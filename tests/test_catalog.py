import json
from http.server import BaseHTTPRequestHandler
from threading import Event
import time
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from game_stream.app import Wall
from game_stream.catalog import Game, CatalogCache, CatalogError, parse_games, fetch_games
from game_stream.catalog_ui import CatalogController
from game_stream.config import ConfigStore, DEFAULTS
from test_streams import serve

ROOT = 'https://v2.streameast.ga/'
HTML = '''<a href="/nfl/">NFL</a><a href="/soccer/english-premier-league/">League</a>
<a href="/nfl/browns-vs-steelers/1"><img alt="Browns"> vs Steelers</a>
<a href="/nfl/browns-vs-steelers/2">Duplicate server</a>
<a href="/nba/lakers-vs-celtics/">Lakers vs Celtics</a>
<a href="/mlb/dodgers-vs-yankees/">Dodgers vs Yankees</a>
<a href="/nhl/bruins-vs-leafs/">Bruins vs Leafs</a>
<a href="/ncaaf/michigan-vs-ohio-state/">Michigan vs Ohio State</a>
<a href="/ufc/ufc-400-main-card/">UFC main card</a>
<a href="https://ads.example/nfl/ad-vs-team/">Ad</a>
<a href="javascript:alert(1)">Bad</a><a href="/nfl/news/something-vs-else/">News</a>'''


def test_major_sports_relative_links_dedup_and_navigation():
    games = parse_games(HTML, ROOT)
    assert len(games) == 6
    assert {g.sport for g in games} == {'NFL', 'NBA', 'MLB', 'NHL', 'NCAAF', 'UFC'}
    nfl = next(g for g in games if g.sport == 'NFL')
    assert nfl.url == ROOT.rstrip('/') + '/nfl/browns-vs-steelers/1'
    assert nfl.title == 'Browns vs Steelers'
    assert all('ads.example' not in g.url for g in games)
    doubleheader = '<a href="/mlb/team-a-vs-team-b-1/">Game 1</a><a href="/mlb/team-a-vs-team-b-2/">Game 2</a>'
    assert len(parse_games(doubleheader, ROOT)) == 2


def test_preferences_validation_and_private_file(tmp_path):
    store = ConfigStore(tmp_path / 'config.json')
    assert store.load() == DEFAULTS
    value = store.save({**DEFAULTS, 'double_tap_ms': 500})
    assert store.load() == value
    assert store.path.stat().st_mode & 0o777 == 0o600
    for bad in ({'root_url': 'file:///etc/passwd'}, {'double_tap_ms': 30}, {'catalog_browser': 'false'}):
        with pytest.raises(ValueError):
            store.save({**DEFAULTS, **bad})


def test_catalog_cache_root_change_and_corruption(tmp_path):
    cache = CatalogCache(tmp_path / 'cache.json')
    games = parse_games(HTML, ROOT)
    cache.save(ROOT, games)
    assert cache.load(ROOT)[0] == games
    assert cache.load('https://different.example/')[0] == []
    data = json.loads(cache.path.read_text())
    data['updated'] = 'bad timestamp'
    cache.path.write_text(json.dumps(data))
    assert cache.load(ROOT)[0] == []


def test_fetch_static_and_javascript_index():
    class Site(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == '/dynamic':
                body = '<script>setTimeout(()=>document.body.innerHTML=`' + HTML + '`,200)</script><body></body>'
            else:
                body = HTML
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(body.encode())
    with serve(Site) as origin:
        assert len(fetch_games(origin)) == 6
        assert len(fetch_games(origin + '/dynamic', browser=True)) == 6


def test_sports_menu_loads_selected_quadrant(tmp_path):
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    wall.count.setCurrentIndex(1)
    controller = CatalogController(wall, ConfigStore(tmp_path / 'config.json'), CatalogCache(tmp_path / 'cache.json'))
    game = Game('NFL', 'Browns vs Steelers', ROOT + 'nfl/browns-vs-steelers/1')
    controller.received([game])
    wall.panes[3].load = Mock()
    try:
        sport = next(a.menu() for a in wall.games_menu.actions() if a.text().startswith('NFL'))
        event = sport.actions()[0].menu()
        event.actions()[3].trigger()
        assert wall.count.currentIndex() == 3
        assert wall.panes[3].url.text() == game.url
        wall.panes[3].load.assert_called_once_with(auto_play=True)
        assert controller.games == [game]
    finally:
        wall.close()


def test_failed_refresh_keeps_last_good_games(tmp_path, monkeypatch):
    from game_stream import catalog_ui
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    store = ConfigStore(tmp_path / 'config.json')
    store.save({**DEFAULTS, 'refresh_on_start': False})
    cache = CatalogCache(tmp_path / 'cache.json')
    games = parse_games(HTML, ROOT)
    cache.save(ROOT, games)
    controller = CatalogController(wall, store, cache)
    controller.start()
    def failure(*args, **kwargs):
        raise CatalogError('Fixture denied access')
    monkeypatch.setattr(catalog_ui, 'fetch_games', failure)
    try:
        controller.refresh()
        until(lambda: controller.worker is None)
        assert controller.games == games
        assert cache.load(ROOT)[0] == games
        assert 'denied' in controller.error
        assert any('Refresh Games' == a.text() for a in wall.games_menu.actions())
    finally:
        wall.close()


def test_changed_root_does_not_cache_old_inflight_results(tmp_path, monkeypatch):
    from game_stream import catalog_ui
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    store = ConfigStore(tmp_path / 'config.json')
    store.save({**DEFAULTS, 'refresh_on_start': False})
    cache = CatalogCache(tmp_path / 'cache.json')
    controller = CatalogController(wall, store, cache)
    controller.start()
    game = Game('NBA', 'Lakers vs Celtics', 'https://new.example/nba/lakers-vs-celtics/')
    def fetch(root, *args, cancel, **kwargs):
        if root == ROOT:
            cancel.wait(5)
            return parse_games(HTML, ROOT)
        return [game]
    monkeypatch.setattr(catalog_ui, 'fetch_games', fetch)
    try:
        controller.refresh()
        controller.apply_preferences(store.save({**DEFAULTS, 'root_url': 'https://new.example/', 'refresh_on_start': False}))
        controller.worker.cancel.set()
        controller.refresh_after = True
        until(lambda: controller.worker is None)
        assert controller.games == [game]
        assert cache.load('https://new.example/')[0] == [game]
    finally:
        wall.close()


def until(predicate):
    deadline = time.monotonic() + 8
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def test_double_tap_audio_zoom_and_timeout():
    app = QApplication.instance() or QApplication([])
    wall = Wall(Mock())
    wall.show()
    app.processEvents()
    clock = [100.0]
    wall.key_clock = lambda: clock[0]
    try:
        QTest.keyClick(wall, Qt.Key.Key_1)
        assert wall.panes[0].audio.isChecked()
        assert not wall.isFullScreen()
        clock[0] += 0.1
        QTest.keyClick(wall, Qt.Key.Key_1)
        app.processEvents()
        assert wall.expanded is wall.panes[0]
        assert wall.isFullScreen()
        QTest.keyClick(wall, Qt.Key.Key_0)
        clock[0] += 1
        QTest.keyClick(wall, Qt.Key.Key_1)
        clock[0] += 0.1
        QTest.keyClick(wall, Qt.Key.Key_1)
        assert wall.expanded is None
        assert not any(p.audio.isChecked() for p in wall.panes)
        clock[0] += 1
        QTest.keyClick(wall, Qt.Key.Key_2)
        clock[0] += 1
        QTest.keyClick(wall, Qt.Key.Key_2)
        assert wall.expanded is None  # Slow taps are audio-only.
        assert wall.panes[1].audio.isChecked()
        app.sendEvent(wall, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2,
            Qt.KeyboardModifier.NoModifier, '2', True))
        assert wall.expanded is None
        clock[0] += 1
        QTest.keyClick(wall, Qt.Key.Key_4)
        QTest.keyClick(wall, Qt.Key.Key_9)
        clock[0] += 0.1
        QTest.keyClick(wall, Qt.Key.Key_4)
        assert wall.expanded is None
    finally:
        wall.close()
