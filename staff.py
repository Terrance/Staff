#!/usr/bin/env python3

from datetime import date, datetime
from enum import Enum, auto
from functools import cached_property, wraps
import json
import logging
from typing import Any, Callable, Iterator, TypeVar

from bs4 import BeautifulSoup, Tag
import requests


_T = TypeVar("_T")

LOG = logging.getLogger(__name__)


class StoryGraphAPI:

    DOMAIN = "app.thestorygraph.com"

    _COOKIE = "_storygraph_session"

    class Error(Exception):
        pass

    def __init__(self):
        self._session = requests.Session()
        self.username = None

    def _request(self, method: str, path: str, **kwargs):
        return self._session.request(method, f"https://{self.DOMAIN}{path}", **kwargs)

    def _get(self, path: str, **kwargs):
        return self._request("GET", path, **kwargs)

    def _post(self, path: str, form: dict | None = None, **kwargs):
        if form:
            kwargs["data"] = form
        return self._request("POST", path, **kwargs)

    def _html(self, resp: requests.Response):
        return BeautifulSoup(resp.text, "html.parser")

    @staticmethod
    def _paged(fn: Callable[[Any, str | None], tuple[list[_T], BeautifulSoup]]) -> Callable[[], Iterator[_T]]:
        @wraps(fn)
        def inner(self, *args, **kwargs):
            path = None
            while True:
                chunk, page = fn(self, path, *args, **kwargs)
                yield from chunk
                more = page.find(id="next_link")
                if not isinstance(more, Tag):
                    break
                path = more["href"]
        return inner

    def _identify(self, page: BeautifulSoup):
        for link in page.nav.find_all("a"):
            if link["href"].startswith("/profile/"):
                username = link["href"].rsplit("/", 1)[1]
                LOG.info("Logged in as %s", username)
                return username
        else:
            raise self.Error("No username")

    def login(self, email: str, password: str):
        resp = self._get("/users/sign_in")
        page = self._html(resp)
        if resp.url.endswith("/users/sign_in"):
            form = page.find("form", action="/users/sign_in")
            data = {}
            for field in form.find_all("input"):
                match field["type"]:
                    case "email":
                        value = email
                    case "password":
                        value = password
                    case _:
                        value = field.get("value", "")
                data[field["name"]] = value
            page = self._html(self._post("/users/sign_in", data))
        self.username = self._identify(page)

    @_paged
    def popular_books(self, path: str | None = None):
        page = self._html(self._get(path or "/browse"))
        root = page.find(class_="search-results-books-panes")
        return [Book(self, tag) for tag in root.find_all("div", recursive=False)], page

    @_paged
    def owned_books(self, path: str | None = None):
        page = self._html(self._get(path or f"/owned-books/{self.username}"))
        root = page.find(class_="owned-books-panes")
        return [Book(self, tag) for tag in root.find_all("div", recursive=False)], page

    @_paged
    def to_read_books(self, path: str | None = None):
        page = self._html(self._get(path or f"/to-read/{self.username}"))
        root = page.find(class_="to-read-books-panes")
        return [Book(self, tag) for tag in root.find_all("div", recursive=False)], page

    @_paged
    def current_books(self, path: str | None = None):
        page = self._html(self._get(path or f"/currently-reading/{self.username}"))
        root = page.find(class_="read-books-panes")
        return [Book(self, tag) for tag in root.find_all("div", recursive=False)], page

    @_paged
    def read_books(self, path: str | None = None):
        page = self._html(self._get(path or f"/books-read/{self.username}"))
        root = page.find(class_="read-books-panes")
        return [Book(self, tag) for tag in root.find_all("div", recursive=False)], page

    @_paged
    def journal(self, path: str | None = None):
        page = self._html(self._get(path or "/journal"))
        root = page.find(class_="journal-entry-panes")
        return [Entry(self, tag) for tag in root.find_all("div", recursive=False)], page


class Element:

    def __init__(self, sg: StoryGraphAPI, tag: Tag):
        self._sg = sg
        self._tag = tag

    def _children(self, tag: Tag | None = None):
        return (tag or self._tag).find_all(recursive=False)


class Book(Element):

    class Status(Enum):
        NONE = auto()
        TO_READ = auto()
        CURRENT = auto()
        READ = auto()
        DID_NOT_FINISH = auto()

    @property
    def _info(self) -> Tag:
        return self._tag.find(class_="book-title-author-and-series")

    @property
    def _title_author_series(self) -> tuple[str, str, str | None, str | None]:
        root: Tag = self._tag.find(class_="book-title-author-and-series")
        series = number = None
        for link in root.find_all("a"):
            match link["href"].split("/", 2)[1]:
                case "books":
                    title = link.text
                case "authors":
                    author = link.text
                case "series":
                    if not series:
                        series = link.text
                    elif link.text[0] == "#":
                        number = link.text[1:]
        return (title, author, series, number)

    @property
    def title(self) -> str:
        return self._title_author_series[0]

    @property
    def author(self) -> str:
        return self._title_author_series[1]

    @property
    def series(self) -> tuple[str | None, int | None]:
        return self._title_author_series[2:]

    @property
    def pages(self) -> int | None:
        for text in self._tag.find_all(string=True):
            text: str
            parts = text.split()
            if len(parts) == 2 and parts[1] == "pages" and parts[0].isdigit():
                return int(parts[0])
        return None

    @property
    def status(self) -> Status:
        label = self._tag.find(class_="read-status-label")
        if not label:
            return self.Status.NONE
        match label.text:
            case "to read":
                return self.Status.TO_READ
            case "currently reading":
                return self.Status.CURRENT
            case "read":
                return self.Status.READ
            case "did not finish":
                return self.Status.DID_NOT_FINISH
            case _:
                raise StoryGraphAPI.Error("Unknown status")

    @property
    def owned(self) -> bool:
        return self._tag.find(class_="remove-from-owned-link") is not None

    def __repr__(self):
        return "<{}: {!r} {!r}>".format(self.__class__.__name__, self.author, self.title)


class Entry(Element):

    class Progress(Enum):
        STARTED = auto()
        UPDATED = auto()
        FINISHED = auto()

    class DateAccuracy(Enum):
        DAY = auto()
        MONTH = auto()
        YEAR = auto()

    _DATES = (
        ("%d %B %Y", DateAccuracy.DAY),
        ("%B %Y", DateAccuracy.MONTH),
        ("%Y", DateAccuracy.YEAR),
    )

    @property
    def _cover(self) -> Tag:
        return self._children()[0]

    @property
    def _info(self) -> Tag:
        return self._children()[1]

    @property
    def _date(self) -> Tag:
        return self._children(self._info)[0]

    @property
    def _title(self) -> Tag:
        return self._children(self._info)[1]

    @property
    def _progress(self) -> Tag:
        return self._children(self._info)[2]

    @cached_property
    def _edit_page(self) -> BeautifulSoup:
        for link in self._date.find_all("a"):
            if link["href"].startswith("/journal_entries/"):
                return self._sg._html(self._sg._get(link["href"]))
        else:
            raise StoryGraphAPI.Error("No entry edit page")

    @property
    def date(self) -> tuple[date | None, DateAccuracy | None]:
        for text in self._date.find_all(string=True):
            if "No date" in text:
                return (None, None)
            for fmt, accuracy in self._DATES:
                try:
                    return datetime.strptime(text.strip(), fmt).date(), accuracy
                except ValueError:
                    pass
        else:
            raise StoryGraphAPI.Error("No entry date")

    @property
    def title(self) -> str:
        return self._title.a.text

    @property
    def author(self) -> str:
        prefix = f"{self.title} by "
        combined = self._cover.img["alt"]
        if not combined.startswith(prefix):
            raise StoryGraphAPI.Error("Can't derive author")
        return combined[len(prefix):]

    @property
    def progress(self) -> tuple[Progress, int]:
        for text in self._progress.find_all(string=True):
            if "Started" in text:
                return Entry.Progress.STARTED, 0
            elif "Finished" in text:
                return Entry.Progress.FINISHED, 100
            elif text.endswith("%"):
                return Entry.Progress.UPDATED, int(text[:-1])
        else:
            raise StoryGraphAPI.Error("No entry progress")


class StoryGraph(StoryGraphAPI):

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def __enter__(self):
        with open(self._path) as fp:
            self._creds = json.load(fp)
        if self._creds.get("cookie"):
            self._session.cookies[self._COOKIE] = self._creds["cookie"]
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._creds["cookie"] = self._session.cookies.get(sg._COOKIE, domain=self.DOMAIN)
        with open(self._path, "w") as fp:
            json.dump(self._creds, fp, indent=2)

    def login(self):
        super().login(self._creds["email"], self._creds["password"])



if __name__ == "__main__":

    import os.path

    logging.basicConfig(level=logging.DEBUG)

    with StoryGraph(os.path.expanduser("~/.storygraph")) as sg:
        sg.login()
