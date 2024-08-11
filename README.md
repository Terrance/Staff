# Staff

Unofficial Python library to work with data on [The StoryGraph](https://thestorygraph.com).

_This library relies on interacting with The StoryGraph's website, and is liable to break at any moment!  If you want something more stable, consider [voting for API support on the roadmap](https://roadmap.thestorygraph.com/features/posts/an-api)._

## Basic usage

Create a JSON file containing your login credentials for The StoryGraph, e.g. `.storygraph.json`:

```json
{
  "email": "user@example.com",
  "password": "password"
}
```

Create a client using your credentials file:

```python
from staff import StoryGraph

with StoryGraph(".storygraph.json") as sg:
    latest = next(sg.current_books())
    print(f"I'm reading {latest.title} by {latest.author}")
```

Your credentials file will be updated with a cookie from the website after your first sign-in.
