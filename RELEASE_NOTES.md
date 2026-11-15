# FiberQ Schematics 1.0.0

First QGIS plugin release of the independent FiberQ schematic designer.

Create ODFs, patch panels, cabinets, boxes, closures, cables and active
equipment. Snap and splice fibers, trace connected paths, apply purposes,
and produce optical budgets, reports, legends and PDF/SVG/DXF exports.

Install `fiberq_schematics-1.0.0.zip` using QGIS's **Install from ZIP** page.
The FiberQ toolbar and Vector menu open the designer and local Help/About.

- Author: Vladimir Vukovic — vukovicvl@fiberq.net
- Supported metadata range: QGIS 4.0–4.99; runtime tested on QGIS 4.2.3 / Qt 6.10.2.
- MIT license, retaining the FiberQ CAD MIT notices.
- No external Python packages, accounts or payment are required.
- Website: https://www.fiberq.net/fiberq-schematics/
- Donate: https://github.com/sponsors/vukovicvl

Local release checks: zero Flake8, Bandit and detect-secrets findings;
package structure, file permissions and reproducible builds pass.
All 252 model/QGIS/packaging cases are verified, with no failures or skips.
Actual QGIS plugin-manager installation and repeated enable/disable cycles
preserve saved work and remove owned UI/tools correctly.

Repository acceptance is determined by QGIS's server checks and review after
upload; the local reports are not a claim of official approval.
