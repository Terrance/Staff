from datetime import date, datetime
from enum import Enum, IntEnum, auto
from functools import cached_property

from bs4 import Tag

from .api import Element, StoryGraphError


def _setter(fn):
    return property(fset=fn)


class Status(Enum):
    NONE = ""
    TO_READ = "to read"
    CURRENT = "currently reading"
    READ = "read"
    DID_NOT_FINISH = "did not finish"


class Progress(Enum):
    STARTED = auto()
    UPDATED = auto()
    FINISHED = auto()


class DateAccuracy(IntEnum):
    YEAR = auto()
    MONTH = auto()
    DAY = auto()


class Book(Element):

    @property
    def path(self):
        for link in self._tag.find_all("a"):
            if link["href"].startswith("/books/"):
                return "/".join(link["href"].split("/", 3)[:3])
        else:
            raise StoryGraphError("No self link")

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

    @cached_property
    def _editions_page(self):
        return self._sg.html(self._sg.get(f"{self.path}/editions")).main

    @cached_property
    def metadata(self):
        block: Tag | None = self._tag.find(class_="edition-info")
        if not block:
            block = self._editions_page.find(class_="edition-info")
        data: dict[str, str | None] = {}
        for line in block.find_all("p"):
            field, value = (node.text.strip() for node in line.children)
            if value in ("None", "Not specified"):
                value = None
            data[field.rstrip(":")] = value
        return data

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

    def other_editions(self):
        return self._sg.paged(f"{self.path}/editions", "search-results-books-panes", Book)

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.author!r} {self.title!r}>"


class Entry(Element):

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
            raise StoryGraphError("No entry date")

    @when.setter
    def when(self, when: date | tuple[date, DateAccuracy]):
        if isinstance(when, date):
            when = (when, DateAccuracy.DAY)
        self.edit(when=when[0], accuracy=when[1])

    @property
    def title(self) -> str:
        return self._title.text

    @property
    def author(self) -> str:
        prefix = f"{self.title} by "
        combined = self._tag.img["alt"]
        if not combined.startswith(prefix):
            raise StoryGraphError("Can't derive author")
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
            raise StoryGraphError("No entry progress")

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
        form: Tag = self._edit_page.find("form", {"class": "edit_journal_entry"})
        data: dict[str, str] = {}
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
        self.reload()

    def delete(self):
        for link in self._edit_page.find_all("a"):
            if link.get("data-method") == "delete" and link["href"].startswith("/journal_entries/"):
                self._sg.method(link)
                return
        else:
            raise StoryGraphError("No delete link")

    def reload(self):
        del self._edit_page

    def __repr__(self):
        progress, percent = self.progress
        return f"<{self.__class__.__name__}: {self.title!r} {progress.name} {percent}%>"
