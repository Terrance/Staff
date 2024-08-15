import json

from .api import StoryGraphAPI
from .models import Book, Entry


class StoryGraph:

    def __init__(self, path: str):
        self._path = path
        self._sg = StoryGraphAPI()

    def __enter__(self):
        with open(self._path) as fp:
            self._creds = json.load(fp)
        if self._creds.get("cookie"):
            self._sg._session.cookies[self._sg.COOKIE] = self._creds["cookie"]
        self._sg.login(self._creds["email"], self._creds["password"])
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._creds["cookie"] = self._sg._session.cookies.get(self._sg.COOKIE, domain=self._sg.DOMAIN)
        with open(self._path, "w") as fp:
            json.dump(self._creds, fp, indent=2)

    def get_book(self, path: str):
        resp = self._sg.get(path)
        page = self._sg.html(resp)
        return Book(self._sg, page.main)

    def import_book(self, isbn: str):
        path = "/import-book-isbn"
        resp = self._sg.get(path)
        page = self._sg.html(resp)
        form = page.main.find("form", action=path)
        resp = self._sg.form(form, {"isbn": isbn})
        page = self._sg.html(resp)
        return Book(self._sg, page.main)

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
