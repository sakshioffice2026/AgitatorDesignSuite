"""Compatibility launcher for the real-blade FreeCAD generator.

It executes the existing generator unchanged except that the impeller builder
functions are overridden immediately before the original dispatch table is
created. This preserves tank, shaft, baffle, TechDraw, STEP and OBJ behavior
while replacing placeholder blade geometry.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORIGINAL = HERE / "generate_agitator_model.py"

source = ORIGINAL.read_text(encoding="utf-8-sig")
marker = "# IMPELLER TYPE → BUILDER DISPATCH TABLE"
if marker not in source:
    raise RuntimeError("Cannot locate impeller dispatch marker in generator")

real_module = (HERE / "real_impeller_geometry.py").read_text(encoding="utf-8-sig")
needle = marker
injected = (
    "# ----------------------------------------------------------------------\n"
    "# REAL IMPELLER GEOMETRY OVERRIDES\n"
    "# ----------------------------------------------------------------------\n"
    + real_module
    + "\n\n"
)
source = source.replace(needle, injected + needle, 1)

namespace = {
    "__name__": "__main__",
    "__file__": str(ORIGINAL),
    "__package__": None,
}
exec(compile(source, str(ORIGINAL), "exec"), namespace, namespace)
