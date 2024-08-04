#!/usr/bin/env python3

import json
import logging

from bs4 import BeautifulSoup
import requests


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
