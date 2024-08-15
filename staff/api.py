from typing import Any, Generator, TypeVar

from bs4 import BeautifulSoup, Tag
import requests


_TElement = TypeVar("_TElement", bound="Element")


class StoryGraphError(Exception):
    pass


class StoryGraphAPI:

    DOMAIN = "app.thestorygraph.com"

    COOKIE = "_storygraph_session"

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
            self.html(self.get("/"))
        if not self._csrf_token:
            raise StoryGraphError("No CSRF token")
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

    def paged(self, path: str, container: str, model: type[_TElement], **kwargs) -> Generator[_TElement, Any, None]:
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
                break
        else:
            raise StoryGraphError("No username")


class Element:

    path: str

    def __init__(self, sg: StoryGraphAPI, tag: Tag):
        self._sg = sg
        self._tag: Tag = tag

    def reload(self):
        resp = self._sg.get(self.path)
        page = self._sg.html(resp)
        self._tag = page.main
