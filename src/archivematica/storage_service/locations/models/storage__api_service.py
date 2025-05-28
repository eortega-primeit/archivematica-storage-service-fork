import os
import requests
import logging
import traceback
import zipfile
import io

from django.db import models
from django.utils.translation import gettext_lazy as _
from archivematica.storage_service.locations.models.location import Location

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

    def browse(self, path):
        """Browse a path in the storage."""
        LOGGER.info(
            "On browse of storage api service --> path: {%s}",
            path
        )
        pass

    def delete_path(self, delete_path):
        """Delete a path in the storage."""
        LOGGER.info(
            "On delete_path of storage api service --> delete_path: {%s}",
            delete_path
        )
        pass

    def move_to_storage_service(self, src_path, dest_path, dest_space):
        """
        Downloads AIP or DIP from Spring Boot API via GET.
        - AIP is saved directly as a file.
        - DIP is assumed to be a zipped folder and is extracted.
        """
        LOGGER.info(
            "On move_to_storage_service of storage api service --> source_path: %s, destination_path: %s, dest_space: {%s}",
            src_path,
            dest_path,
            dest_space
        )

        try:
            if src_path.endswith((".7z", ".zip", ".rar", ".tar.gz", ".tar", ".gz",".pbzip2")):
                # AIP - Download and save as is
                LOGGER.info("Assuming AIP file (no unzip): %s", src_path)
                base_url = f"{self.logalty_url}/file/download/aip"
                params = {"origin": src_path}
                response = requests.get(base_url, params=params, stream=True)
                response.raise_for_status()

                # Ensure the directory exists
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)

                # Save the file directly
                with open(dest_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                LOGGER.info(f"AIP downloaded to {dest_path}")

            else:
                # DIP - Download and unzip
                LOGGER.info("Assuming DIP folder (will unzip): %s", src_path)
                base_url = f"{self.logalty_url}/file/download/dip"
                params = {"origin": src_path}
                response = requests.get(base_url, params=params, stream=True)
                response.raise_for_status()

                # Unzip into destination path
                with zipfile.ZipFile(io.BytesIO(response.content)) as zip_ref:
                    os.makedirs(dest_path, exist_ok=True)
                    LOGGER.info(f"makedirs for {dest_path}")
                    zip_ref.extractall(dest_path)

                LOGGER.info(f"DIP downloaded and extracted to {dest_path}")

        except requests.RequestException as e:
            LOGGER.error(f"HTTP request failed: {e}")
            raise LogaltyRESTException(f"Error downloading file via GET: {e}")
        except zipfile.BadZipFile as e:
            LOGGER.error(f"Failed to unzip content: {e}")
            raise LogaltyRESTException(f"Error unzipping downloaded file: {e}")
        except Exception as e:
            LOGGER.error(f"Unexpected error: {e}")
            raise LogaltyRESTException(f"Error in move_to_storage_service: {e}")



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
            LOGGER.info("Is a directroy: %s", source_path)
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

        return requests.post(url, files=files, data=data, cookies=cookies, auth=(self.logalty_user, self.logalty_pass))
