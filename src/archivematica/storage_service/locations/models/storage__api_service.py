import os
import json
import requests
import logging
import traceback

from django.db import models
from django.utils.translation import gettext_lazy as _

from archivematica.storage_service.locations.models.location import Location

API_BASE_URL = "http://localhost:8082/api/storage"

LOGGER = logging.getLogger(__name__)
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
DS_SCHEME = "https"
DFLT_AS_PORT = 8089
DFLT_DS_PORT = 443


class LogaltyRESTException(Exception):
    def __init__(self, msg, url=None, email=None, exc_info=False):
        msg = [msg]
        if url:
            msg.append(f' Using url "{url}".')
        if email:
            msg.append(f' Using email "{email}".')
        if exc_info:
            msg.append(f" {traceback.format_exc()}")
        super().__init__("".join(msg))


class Logalty(models.Model):
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

    class Meta:
        verbose_name = "Logalty Storage API Service Space"
        app_label = 'locations'

    ALLOWED_LOCATION_PURPOSE = [
        Location.AIP_STORAGE,
        Location.DIP_STORAGE,
    ]

    def _api_url(self, path):
        cleaned_path = path.strip("/")
        full_url = f"{API_BASE_URL}/{self.space.uuid}/{cleaned_path}"

        LOGGER.info("🔧 Building API URL")
        LOGGER.info("🧩 Base URL: %s", API_BASE_URL)
        LOGGER.info("🆔 Space UUID: %s", self.space.uuid)
        LOGGER.info("🪪 Path: %s", cleaned_path)
        LOGGER.info("🔗 Full URL: %s", full_url)

        return full_url

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
        LOGGER.info(
            "On move_from_storage_service of storage api service --> source_path: %s, destination_path: %s, package: {%s}",
            source_path,
            destination_path,
            package
        )

        if os.path.isdir(source_path):
            LOGGER.info(("Is a directroy: %s"), source_path)
            # ensure trailing slash on both paths
            src_path = os.path.join(source_path, "")
            dest_path = os.path.join(destination_path, "")

            # strip leading slash on dest_path
            dest_path = dest_path.lstrip("/")

            for path, _dirs, files in os.walk(src_path):
                for basename in files:
                    entry = os.path.join(path, basename)
                    dest = entry.replace(src_path, dest_path, 1)
                    self.upload_object(basename, dest, path,package, isFile=False)

        elif os.path.isfile(source_path):
            LOGGER.info(("Is a file: %s"), source_path)
            # strip leading slash on dest_path
            dest_path = destination_path.lstrip("/")
            self.upload_object(os.path.basename(source_path), destination_path, source_path,package, isFile=True)

    def upload_object(self, basename, dest, path, package, isFile=False):
        base_url = f"{self.logalty_url}/file"
        LOGGER.info("Upload OBJECT --> base_url: %s, dest: %s, package_type: %s", base_url, dest, package.package_type)
        try:
            # Read file bytes
            if isFile:
                with open(path, "rb") as f:
                    file_bytes = f.read()
            else:
                with open(os.path.join(path, basename), "rb") as f:
                    file_bytes = f.read()


            # Prepare JSON payload
            payload = {
                "destination": dest
            }
            if package.package_type == "DIP":
                self._post(
                    base_url + "/dip",
                    file=file_bytes,
                    json_data=payload,
                    cookies=None,
                )
            else:
                self._post(
                    base_url + "/aip",
                    file=file_bytes,
                    json_data=payload,
                    cookies=None,
                )

        except Exception:
            raise LogaltyRESTException(f"Error sending {basename} to {base_url}.")

    def _post(self, url, file=None, json_data=None, cookies=None):
        files = {}
        data = {}

        if file:
            files["file"] = ("filename", file)  # (name, file-like object)

        if json_data:
            # Convert the JSON dict to a string before sending
            data["destination"] = json_data["destination"]

        return requests.post(url, files=files, data=data, cookies=cookies)
