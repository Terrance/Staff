#!/usr/bin/env python3

from datetime import date, datetime
from enum import Enum, IntEnum, auto
from functools import cached_property
import json
import logging
from typing import Any, Generator, TypeVar

from bs4 import BeautifulSoup, Tag
import requests


_TElement = TypeVar("_TElement", bound="Element")

LOG = logging.getLogger(__name__)


def _setter(fn):
    return property(fset=fn)


class StoryGraphAPI:

    DOMAIN = "app.thestorygraph.com"

    COOKIE = "_storygraph_session"

    class Error(Exception):
        pass

    def __init__(self):
        self._session = requests.Session()
        self._csrf_param: str | None = None
        self._csrf_token: str | None = None
        self.username = None

    def request(self, method: str, path: str, **kwargs):
        return self._session.request(method, f"https://{self.DOMAIN}{path}", **kwargs)

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, form: dict | None = None, csrf = False, **kwargs):
        if form:
            kwargs["data"] = form
        if csrf:
            kwargs.setdefault("headers", {})["X-CSRF-Token"] = self.csrf()
        return self.request("POST", path, **kwargs)

    def html(self, resp: requests.Response):
        page = BeautifulSoup(resp.text, "html.parser")
        if param := page.find("meta", {"name": "csrf-param"}):
            self._csrf_param = param["content"]
        if token := page.find("meta", {"name": "csrf-token"}):
            self._csrf_token = token["content"]
        return page

    def csrf(self):
        if not self._csrf_token:
            self.get("/")
        if not self._csrf_token:
            raise self.Error("No CSRF token")
        csrf = self._csrf_token
        self._csrf_token = None
        return csrf

    def method(self, link: Tag):
        data = {
            "_method": link["data-method"],
            self._csrf_param: self.csrf(),
        }
        return self.post(link["href"], data)

    def form(self, form: Tag, data: dict[str, str] | None = None, csrf: bool = False):
        if not data:
            data = {}
        for type_ in ("input", "select", "button"):
            for field in form.find_all(type_, {"name": True}):
                name: str = field["name"]
                value: str
                if type_ == "select":
                    option = field.find("option", selected=True)
                    if not option:
                        continue
                    value = option.get("value", "")
                else:
                    value = field.get("value", "")
                data.setdefault(name, value)
        return self.post(form["action"], data, csrf)

    def paged(self, path: str, container: str, model: type["_TElement"], **kwargs) -> Generator[_TElement, Any, None]:
        while True:
            page = self.html(self.get(path, **kwargs))
            root = page.find(class_=container)
            if not root:
                break
            for tag in root.find_all("div", recursive=False):
                yield model(self, tag)
            more = page.find(id="next_link")
            if not isinstance(more, Tag):
                break
            path = more["href"]

    def login(self, email: str, password: str):
        target = "/users/sign_in"
        resp = self.get(target)
        page = self.html(resp)
        if resp.url.endswith(target):
            form: Tag = page.find("form", action=target)
            data = {
                "user[email]": email,
                "user[password]": password,
            }
            page = self.html(self.form(form, data))
        for link in page.nav.find_all("a"):
            if link["href"].startswith("/profile/"):
                self.username = link["href"].rsplit("/", 1)[1]
                LOG.info("Logged in as %s", self.username)
                break
        else:
            raise self.Error("No username")

    def get_book(self, path: str):
        resp = self.get(path)
        page = self.html(resp)
        return Book(self, page.main)


class Element:

    path: str

    def __init__(self, sg: StoryGraphAPI, tag: Tag):
        self._sg = sg
        self._tag: Tag = tag

    def reload(self):
        resp = self._sg.get(self.path)
        page = self._sg.html(resp)
        self._tag = page.main


class Book(Element):

    class Status(Enum):
        NONE = ""
        TO_READ = "to read"
        CURRENT = "currently reading"
        READ = "read"
        DID_NOT_FINISH = "did not finish"

    @property
    def path(self):
        for link in self._tag.find_all("a"):
            if link["href"].startswith("/books/"):
                return "/".join(link["href"].split("/", 3)[:3])
        else:
            raise StoryGraphAPI.Error("No self link")

    @property
    def _info(self) -> Tag:
        return self._tag.find(class_="book-title-author-and-series")

    @property
    def _title_author_series(self) -> tuple[str, str, str | None, str | None]:
        root: Tag = self._tag.find(class_="book-title-author-and-series")
        title = author = series = number = None
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
        if not title:
            title = root.h3.find(string=True).strip()
        return (title, author, series, number)

    @property
    def title(self) -> str:
        return self._title_author_series[0]

    @property
    def author(self) -> str | None:
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
        return self.Status(label.text) if label else self.Status.NONE

    @status.setter
    def status(self, new: Status):
        if self.status == new:
            return
        for form in self._tag.find_all("form"):
            if new is self.Status.NONE:
                if "/remove-book/" in form["action"]:
                    break
            else:
                if "/update-status" in form["action"] and ("=" + new.value.replace(" ", "-")) in form["action"]:
                    break
        else:
            raise StoryGraphAPI.Error("No update status form")
        self._sg.form(form)
        self.reload()

    @property
    def owned(self) -> bool:
        return self._tag.find(class_="remove-from-owned-link") is not None

    @owned.setter
    def owned(self, owned: bool):
        class_ = "mark-as-owned-link" if owned else "remove-from-owned-link"
        link: Tag | None = self._tag.find(class_=class_)
        if not link:
            return
        self._sg.method(link)
        self.reload()

    def _update_progress(self, unit: str, value: int):
        form: Tag = self._tag.find("form", action="/update-progress")
        data = {
            "read_status[progress_number]": str(value),
            "read_status[progress_type]": unit,
        }
        self._sg.form(form, data, True)

    @_setter
    def pages_read(self, pages: int):
        self._update_progress("pages", pages)

    @_setter
    def percent_read(self, percent: int):
        self._update_progress("percentage", percent)

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.author!r} {self.title!r}>"


class Entry(Element):

    class Progress(Enum):
        STARTED = auto()
        UPDATED = auto()
        FINISHED = auto()

    class DateAccuracy(IntEnum):
        YEAR = auto()
        MONTH = auto()
        DAY = auto()

    _DATES = (
        ("%d %B %Y", DateAccuracy.DAY),
        ("%B %Y", DateAccuracy.MONTH),
        ("%Y", DateAccuracy.YEAR),
    )

    @property
    def _date_title_progress(self) -> tuple[Tag, Tag, Tag]:
        right = self._tag.find_all(recursive=False)[1]
        return tuple(right.find_all(recursive=False)[:3])

    @property
    def _title(self) -> Tag:
        return self._date_title_progress[1].a

    @cached_property
    def _edit_link(self) -> str:
        for link in self._date_title_progress[0].find_all("a"):
            if link["href"].startswith("/journal_entries/"):
                return link["href"]
        else:
            raise StoryGraphAPI.Error("No entry edit page")

    @cached_property
    def _edit_page(self) -> Tag:
        return self._sg.html(self._sg.get(self._edit_link)).main

    @property
    def when(self) -> tuple[date | None, DateAccuracy | None]:
        for text in self._date_title_progress[0].find_all(string=True):
            if "No date" in text:
                return (None, None)
            for fmt, accuracy in self._DATES:
                try:
                    return datetime.strptime(text.strip(), fmt).date(), accuracy
                except ValueError:
                    pass
        else:
            raise StoryGraphAPI.Error("No entry date")

    @when.setter
    def when(self, when: date | tuple[date, DateAccuracy]):
        if isinstance(when, date):
            when = (when, self.DateAccuracy.DAY)
        self.edit(when=when[0], accuracy=when[1])

    @property
    def title(self) -> str:
        return self._title.text

    @property
    def author(self) -> str:
        prefix = f"{self.title} by "
        combined = self._tag.img["alt"]
        if not combined.startswith(prefix):
            raise StoryGraphAPI.Error("Can't derive author")
        return combined[len(prefix):]

    @property
    def progress(self) -> tuple[Progress, int]:
        for text in self._date_title_progress[2].find_all(string=True):
            if "Started" in text:
                return Entry.Progress.STARTED, 0
            elif "Finished" in text:
                return Entry.Progress.FINISHED, 100
            elif text.endswith("%"):
                return Entry.Progress.UPDATED, int(text[:-1])
        else:
            raise StoryGraphAPI.Error("No entry progress")

    def _edit_input(self, name: str) -> int:
        return int(self._edit_page.find("input", {"name": name})["value"])

    @property
    def pages(self):
        return self._edit_input("journal_entry[pages_read]")

    @pages.setter
    def pages(self, pages: int):
        self.edit(pages=pages)

    @property
    def pages_total(self):
        return self._edit_input("journal_entry[pages_read_total]")

    @pages_total.setter
    def pages_total(self, pages_total: int):
        self.edit(pages_total=pages_total)

    @property
    def percent(self):
        return self._edit_input("journal_entry[percent_reached]")

    @percent.setter
    def percent(self, percent: int):
        self.edit(percent=percent)

    def get_book(self):
        return self._sg.get_book(self._title["href"])

    def edit(
        self,
        when: date | None = None,
        accuracy: DateAccuracy = DateAccuracy.DAY,
        percent: int | None = None,
        pages: int | None = None,
        pages_total: int | None = None
    ):
        form: Tag = self._edit_page.find("form", {"class": "edit_journal_entry"})
        data: dict[str, str] = {}
        if when:
            for part in self.DateAccuracy:
                field = part.name.lower()
                value = getattr(when, field) if accuracy >= part else ""
                data[f"journal_entry[{field}]"] = value
        if pages is not None:
            data["journal_entry[pages_read]"] = str(pages)
        if pages_total is not None:
            data["journal_entry[pages_read_total]"] = str(pages_total)
        if percent is not None:
            data["journal_entry[percent_reached]"] = str(percent)
        self._sg.form(form, data)
        self.reload()

    def delete(self):
        for link in self._edit_page.find_all("a"):
            if link.get("data-method") == "delete" and link["href"].startswith("/journal_entries/"):
                self._sg.method(link)
                return
        else:
            raise StoryGraphAPI.Error("No delete link")

    def reload(self):
        del self._edit_page

    def __repr__(self):
        progress, percent = self.progress
        return f"<{self.__class__.__name__}: {self.title!r} {progress.name} {percent}%>"


class StoryGraph:

    def __init__(self, path: str):
        self._path = path
        self._sg = StoryGraphAPI()

    def __enter__(self):
        with open(self._path) as fp:
            self._creds = json.load(fp)
        if self._creds.get("cookie"):
            self._sg._session.cookies[self._sg.COOKIE] = self._creds["cookie"]
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._creds["cookie"] = self._sg._session.cookies.get(self._sg.COOKIE, domain=self._sg.DOMAIN)
        with open(self._path, "w") as fp:
            json.dump(self._creds, fp, indent=2)

    def login(self):
        self._sg.login(self._creds["email"], self._creds["password"])

    def get_book(self, path: str):
        return self._sg.get_book(path)

    def browse_books(self, search: str | None = None):
        return self._sg.paged("/browse", "search-results-books-panes", Book, params={"search_term": search})

    def owned_books(self):
        return self._sg.paged(f"/owned-books/{self._sg.username}", "owned-books-panes", Book)

    def to_read_books(self):
        return self._sg.paged(f"/to-read/{self._sg.username}", "to-read-books-panes", Book)

    def current_books(self):
        return self._sg.paged(f"/currently-reading/{self._sg.username}", "read-books-panes", Book)

    def read_books(self):
        return self._sg.paged(f"/books-read/{self._sg.username}", "read-books-panes", Book)

    def journal(self):
        return self._sg.paged("/journal", "journal-entry-panes", Entry)


if __name__ == "__main__":

    import os.path

    logging.basicConfig(level=logging.DEBUG)

    with StoryGraph(os.path.expanduser("~/.storygraph")) as sg:
        sg.login()
