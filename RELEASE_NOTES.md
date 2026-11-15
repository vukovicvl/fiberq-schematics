# FiberQ Schematics 1.0.1

Fix the Qt6 compatibility findings reported by the QGIS plugin repository.

- Replace all 31 flat enum aliases with scoped QGIS 4 APIs for GeoPackage
  writes, symbols, labels, previews and drawing exports.
- Remove the obsolete `QFontMetrics.width()` compatibility fallback.
- Add the official QGIS checker to CI, checking the exact release ZIP in the
  upstream QGIS container. Dry-run findings fail the gate even with exit code 0.
- Preserve workspace schema 13, drawing data and stable element/fiber IDs.

Create ODFs, patch panels, cabinets, boxes, closures, cables and active
equipment. Snap and splice fibers, trace connected paths, apply purposes,
and produce optical budgets, reports, legends and PDF/SVG/DXF exports.

Install `fiberq_schematics-1.0.1.zip` using QGIS's **Install from ZIP** page.
The FiberQ toolbar and Vector menu open the designer and local Help/About.

- Author: Vladimir Vukovic — vukovicvl@fiberq.net
- Supported metadata range: QGIS 4.0–4.99; runtime tested on QGIS 4.2.3 / Qt 6.10.2.
- MIT license, retaining the FiberQ CAD MIT notices.
- No external Python packages, accounts or payment are required.
- Website: https://www.fiberq.net/fiberq-schematics/
- Donate: https://github.com/sponsors/vukovicvl

The official checker reproduces the original 31 enum findings and an additional
font-metrics warning on 1.0.0. The corrected ZIP has zero reported Qt6 findings.
Flake8, Bandit and detect-secrets run without suppression configurations.

Repository acceptance is determined by QGIS's server checks and review after
upload; the local reports are not a claim of official approval. Upload 1.0.1
as a new version of the existing FiberQ Schematics plugin.
