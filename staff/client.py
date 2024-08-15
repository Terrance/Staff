import json

from .api import StoryGraphAPI
from .models import Book, Entry


class StoryGraph:
    """
    Client to work with data on The StoryGraph.  Use as a context manager to
    handle session management.
    """

    def __init__(self, path: str):
        """
        Initialise with `path` pointing at a writable JSON file, containing
        `email` and `password` fields.  This file will be updated on exit with
        the session cookie, used by subsequent sessions.
        """
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
        """
        Retrieve a single book from a URL (e.g. a book's own page:
        `/books/020df915-11d0-405f-a1e7-dce262b4a255`).
        """
        resp = self._sg.get(path)
        page = self._sg.html(resp)
        return Book(self._sg, page.main)

    def import_book(self, isbn: str):
        """
        Lookup or import a book by its ISBN (10 or 13 characters).
        """
        path = "/import-book-isbn"
        resp = self._sg.get(path)
        page = self._sg.html(resp)
        form = page.main.find("form", action=path)
        resp = self._sg.form(form, {"isbn": isbn})
        page = self._sg.html(resp)
        return Book(self._sg, page.main)

    def browse_books(self, search: str | None = None):
        """
        Search for books by title, author or other metadata, or if no search
        query given then just retrieve popular books.

        This provides a generator which may page forever!  Avoid iterating over
        the result without a suitable `break` statement or other escape hatch.
        """
        return self._sg.paged("/browse", "search-results-books-panes", Book, params={"search_term": search})

    def owned_books(self):
        """
        Retrieve all books marked as owned.

        This produces a generator that pages the user's entire collection.
        """
        return self._sg.paged(f"/owned-books/{self._sg.username}", "owned-books-panes", Book)

    def to_read_books(self):
        """
        Retrieve all books set as "to read".

        This produces a generator that pages the user's entire collection.
        """
        return self._sg.paged(f"/to-read/{self._sg.username}", "to-read-books-panes", Book)

    def current_books(self):
        """
        Retrieve all books set as "currently reading".

        This produces a generator that pages the user's entire collection.
        """
        return self._sg.paged(f"/currently-reading/{self._sg.username}", "read-books-panes", Book)

    def read_books(self):
        """
        Retrieve all books set as "read".

        This produces a generator that pages the user's entire collection.
        """
        return self._sg.paged(f"/books-read/{self._sg.username}", "read-books-panes", Book)

    def journal(self):
        """
        Retrieve all journal entries for read and currently-reading books.

        This produces a generator that pages the user's entire history.
        """
        return self._sg.paged("/journal", "journal-entry-panes", Entry)
