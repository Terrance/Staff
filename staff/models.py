from datetime import date, datetime
from enum import Enum, IntEnum, auto
from functools import cached_property
from typing import Dict, List, Tuple

from bs4 import Tag

from .api import Element, StoryGraphError


def _setter(fn):
    return property(fset=fn)


class Status(Enum):
    """
    Reading state of a book as set by the user.
    """

    NONE = ""
    """Book hasn't been read (the default state)."""

    TO_READ = "to read"
    """Book is in the queue to be read in future."""

    CURRENT = "currently reading"
    """Book is currently being read."""

    READ = "read"
    """Book has been read in the past."""

    DID_NOT_FINISH = "did not finish"
    """Book was started but not completed in the past."""


class Progress(Enum):
    """
    Reading state of an entry.
    """

    STARTED = auto()
    """Book was just started (when set to `Status.CURRENT`)."""

    UPDATED = auto()
    """Book reading progress has been changed."""

    FINISHED = auto()
    """Book was just finished (when set to `Status.READ`)."""

    DID_NOT_FINISH = auto()
    """Book was abandoned (when set to `Status.DID_NOT_FINISH`)."""


class DateAccuracy(IntEnum):
    """
    Indicator of how much of a date is filled in.

    Read dates are presented as a `date` object and an accuracy: the most
    significant field filled in.

        (date(2024, 3, 7), DateAccuracy.DAY)    # 7 March 2024
        (date(2024, 3, 1), DateAccuracy.MONTH)  # March 2024
        (date(2024, 1, 1), DateAccuracy.YEAR)   # 2024
    """

    YEAR = auto()
    """Date is just a year (e.g. 2024)."""

    MONTH = auto()
    """Date is a month and year (e.g. March 2024)."""

    DAY = auto()
    """Date is a day, month and year (e.g. 7 March 2024)."""

    @classmethod
    def wrap(cls, when: date | None | Tuple[date | None, "DateAccuracy"]) -> Tuple[date | None, "DateAccuracy"]:
        """
        Convert a bare date to a date-accuracy tuple with `DAY` accuracy.
        """
        if not when or isinstance(when, date):
            when = (when, DateAccuracy.DAY)
        return when

    @classmethod
    def parse(cls, text: str) -> Tuple[None, None] | Tuple[date, "DateAccuracy"]:
        """
        Convert a textual date of any accuracy to a Python `date` and the
        corresponding `DateAccuracy`.
        """
        if text == "No date":
            return (None, None)
        for pattern, accuracy in (
            ("%d %B %Y", cls.DAY),
            ("%B %Y", cls.MONTH),
            ("%Y", cls.YEAR),
        ):
            try:
                when = datetime.strptime(text, pattern)
            except ValueError:
                continue
            else:
                return (when.date(), accuracy)
        else:
            raise StoryGraphError(f"Can't parse date: {text!r}")

    @classmethod
    def unparse(cls, when: date | None, accuracy: "DateAccuracy") -> str:
        """
        Format a Python `date` and its accuracy back into a textual date.
        """
        if when is None:
            return "No date"
        if accuracy == cls.DAY:
            pattern = "%d %B %Y"
        elif accuracy == cls.MONTH:
            pattern = "%B %Y"
        elif accuracy == cls.YEAR:
            pattern = "%Y"
        return when.strftime(pattern)


class Book(Element):
    """
    Representation of an individual book.
    """

    @property
    def _path(self) -> str:
        for link in self._tag.find_all("a"):
            if link["href"].startswith("/books/"):
                return "/".join(link["href"].split("/", 3)[:3])
        else:
            raise StoryGraphError("No self link")

    @property
    def _id(self) -> str:
        return self._path.rsplit("/", 1)[-1]

    @property
    def _info(self) -> Tag:
        return self._tag.find(class_="book-title-author-and-series")

    @property
    def _title_author_series(self) -> Tuple[str, List[str], str | None, str | None]:
        root: Tag = self._tag.find(class_="book-title-author-and-series")
        title = series = number = None
        authors: List[str] = []
        for link in root.find_all("a"):
            type_ = link["href"].split("/", 2)[1]
            if type_ == "books":
                title = link.text
            elif type_ == "authors":
                authors.append(link.text)
            elif type_ == "series":
                if not series:
                    series = link.text
                elif link.text[0] == "#":
                    number = link.text[1:]
        if not title:
            title = root.h3.find(string=True).strip()
        return (title, authors, series, number)

    @cached_property
    def _editions_page(self):
        return self._sg.html(self._sg.get(f"{self._path}/editions")).main

    @cached_property
    def metadata(self) -> Dict[str, str | None]:
        """
        Edition-specific information, such as format, ISBN and language.
        """
        block: Tag | None = self._tag.find(class_="edition-info")
        if not block:
            block = self._editions_page.find(class_="edition-info")
        data: Dict[str, str | None] = {}
        for line in block.find_all("p"):
            field, value = (node.text.strip() for node in line.children)
            if value in ("None", "Not specified"):
                value = None
            data[field.rstrip(":")] = value
        return data

    @property
    def title(self) -> str:
        """
        Name of the book.
        """
        return self._title_author_series[0]

    @property
    def authors(self) -> List[str]:
        """
        All listed authors or contributors to the book.
        """
        return self._title_author_series[1]

    @property
    def author(self) -> str | None:
        """
        First (primary) author of the book.
        """
        return next(iter(self.authors), None)

    @property
    def series(self) -> Tuple[str | None, str | None]:
        """
        Tuple of series name and position in that series.
        """
        return self._title_author_series[2:]

    @property
    def pages(self) -> int | None:
        """
        Number of pages in the book.
        """
        for text in self._tag.find_all(string=True):
            text: str
            parts = text.split()
            if len(parts) == 2 and parts[1] == "pages" and parts[0].isdigit():
                return int(parts[0])
        return None

    @property
    def status(self) -> Status:
        """
        Reading state of the book.

        This is a writable field which sets the new status, generating any
        corresponding journal entries and updating any read-throughs.
        """
        label = self._tag.find(class_="read-status-label")
        return Status(label.text) if label else Status.NONE

    @status.setter
    def status(self, new: Status):
        if self.status == new:
            return
        for form in self._tag.find_all("form"):
            if new is Status.NONE:
                if "/remove-book/" in form["action"]:
                    break
            else:
                if "/update-status" in form["action"] and ("=" + new.value.replace(" ", "-")) in form["action"]:
                    break
        else:
            raise StoryGraphError("No update status form")
        self._sg.form(form)
        self._reload()

    @property
    def owned(self) -> bool:
        """
        Whether this book has been marked as owned.

        This is a writable field which can toggle the owned status.
        """
        return self._tag.find(class_="remove-from-owned-link") is not None

    @owned.setter
    def owned(self, owned: bool):
        class_ = "mark-as-owned-link" if owned else "remove-from-owned-link"
        link: Tag | None = self._tag.find(class_=class_)
        if not link:
            return
        self._sg.method(link)
        self._reload()

    def _update_progress(self, unit: str, value: int):
        form: Tag = self._tag.find("form", action="/update-progress")
        data = {
            "read_status[progress_number]": str(value),
            "read_status[progress_type]": unit,
        }
        self._sg.form(form, data, True)

    @_setter
    def pages_read(self, pages: int):
        """
        Page reached in the current read-through.

        This is a write-only field which will create a new journal entry.
        """
        self._update_progress("pages", pages)

    @_setter
    def percent_read(self, percent: int):
        """
        Percentage of the book completed in the current read-through.

        This is a write-only field which will create a new journal entry.
        """
        self._update_progress("percentage", percent)

    @cached_property
    def _reads_page(self) -> Tag:
        return self._sg.html(self._sg.get(f"/read_instances/new?book_id={self._id}")).main

    def reads(self) -> List["Read"]:
        """
        Current and any previous read-throughs of the book.
        """
        panel: Tag = self._reads_page.find(id="reading-summary")
        reads: List[Read] = []
        for row in panel.find_all("p", recursive=False):
            if row.find(class_="edit-read-instance"):
                reads.append(Read(self._sg, row))
        return reads

    def other_editions(self):
        """
        Retrieve all known editions of this books.

        This produces a generator that pages the book's entire edition list.
        """
        return self._sg.paged(f"{self._path}/editions", "search-results-books-panes", Book)

    def _reload(self):
        resp = self._sg.get(self._path)
        page = self._sg.html(resp)
        self._tag = page.main

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.author!r} {self.title!r}>"


class Read(Element):
    """
    Representation of a single read-through of a book.
    """

    @property
    def _start_end(self) -> Tuple[str, str]:
        for text in self._tag.find_all(string=True):
            if " to " in text:
                return tuple(text.strip().split(" to ", 1))
        else:
            raise StoryGraphError("No read dates")

    @property
    def start(self) -> Tuple[None, None] | Tuple[date, DateAccuracy]:
        """
        Date the read was started.

        This is a writable field which calls `edit()` with the new start date,
        as either a date/accuracy tuple or just the date (`DateAccuracy.DAY` is
        assumed).
        """
        return DateAccuracy.parse(self._start_end[0])

    @start.setter
    def start(self, start: date | None | Tuple[date | None, DateAccuracy]):
        when, accuracy = DateAccuracy.wrap(start)
        self.edit(start=when, start_accuracy=accuracy)

    @property
    def end(self) -> Tuple[None, None] | Tuple[date, DateAccuracy]:
        """
        Date the read was either finished or abandoned.

        This is a writable field which calls `edit()` with the new end date, as
        either a date/accuracy tuple or just the date (`DateAccuracy.DAY` is
        assumed).
        """
        return DateAccuracy.parse(self._start_end[1])

    @end.setter
    def end(self, end: date | None | Tuple[date | None, DateAccuracy]):
        when, accuracy = DateAccuracy.wrap(end)
        self.edit(end=when, end_accuracy=accuracy)

    def edit(
        self,
        start: date | None = None,
        start_accuracy: DateAccuracy = DateAccuracy.DAY,
        end: date | None = None,
        end_accuracy: DateAccuracy = DateAccuracy.DAY,
    ):
        """
        Change the start and/or end date of the read-through.
        """
        link: Tag = self._tag.find("a", {"data-method": "get"})
        panel = self._sg.html(self._sg.method(link))
        form: Tag = panel.find("form")
        data = {}
        if start:
            for part in DateAccuracy:
                field = part.name.lower()
                value = getattr(start, field) if start_accuracy >= part else ""
                data[f"read_instance[start_{field}]"] = value
        if end:
            for part in DateAccuracy:
                field = part.name.lower()
                value = getattr(end, field) if end_accuracy >= part else ""
                data[f"read_instance[{field}]"] = value
        self._sg.form(form, data, True)

    def delete(self):
        """
        Delete this read-through.
        """
        link: Tag = self._tag.find("a", {"data-method": "delete"})
        self._sg.method(link)

    def __repr__(self):
        return f"<{self.__class__.__name__}: {DateAccuracy.unparse(*self.start)} -> {DateAccuracy.unparse(*self.end)}>"


class Entry(Element):
    """
    Representation of a single journal entry within a read-through.
    """

    @property
    def _date_title_progress(self) -> Tuple[Tag, Tag, Tag]:
        right = self._tag.find_all(recursive=False)[1]
        return tuple(right.find_all(recursive=False)[:3])

    @property
    def _title(self) -> Tag:
        return self._date_title_progress[1].a

    @property
    def _edit_link(self) -> str:
        for link in self._date_title_progress[0].find_all("a"):
            if link["href"].startswith("/journal_entries/"):
                return link["href"]
        else:
            raise StoryGraphError("No entry edit page")

    @cached_property
    def _edit_page(self) -> Tag:
        return self._sg.html(self._sg.get(self._edit_link)).main

    @property
    def when(self) -> Tuple[None, None] | Tuple[date, DateAccuracy]:
        """
        Date when this entry applies.

        This is a writable field which calls `edit()` with the new date, as
        either a date/accuracy tuple or just the date (`DateAccuracy.DAY` is
        assumed).
        """
        for text in self._date_title_progress[0].find_all(string=True):
            try:
                return DateAccuracy.parse(text)
            except StoryGraphError:
                pass
        else:
            raise StoryGraphError("No entry date")

    @when.setter
    def when(self, when: date | None | Tuple[date | None, DateAccuracy]):
        self.edit(*DateAccuracy.wrap(when))

    @property
    def title(self) -> str:
        """
        Name of the book.
        """
        return self._title.text

    @property
    def author(self) -> str:
        """
        First (primary) author of the book.
        """
        prefix = f"{self.title} by "
        combined = self._tag.img["alt"]
        if not combined.startswith(prefix):
            raise StoryGraphError("Can't derive author")
        return combined[len(prefix):]

    @property
    def _progress_percent(self) -> Tuple[Progress, int]:
        progress = Progress.UPDATED
        for text in self._date_title_progress[2].find_all(string=True):
            if "Started" in text:
                return Progress.STARTED, 0
            elif "Finished" in text:
                return Progress.FINISHED, 100
            elif "Did not finish" in text:
                progress = Progress.DID_NOT_FINISH
            elif text.endswith("%"):
                return progress, int(text[:-1])
        else:
            raise StoryGraphError("No entry progress")

    @property
    def progress(self) -> Progress:
        """
        Reading state of the entry.
        """
        return self._progress_percent[0]

    @property
    def progress_percent(self) -> int:
        """
        Percentage of the book completed at the time of this entry.
        """
        return self._progress_percent[1]

    def _edit_input(self, name: str) -> int:
        return int(self._edit_page.find("input", {"name": name})["value"])

    @property
    def pages(self) -> int:
        """
        Page reached at the time of this entry.

        This is a writable field which calls `edit()` with the new page.
        """
        return self._edit_input("journal_entry[pages_read]")

    @pages.setter
    def pages(self, pages: int):
        self.edit(pages=pages)

    @property
    def pages_total(self) -> int:
        """
        Number of pages in the book.

        This is a writable field which calls `edit()` with the new total.
        """
        return self._edit_input("journal_entry[pages_read_total]")

    @pages_total.setter
    def pages_total(self, pages_total: int):
        self.edit(pages_total=pages_total)

    @property
    def percent(self) -> int:
        """
        Percentage of the book completed at the time of this entry.

        This is a writable field which calls `edit()` with the new percentage.
        """
        return self._edit_input("journal_entry[percent_reached]")

    @percent.setter
    def percent(self, percent: int):
        self.edit(percent=percent)

    def get_book(self) -> Book:
        """
        Retrieve the book being read from this entry.
        """
        resp = self._sg.get(self._title["href"])
        page = self._sg.html(resp)
        return Book(self._sg, page.main)

    def edit(
        self,
        when: date | None = None,
        accuracy: DateAccuracy = DateAccuracy.DAY,
        percent: int | None = None,
        pages: int | None = None,
        pages_total: int | None = None
    ):
        """
        Change the date or progress in this entry.
        """
        form: Tag = self._edit_page.find("form", {"class": "edit_journal_entry"})
        data: Dict[str, str] = {}
        if when:
            for part in DateAccuracy:
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
        self._reload()

    def delete(self):
        """
        Delete this entry.
        """
        for link in self._edit_page.find_all("a"):
            if link.get("data-method") == "delete" and link["href"].startswith("/journal_entries/"):
                self._sg.method(link)
                return
        else:
            raise StoryGraphError("No delete link")

    def _reload(self):
        del self._edit_page

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.title!r} {self.progress.name} {self.progress_percent}%>"
