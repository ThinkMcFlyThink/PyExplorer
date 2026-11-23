"""
SearchSpec - everything the Search tab asks for, as plain data.

    spec = SearchSpec(locations=["/home/me"], name="holiday ext:jpg",
                      size_min=1 * MB, modified_after=time.time() - 7 * DAY)

It is saved with the session (``to_dict`` / ``from_dict``) and handed to the
search service, which does the work. ``name`` uses the same query syntax as
the instant filter (see core/query.py); the other fields are what the Search
tab's filter controls set.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import List, Optional

KB, MB, GB = 1024, 1024 ** 2, 1024 ** 3
DAY = 86400
SIZE_UNITS = [("bytes", 1), ("KB", KB), ("MB", MB), ("GB", GB)]


@dataclass
class SearchSpec:
    # where
    locations: List[str] = field(default_factory=list)   # folders / drives / smb://... (one or many)
    whole_computer: bool = False                          # ignore ``locations``: every local drive
    # name
    name: str = ""                                        # query syntax: "holiday ext:jpg !thumb"
    case_sensitive: bool = False
    # contents
    content: str = ""                                     # text to look for INSIDE files
    content_case: bool = False
    content_regex: bool = False
    content_whole_word: bool = False
    content_max_mb: int = 200                             # don't read files bigger than this
    content_binary: bool = False                          # also look inside binary files
    # filters
    kind: str = "all"                                     # all | files | folders
    extensions: List[str] = field(default_factory=list)   # ["jpg", "png"] (without dots)
    size_min: Optional[int] = None                        # bytes
    size_max: Optional[int] = None
    modified_after: Optional[float] = None                # seconds since epoch
    modified_before: Optional[float] = None
    include_hidden: bool = False
    exclude: List[str] = field(default_factory=list)      # folder names to skip, e.g. node_modules
    max_results: int = 250_000

    @property
    def has_content(self) -> bool:
        return bool(self.content)

    @property
    def is_empty(self) -> bool:
        """Nothing asked for at all (an empty search lists everything - allowed, but
        the Search tab waits for the user to type something first)."""
        return not (self.name.strip() or self.content or self.extensions or self.size_min is not None
                    or self.size_max is not None or self.modified_after is not None
                    or self.modified_before is not None or self.kind != "all")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "SearchSpec":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


def parse_extensions(text: str) -> List[str]:
    """"jpg, .png; GIF" -> ["jpg", "png", "gif"]"""
    out = []
    for part in text.replace(";", ",").replace(" ", ",").split(","):
        part = part.strip().lstrip("*").lstrip(".").lower()
        if part and part not in out:
            out.append(part)
    return out


def parse_names(text: str) -> List[str]:
    """"node_modules, .git" -> ["node_modules", ".git"]"""
    return [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
