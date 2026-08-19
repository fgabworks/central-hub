"""Read-only Google Drive connector for AiriX context (no Drive Center)."""

from __future__ import annotations

from hub.drive.service import DriveService, DriveServiceError

__all__ = ["DriveService", "DriveServiceError"]
