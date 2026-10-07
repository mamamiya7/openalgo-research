# Reviewed source catalog

These two metadata manifests identify the public source files already pinned by
`research/evidence_import.py`. They contain dates, official URLs, lengths and
hashes, not candle records or original ZIP/PDF binaries. Preserve their exact
bytes: the importer checks the original manifest SHA-256 values.

Use `tools/research_evidence.py` to install and verify the separately stored
baseline. Updating these catalogs requires the importer's normal evidence review;
downloading a newer file does not make it an accepted replacement.
