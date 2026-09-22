# -*- coding: utf-8 -*-
"""Refuse to run when the original ``docxtpl`` distribution is installed too.

Both distributions ship the ``docxtpl`` import package. pip does not treat
them as conflicting, so installing both silently interleaves their files and
uninstalling either one breaks the other. Set
``DOCXTPL_ALLOW_MIXED_INSTALL=1`` only to inspect such an environment.
"""
import os


def check():
    if os.environ.get("DOCXTPL_ALLOW_MIXED_INSTALL") == "1":
        return
    try:
        from importlib import metadata
    except ImportError:  # Python < 3.8
        return
    try:
        version = metadata.version("docxtpl")
    except metadata.PackageNotFoundError:
        return
    except Exception:
        return
    raise ImportError(
        "The 'docxtpl' distribution (%s) is installed next to 'rust-docxtpl'. Both provide the "
        "'docxtpl' import package and overwrite each other's files. Create a clean environment "
        "with exactly one of them (pip uninstall docxtpl rust-docxtpl, then reinstall one)." % version
    )
