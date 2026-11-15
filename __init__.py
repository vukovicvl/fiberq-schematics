"""FiberQ Schematics QGIS plugin entry point."""


def classFactory(iface):
    """Create the plugin instance requested by QGIS."""
    from .plugin import FiberQSchematicsPlugin

    return FiberQSchematicsPlugin(iface)
