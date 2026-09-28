"""Stage the standalone HTML guides with links suitable for GitHub Pages."""

import argparse
import html
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
HREF = re.compile(r"\bhref=([\"'])(.*?)\1", re.IGNORECASE)


def publish_html(page: Path, repository_url: str) -> str:
    def rewrite_link(match: re.Match[str]) -> str:
        link = urlsplit(html.unescape(match[2]))
        if link.scheme or link.netloc or not link.path:
            return match[0]

        target = (page.parent / unquote(link.path)).resolve()
        if target == ROOT / "README.md" and not link.fragment and not link.query:
            href = "index.html"
        elif target.suffix in {".md", ".py", ".mmd"}:
            relative_path = target.relative_to(ROOT).as_posix()
            href = urlunsplit(urlsplit(
                f"{repository_url}/{quote(relative_path)}"
            )._replace(query=link.query, fragment=link.fragment))
        else:
            return match[0]
        return f'href="{html.escape(href, quote=True)}"'

    return HREF.sub(rewrite_link, page.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "danballance/pyarchgraph")
    revision = os.environ.get("GITHUB_SHA", "main")
    repository_url = f"{server}/{repository}/blob/{quote(revision, safe='')}"

    if not (DOCS / "index.html").is_file():
        parser.error("docs/index.html is required as the site entry point")
    # A new directory prevents stale files from leaking into the artifact.
    args.output.mkdir(parents=True, exist_ok=False)
    for page in sorted(DOCS.glob("*.html")):
        (args.output / page.name).write_text(
            publish_html(page, repository_url), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
