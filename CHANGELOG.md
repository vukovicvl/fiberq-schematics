# Changelog

## 1.0.1 — 2026-11-15

- Replace all 31 flat QGIS enum aliases reported by the repository Qt6 check
  with scoped QGIS 4 APIs for GeoPackage writing, rendering, previews and exports.
- Remove the obsolete `QFontMetrics.width()` fallback.
- Run the official QGIS Qt6 checker against the exact release ZIP in CI and
  fail on reported findings, including dry-run warnings with exit status zero.

## 1.0.0 — 2026-11-15

- First packaged QGIS release as **FiberQ Schematics**.
- Native QGIS toolbar, Vector menu and Help/About with support and donation links.
- Equipment families, native cable catalogues and modular housing attachments.
- Fiber snapping, Cut/Through, Join/Group join, splice points, branches and slack.
- Connected-path selection, purpose styling and optical tracing.
- Splitters, OLT/ONT equipment, route reports, checks and optical budgets.
- Legends, English/Serbian settings, PDF/SVG/DXF export and GeoPackage persistence.
- Safe migration of earlier workspaces, preserving original files and IDs.
