# Extension artwork

Original geometric mark: three signal bars and a forward arrow, in dark green and mint.
It represents moving scanner signals into research and does not use Chartink or OpenAlgo artwork.
The logo is provided under the repository's existing AGPL v3 license.

| Asset | Use |
| --- | --- |
| [Vector original](../../../extensions/chartink/icons/logo.svg) | Editable SVG master |
| [128 × 128 icon](../../../extensions/chartink/icons/icon128.png) | Chrome Web Store icon; 96 × 96 artwork with transparent padding |
| [16](../../../extensions/chartink/icons/icon16.png), [32](../../../extensions/chartink/icons/icon32.png), [48](../../../extensions/chartink/icons/icon48.png) pixel icons | Chrome toolbar, extension management and popup |
| [512 × 512 preview](logo-preview.png) | Inspect the logo at a larger size |
| [440 × 280 promotional tile](promo-440x280.png) | Store's small promotional tile; brand artwork, not a screenshot |

At least one real product screenshot at 1280 × 800 or 640 × 400 is still needed for submission.
Do not present the promotional tile as a working product screenshot. Follow the
[publishing guide](../CHROME_EXTENSION_PUBLISHING.md) for privacy and source-content checks.

Rebuild artwork from the repository root with `python extensions/chartink/artwork/build.py`
(development dependency: Pillow). Rebuild the bundled policy after editing its canonical
Markdown with `python extensions/chartink/artwork/build_privacy.py` (development dependency:
markdown-it-py). These tools are not shipped in the extension runtime; generated files are
kept in source so users and the ZIP packager need no image/rendering dependencies.
