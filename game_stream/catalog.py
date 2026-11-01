"""Extract event page links, not stream credentials, from a sports index."""
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import shutil
from threading import Event
from time import time
from urllib.parse import urljoin, urlsplit, unquote, urlunsplit

import requests
from .config import xdg_path, write_json
from .resolver import validate_url, load_cookies

SPORTS = {'nfl': 'NFL', 'nba': 'NBA', 'mlb': 'MLB', 'nhl': 'NHL', 'wnba': 'WNBA',
          'ncaaf': 'NCAAF', 'college-football': 'NCAAF', 'cfb': 'NCAAF',
          'ncaab': 'NCAAB', 'college-basketball': 'NCAAB', 'cbb': 'NCAAB',
          'soccer': 'Soccer', 'football': 'Soccer', 'ufc': 'UFC', 'mma': 'UFC',
          'boxing': 'Boxing', 'tennis': 'Tennis', 'f1': 'Formula 1', 'formula-1': 'Formula 1',
          'motogp': 'MotoGP', 'wwe': 'WWE', 'cricket': 'Cricket', 'rugby': 'Rugby',
          'other-events': 'Other Events'}


@dataclass(frozen=True)
class Game:
    sport: str
    title: str
    url: str


class CatalogError(RuntimeError):
    pass


class IndexParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None
        self.title_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            if self.current:
                self.links.append(self.current)
            self.current = [attrs.get('href', ''), attrs.get('title') or attrs.get('aria-label', ''), [], []]
            self.title_depth = 0
        elif tag == 'img' and self.current and attrs.get('alt'):
            self.current[2].append(attrs['alt'])
        if self.current and (self.title_depth or 'm-card__title' in attrs.get('class', '').split()):
            self.title_depth += 1

    def handle_endtag(self, tag):
        if tag == 'a' and self.current:
            self.links.append(self.current)
            self.current = None
            self.title_depth = 0
        elif self.title_depth:
            self.title_depth -= 1

    def handle_data(self, data):
        if self.current:
            self.current[2].append(data)
            if self.title_depth:
                self.current[3].append(data)


def parse_games(html, root):
    validate_url(root)
    parser = IndexParser()
    parser.feed(html)
    if parser.current:
        parser.links.append(parser.current)
    host = urlsplit(root).hostname.lower()
    host = re.sub(r'^(?:www|v\d+)\.', '', host)
    found = {}
    for href, title, parts, event_title in parser.links:
        if len(href) > 16384:
            continue
        url = urljoin(root, href)
        try:
            validate_url(url)
        except ValueError:
            continue
        parsed = urlsplit(url)
        link_host = parsed.hostname.lower()
        if link_host != host and not link_host.endswith('.' + host):
            continue
        path = [p for p in unquote(parsed.path).lower().split('/') if p]
        category = next((i for i, p in enumerate(path[:-1]) if p in SPORTS), None)
        if category is None:
            continue
        slug = path[category + 1]
        # Exclude sport navigation, standings and news; accept matchup slugs or
        # descriptive event slugs (e.g. UFC cards, grand prix, tournaments).
        if slug in ('schedule', 'standings', 'news', 'scores', 'live', 'streams', 'highlights'):
            continue
        sport = SPORTS[path[category]]
        matchup = re.search(r'[-_](?:vs|v|at)[-_]', slug)
        redzone = sport == 'NFL' and re.fullmatch(r'(?:nfl-)?redzone(?:-\d+)?', slug)
        if (not matchup and not redzone and sport in ('NFL', 'NBA', 'MLB', 'NHL', 'WNBA', 'NCAAF', 'NCAAB', 'Soccer', 'Cricket', 'Rugby')):
            continue
        if not matchup and not redzone and len(slug.split('-')) < 3:
            continue
        label = ' '.join((title or ' '.join(event_title) or ' '.join(parts)).split())
        if len(label) < 6 or label.lower() in ('watch now', 'watch live', 'live stream', 'watch stream'):
            label = re.sub(r'-\d+$', '', slug).replace('-', ' ').title().replace(' Vs ', ' vs ')
        label = label[:180]
        # Server choices in trailing /1, /2 paths share the same slug. Keep
        # numeric slug suffixes: MLB doubleheaders can be separate events.
        key = (sport, slug)
        clean_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))
        found.setdefault(key, Game(sport, label, clean_url))
        if len(found) >= 2000:
            break
    return sorted(found.values(), key=lambda g: (g.sport, g.title.casefold()))


def fetch_games(root, browser=False, cookies='', cancel=None):
    validate_url(root)
    cancel = cancel or Event()
    if not browser:
        try:
            with requests.get(root, timeout=(10, 20), stream=True,
                              headers={'User-Agent': 'Mozilla/5.0 GameStream/0.2'}) as response:
                response.raise_for_status()
                body = bytearray()
                for part in response.iter_content(65536):
                    if cancel.is_set():
                        raise CatalogError('Refresh cancelled')
                    body.extend(part)
                    if len(body) > 6 * 1024 * 1024:
                        raise CatalogError('Root page is too large')
                games = parse_games(body.decode(response.encoding or 'utf-8', errors='replace'), response.url)
                if games:
                    return games
        except requests.RequestException:
            pass
        # Some roots render links in JavaScript or require the normal browser
        # request stack. Do not bypass site challenges or access restrictions.
    if cancel.is_set():
        raise CatalogError('Refresh cancelled')
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            executable = os.environ.get('GAME_STREAM_CHROMIUM') or os.environ.get('STREAMWALL_CHROMIUM')
            if not executable and not Path(p.chromium.executable_path).exists():
                executable = shutil.which('chromium') or shutil.which('chromium-browser')
            instance = p.chromium.launch(headless=True, executable_path=executable)
            try:
                context = instance.new_context()
                imported = load_cookies(cookies)
                if imported:
                    context.add_cookies(imported)
                page = context.new_page()
                response = page.goto(root, wait_until='domcontentloaded', timeout=25000)
                if response and response.status >= 400:
                    raise CatalogError('The root page denied access. Check the URL or try again later.')
                for _ in range(15):
                    if cancel.is_set():
                        raise CatalogError('Refresh cancelled')
                    games = parse_games(page.content(), page.url)
                    if games:
                        return games
                    page.wait_for_timeout(250)
            finally:
                instance.close()
    except CatalogError:
        raise
    except Exception as exc:
        raise CatalogError('Could not refresh the root page. Check connectivity, browser installation and Preferences.') from exc
    raise CatalogError('No supported game links found. The page may have no events, require login, or have changed its layout.')


class CatalogCache:
    def __init__(self, path=None):
        self.path = Path(path) if path else xdg_path('XDG_CACHE_HOME', '.cache', 'catalog.json')

    def load(self, root):
        try:
            if self.path.stat().st_size > 2 * 1024 * 1024:
                return [], None
            data = json.loads(self.path.read_text())
            if data['root'] != root or len(data['games']) > 2000:
                return [], None
            games = [Game(**g) for g in data['games']]
            for game in games:
                validate_url(game.url)
                if (not all(isinstance(v, str) for v in (game.title, game.sport)) or
                        game.sport not in SPORTS.values() or not 1 <= len(game.title) <= 180 or len(game.url) > 16384):
                    return [], None
            stamp = data.get('updated')
            if type(stamp) not in (float, int) or not math.isfinite(stamp) or not 0 <= stamp <= time() + 86400:
                return [], None
            return games, stamp
        except (OSError, ValueError, KeyError, TypeError):
            return [], None

    def save(self, root, games):
        stamp = time()
        write_json(self.path, dict(root=root, updated=stamp, games=[asdict(g) for g in games]))
        return stamp
