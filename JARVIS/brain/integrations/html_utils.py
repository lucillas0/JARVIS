"""Small HTML-to-text parser used for Moodle pages."""
from html.parser import HTMLParser


_IGNORED_TAGS = {"script", "style", "noscript"}
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
    "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
    "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol",
    "p", "pre", "section", "table", "tbody", "td", "th", "thead", "tr", "ul",
}
_CONTENT_CLASSES = {"activity-description", "text_to_print"}
_CONTENT_IDS = {"intro", "region-main"}


class _MoodleHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.markers = []
        self._length = 0
        self._ignored = []

    def _append(self, value):
        self.parts.append(value)
        self._length += len(value)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self._ignored:
            if tag in _IGNORED_TAGS:
                self._ignored.append(tag)
            return
        if tag in _IGNORED_TAGS:
            self._ignored.append(tag)
            return

        attributes = {name.lower(): (value or "") for name, value in attrs}
        classes = set(attributes.get("class", "").split())
        if (classes & _CONTENT_CLASSES
                or attributes.get("itemprop", "").lower() == "description"
                or attributes.get("id", "").lower() in _CONTENT_IDS
                or attributes.get("data-region", "").lower() == "content"
                or attributes.get("role", "").lower() == "main"):
            self.markers.append(self._length)
        if tag in _BLOCK_TAGS:
            self._append("\n")
        if tag == "li":
            self._append("- ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._ignored:
            if tag == self._ignored[-1]:
                self._ignored.pop()
            return
        if tag in _BLOCK_TAGS:
            self._append("\n")

    def handle_data(self, data):
        if not self._ignored:
            self._append(data)


def html_text_and_markers(html):
    """Parse HTML without executable/style contents and return text/offsets."""
    parser = _MoodleHTMLParser()
    parser.feed(html or "")
    parser.close()
    return "".join(parser.parts), parser.markers


def html_text(html):
    return html_text_and_markers(html)[0]
