from typing import Dict, Type, TypeVar

from bs4 import BeautifulSoup, Tag
from requests import Response, Session


_TElement = TypeVar("_TElement", bound="Element")


class StoryGraphError(Exception):
    """
    Base exception for client-related errors.
    """


class StoryGraphAPI:
    """
    Low-level helpers for interacting with The StoryGraph's website.
    """

    DOMAIN = "app.thestorygraph.com"
    """Base domain of the website."""

    COOKIE = "_storygraph_session"
    """Name of the session cookie produced by the website."""

    username: str | None
    """Username of the currently logged-in user."""

    def __init__(self):
        self._session = Session()
        self._csrf_param: str | None = None
        self._csrf_token: str | None = None
        self.username = None

    def request(self, method: str, path: str, **kwargs) -> Response:
        """
        Make a general request to the website.

        Additional arguments are passed directly to `requests.Session.request`.
        """
        return self._session.request(method, f"https://{self.DOMAIN}{path}", **kwargs)

    def get(self, path: str, **kwargs) -> Response:
        """
        Make a `GET` request.
        """
        return self.request("GET", path, **kwargs)

    def post(self, path: str, form: dict | None = None, csrf = False, **kwargs) -> Response:
        """
        Make a `POST` request, optionally with form data or a CSRF token.
        """
        if form:
            kwargs["data"] = form
        if csrf:
            kwargs.setdefault("headers", {})["X-CSRF-Token"] = self.csrf()
        return self.request("POST", path, **kwargs)

    def html(self, resp: Response) -> BeautifulSoup:
        """
        Parse the HTML of a response.

        If a CSRF token is present on the page, it will be captured for future
        form submissions.
        """
        page = BeautifulSoup(resp.text, "html.parser")
        if param := page.find("meta", {"name": "csrf-param"}):
            self._csrf_param = param["content"]
        if token := page.find("meta", {"name": "csrf-token"}):
            self._csrf_token = token["content"]
        return page

    def csrf(self) -> str:
        """
        Retrieve the cached CSRF token if one exists, otherwise fetch a new one
        from the home page.
        """
        if not self._csrf_token:
            self.html(self.get("/"))
        if not self._csrf_token:
            raise StoryGraphError("No CSRF token")
        csrf = self._csrf_token
        self._csrf_token = None
        return csrf

    def method(self, link: Tag) -> Response:
        """
        Execute a [Turbo action][1] defined on an `<a>` link tag.

        [1]: https://turbo.hotwired.dev/handbook/drive
        """
        data = {
            "_method": link["data-method"],
            self._csrf_param: self.csrf(),
        }
        return self.post(link["href"], data)

    def form(self, form: Tag, data: Dict[str, str] | None = None, csrf: bool = False) -> Response:
        """
        Submit a HTML form, combining existing input fields with any custom
        form data, optionally with a CSRF token.
        """
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

    def paged(self, path: str, container: str, model: Type[_TElement], **kwargs):
        """
        Repeatedly follow the next page link when present on a page, and yield
        items as `Element`s as they're found within a container tag.

        Additional arguments are passed to `get()`.
        """
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
        """
        Check if the user is currently logged in, and submit the login form
        with the given credentials if not.
        """
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
    """
    Base class for models backed by a HTML element.
    """

    def __init__(self, sg: StoryGraphAPI, tag: Tag):
        self._sg = sg
        self._tag: Tag = tag
