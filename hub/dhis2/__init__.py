"""DHIS2 read-only integration package."""

from hub.dhis2.client import ALLOWED_RESOURCES, Dhis2Client, Dhis2Error

__all__ = ["ALLOWED_RESOURCES", "Dhis2Client", "Dhis2Error"]
