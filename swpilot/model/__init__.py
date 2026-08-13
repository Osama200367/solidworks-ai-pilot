"""Shared geometric model layer (the "digital twin").

Pure Python, no COM. The executor maintains a :class:`ModelTracker` for
every backend: it validates commands, derives 3D faces/edges for the
solids being built, and resolves declarative selectors into concrete
pick coordinates — with identical results whether the backend is the
mock logger or real SolidWorks.
"""
