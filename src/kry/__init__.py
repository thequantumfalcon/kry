"""KRY — proof-of-efficiency compute credit."""

# A literal, not an importlib.metadata lookup: `kry` is routinely imported straight from a
# source checkout (PYTHONPATH=src, and pytest's own pythonpath) where no .dist-info exists and
# metadata.version() would raise PackageNotFoundError. Keeping it here means every importable
# copy — wheel or checkout — carries its own version. pyproject.toml remains the release source
# of truth; tests/test_package_version.py pins this to it so the two cannot drift.
__version__ = "0.1.2"
