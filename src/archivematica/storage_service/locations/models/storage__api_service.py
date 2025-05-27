import os
import json
import mimetypes
import requests
import logging
import traceback

from django.db import models
from django.utils.translation import gettext_lazy as _

from archivematica.storage_service.locations.models.location import Location

API_BASE_URL = "http://localhost:8082/storage/api"

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


def _post(url, filename=None, file=None, json_data=None, cookies=None):
    files = {}
    data = {}

    if file and filename:
        # Guess MIME type
        mime_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        files["file"] = (filename, file, mime_type)

    if json_data:
        # Flatten JSON dict into form fields (multipart-style)
        for key, value in json_data.items():
            data[key] = value

    # Logging for debug
    LOGGER.info(f"📡 POST to URL: {url}")
    LOGGER.info(f"📁 Sending file: {files.get('file')[0] if 'file' in files else 'None'}")
    LOGGER.info(f"📦 Payload data: {data}")

    response = requests.post(url, files=files, data=data, cookies=cookies)

    LOGGER.info(f"✅ Response status code: {response.status_code}")
    if not response.ok:
        LOGGER.error(f"❌ Error response: {response.text}")
        response.raise_for_status()

    return response


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
            LOGGER.info("Is a directory: %s", source_path)
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
            LOGGER.info("Is a file: %s", source_path)
            # strip leading slash on dest_path
            dest_path = destination_path.lstrip("/")
            self.upload_object(os.path.basename(source_path), destination_path, source_path,package, isFile=True)

    def upload_object(self, basename, dest, path, package, isFile=False):
        base_url = f"{self.logalty_url}/file"
        endpoint = "/dip" if package.package_type == "DIP" else "/aip"
        url = base_url + endpoint

        LOGGER.info("Upload OBJECT --> base_url: %s, dest: %s, package_type: %s", base_url, dest, package.package_type)

        try:
            file_path = path if isFile else os.path.join(path, basename)
            with open(file_path, "rb") as f:
                filename = os.path.basename(file_path)
                file_bytes = f.read()

            # Prepare JSON payload as part of multipart form
            payload = {
                "destination": dest
            }

            _post(
                url,
                filename=filename,
                file=file_bytes,
                json_data=payload,
                cookies=None,
            )

        except Exception as e:
            raise LogaltyRESTException(f"Error sending {basename} to {base_url}: {e}")
