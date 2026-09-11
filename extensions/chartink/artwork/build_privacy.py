"""Render the bundled policy from its canonical Markdown. Dev dependency: markdown-it-py."""

from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parents[1] / "docs/research/CHARTINK_PRIVACY.md"


def main():
    content = MarkdownIt("commonmark", {"html": False}).render(SOURCE.read_text(encoding="utf-8"))
    (ROOT / "privacy.html").write_text(
        "<!doctype html>\n"
        "<!-- Generated from docs/research/CHARTINK_PRIVACY.md by artwork/build_privacy.py. -->\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Privacy · OpenAlgo Research — Chartink</title>"
        '<link rel="stylesheet" href="privacy.css"></head><body><main>'
        + content
        + "</main></body></html>\n",
        encoding="utf-8",
    )
    print("Built bundled privacy page from the public policy.")


if __name__ == "__main__":
    main()
