import os
import requests
from django.db import models
from archivematica.storage_service.locations.models.location import Location
from django.utils.translation import gettext_lazy as _

API_BASE_URL = "http://localhost:8082/api/storage"

class LogaltyStorageAPIServiceSpace(models.Model):
    space = models.OneToOneField("Space", to_field='uuid', on_delete=models.CASCADE)
    logalty_user = models.CharField(
        max_length=64, blank=True, verbose_name=_("User name for Logalty Storage Service"),
    )
    logalty_pass = models.CharField(
        max_length=256,
        blank=True,
        verbose_name=_("User name password for Logalty Storage Service"),
    )
    logalty_url = models.CharField(
        max_length=2048,
        verbose_name=_("Logalty Storage Module Endpoint URL"),
        help_text=_("Logalty Storage Module Endpoint URL."),
    )
    ARCHIVE_FORMAT_ZIP = "ZIP"
    ARCHIVE_FORMAT_7Z = "7Z"
    ARCHIVE_FORMAT_CHOICES = ((ARCHIVE_FORMAT_ZIP, "ZIP"), (ARCHIVE_FORMAT_7Z, "7z"))
    archive_format = models.CharField(
        max_length=3,
        choices=ARCHIVE_FORMAT_CHOICES,
        default=ARCHIVE_FORMAT_ZIP,
        verbose_name=_("Archive format"),
    )
    class Meta:
        verbose_name = "Logalty Storage API Service Space"
        app_label = 'locations'

    ALLOWED_LOCATION_PURPOSE = [
        Location.AIP_STORAGE,
        Location.DIP_STORAGE,
    ]

    def _api_url(self, path):
        return f"{API_BASE_URL}/{self.space.uuid}/{path.strip('/')}"

    def browse(self, path):
        """Browse a path in the storage."""
        pass

    def delete_path(self, delete_path):
        """Delete a path in the storage."""
        pass

    def move_to_storage_service(self, src_path, dest_path, dest_space):
        """
        Moves src_path to dest_space.staging_path/dest_path. (DOWNLOAD FILE)
        Assumes API handles both source and destination info.
        """
        pass

    def move_from_storage_service(self, source_path, destination_path, package=None):
        """
        Moves self.staging_path/source_path to destination_path. (UPLOAD FILE)
        """
        pass
