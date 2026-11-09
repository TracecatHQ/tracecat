"""Adapter for resource types whose resources share one bundle file.

Case tags use it: every tag lives in ``case_tags/_all.yml``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel

from tracecat.workspace_sync.adapters.base import FlatManifestAdapter, path_parts
from tracecat.workspace_sync.schemas import WorkspaceManifestResources

BUNDLE_FORMAT_VERSION = 1
"""Version of the bundle file format written by :class:`BundledManifestAdapter`."""


class BundleFormatError(ValueError):
    """A bundle file is not valid YAML or not in the bundle format."""


class BundledManifestAdapter(FlatManifestAdapter):
    """Flat layout where every resource of the type shares one bundle file.

    Pushes write ``<root>/<bundle_filename>``, a YAML file listing every
    resource under ``bundle_key``::

        version: 1
        tags:
        - id: urgent
          name: Urgent

    Repositories written before bundling hold one ``<root>/<key>.yml`` file
    per resource. Those still parse, and a push moves each resource it writes
    into the bundle. The bundle file name starts with ``_``, which
    :func:`path_segment` strips, so no per-resource file can ever share it.
    """

    bundle_filename: ClassVar[str] = "_all.yml"
    """File name of the bundle inside the resource root."""
    bundle_key: ClassVar[str]
    """Top-level key that holds the list of resources, e.g. ``tags``."""

    @property
    def bundle_path(self) -> str:
        """Return where pushes write the bundle: ``<root>/<bundle_filename>``."""
        return f"{self.root}/{self.bundle_filename}"

    def bundle_path_in(self, roots: WorkspaceManifestResources) -> str:
        """Return the bundle path under the root a manifest declares."""
        # ``getattr``: each adapter names its manifest root field by
        # ``spec_attr``, a ``WorkspaceSpecField`` literal, so the field is
        # only known per subclass. Other path helpers here look it up the same way.
        root_parts = path_parts(str(getattr(roots, self.spec_attr)))
        return "/".join([*root_parts, self.bundle_filename])

    def is_bundle_file(self, path: str) -> bool:
        """Return whether a path already known to be this type's is a bundle."""
        return path.rsplit("/", 1)[-1] == self.bundle_filename

    def path_key_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> str | None:
        """Recover a per-resource file's stem; the bundle file is not one."""
        if self.is_bundle_file(path):
            return None
        return super().path_key_from_path(path, roots)

    def bundle_entry(self, spec: BaseModel) -> dict[str, Any]:
        """Return one resource's entry in the bundle.

        ``version`` and ``type`` are left out: the bundle header implies them,
        and the spec model fills them back in on parse.

        The entry is an untyped dict on purpose. It is the JSON dump of
        whichever spec model the subclass declares, including unknown fields
        that model keeps (``extra="allow"``), so no single ``TypedDict``
        describes it. Sorting and partial-push merging read its ``id`` and
        import identity fields, such as ``name``; the rest is opaque.
        """
        return spec.model_dump(
            mode="json", exclude_none=True, exclude={"version", "type"}
        )

    def dump_bundle(self, entries: Iterable[Any]) -> str:
        """Serialize bundle entries, sorted by ``id``, into the bundle file."""
        ordered = sorted(entries, key=bundle_entry_id)
        return yaml.safe_dump(
            {"version": BUNDLE_FORMAT_VERSION, self.bundle_key: ordered},
            sort_keys=False,
            allow_unicode=True,
        )

    def load_bundle(self, content: str) -> list[Any]:
        """Return the raw entries of a bundle file, unvalidated.

        Raises:
            BundleFormatError: The file is not YAML, or not a bundle of this
                format version.
        """
        try:
            raw = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise BundleFormatError(f"YAML parsing error: {e}") from e
        if not isinstance(raw, dict) or raw.get("version") != BUNDLE_FORMAT_VERSION:
            raise BundleFormatError(
                f"Expected a bundle file with 'version: {BUNDLE_FORMAT_VERSION}'"
            )
        # A missing key is an error, not an empty bundle: a partial push
        # would otherwise overwrite whatever the file holds.
        entries = raw.get(self.bundle_key)
        if not isinstance(entries, list):
            raise BundleFormatError(f"Expected '{self.bundle_key}' to be a list")
        return entries


def bundle_entry_id(entry: Any) -> str:
    """Return a raw bundle entry's ``id``, or ``""`` when it has none."""
    match entry:
        case {"id": str(source_id)}:
            return source_id
        case _:
            return ""
