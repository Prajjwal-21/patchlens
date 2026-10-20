"""A deliberately small demo service used by PatchLens' local demo workflow.

It is not a tutorial: it exists so `make demo` can index a real repository and
review a real change without any GitHub App configuration. The bug history is on
purpose — see ``pr.diff`` in this directory.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
