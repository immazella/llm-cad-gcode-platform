"""Post-processor kayıt defteri: profil → post-processor."""
from __future__ import annotations

from ..profiles import MachineProfile
from .base import PostProcessor
from .dialects import FanucPost, GrblPost, LinuxCncPost

POSTS: dict[str, type[PostProcessor]] = {p.id: p for p in (FanucPost, LinuxCncPost, GrblPost)}


def get_post(profile: MachineProfile) -> PostProcessor:
    try:
        return POSTS[profile.post](profile)
    except KeyError as exc:
        raise KeyError(f"Profil '{profile.id}' için post-processor bulunamadı: {profile.post}. Mevcut: {', '.join(POSTS)}") from exc


__all__ = ["POSTS", "PostProcessor", "get_post"]
