# Vendored front-end libraries

Bundled locally (no CDN) so the UI works offline and behind Home Assistant
Ingress. When updating a file, replace it with the official release build and
update its version and SHA-256 below (`sha256sum` / `shasum -a 256`);
`tests/test_webui_shared.py` checks that the hashes match.

| Library | Version | Source |
|---|---|---|
| Leaflet | 1.9.4 | https://github.com/Leaflet/Leaflet/releases/tag/v1.9.4 (`dist/`) |
| Leaflet.markercluster | 1.5.x (no version string in the minified file; contains the 1.5.0+ options `clusterPane`/`spiderfyShapePositions` — most likely 1.5.3, verify against the hash below) | https://github.com/Leaflet/Leaflet.markercluster/releases (`dist/`) |

## SHA-256

| File | Library | SHA-256 |
|---|---|---|
| `leaflet.js` | Leaflet 1.9.4 | `db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a` |
| `leaflet.css` | Leaflet 1.9.4 | `337bfca5cabd03b39815b2700febe2b3b7edf55921c59cd49f88ecb328212303` |
| `images/layers.png` | Leaflet 1.9.4 | `1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6` |
| `images/layers-2x.png` | Leaflet 1.9.4 | `066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf` |
| `images/marker-icon.png` | Leaflet 1.9.4 | `574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437` |
| `images/marker-icon-2x.png` | Leaflet 1.9.4 | `00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528` |
| `images/marker-shadow.png` | Leaflet 1.9.4 | `264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da` |
| `leaflet.markercluster.js` | Leaflet.markercluster 1.5.x | `1e4e1d22972a3926f48598e0caf14e3fe7049835d428a344fed4f9e3665b3508` |
| `MarkerCluster.css` | Leaflet.markercluster 1.5.x | `614dea0a98ff3f4ead74f04918f6b1d1b9ba435c25b5fc23b21a394d1e3e4d87` |
| `MarkerCluster.Default.css` | Leaflet.markercluster 1.5.x | `61258232d98d64dc2a7b1e02130d67421bc5b9bda5994eef70228ff97570c170` |
