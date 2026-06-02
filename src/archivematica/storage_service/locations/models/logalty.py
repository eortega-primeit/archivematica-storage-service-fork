import os
import io
import re
import py7zr
import logging
import requests
import traceback

from django.db import models
from django.utils.translation import gettext_lazy as _
from .location import Location

# Global constants
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
        max_length=64, blank=True, verbose_name=_("The username for Logalty Storage Service"),
    )
    logalty_pass = models.CharField(
        max_length=256, blank=True, verbose_name=_("The password for Logalty Storage Service"),
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
        LOGGER.info("📁 [BROWSE] Path: %s", path)
        pass

    def delete_path(self, delete_path):
        """Delete a path in the storage via REST API."""
        LOGGER.info("🗑️ [DELETE] Path: %s", delete_path)
        base_url = f"{self.logalty_url}/file"

        try:
            params = {"destination": delete_path}
            response = requests.delete(
                base_url,
                params=params,
                headers=HEADERS,
                auth=(self.logalty_user, self.logalty_pass)
            )
            response.raise_for_status()
            LOGGER.info("✅ Successfully deleted path: %s", delete_path)

        except requests.RequestException as e:
            LOGGER.error("❌ Failed to delete path %s: %s", delete_path, e)
            raise LogaltyRESTException(f"Error deleting path via DELETE: {e}", url=base_url)

    def move_to_storage_service(self, src_path, dest_path, dest_space):
        """
        Downloads AIP or DIP from Spring Boot API via GET.
        - AIP files (.7z, .zip, etc.) are saved directly as compressed files.
        - DIP folders are downloaded as 7z archives and extracted.
        """
        # Extract package UUID from src_path to query database for user_id and object_salt
        # Pattern: /5178/fb1b/f2ca/429c/94c9/1ed6/7c13/6830/... -> 5178fb1b-f2ca-429c-94c9-1ed67c136830
        package_uuid = None
        user_id = None
        object_salt = None
        
        uuid_pattern = r'/([0-9a-f]{4})/([0-9a-f]{4})/([0-9a-f]{4})/([0-9a-f]{4})/([0-9a-f]{4})/([0-9a-f]{4})/([0-9a-f]{4})/([0-9a-f]{4})/'
        match = re.search(uuid_pattern, src_path)
        if match:
            package_uuid = '-'.join(match.groups())
            try:
                from .package import Package
                package = Package.objects.get(uuid=package_uuid)
                if package.misc_attributes:
                    user_id = package.misc_attributes.get("user_id")
                    object_salt = package.misc_attributes.get("object_salt")
            except Exception as e:
                LOGGER.warning("Could not retrieve package metadata: %s", e)
        
        LOGGER.info("⬇️ [DOWNLOAD] AIP/DIP from src: %s ➡ dest: %s", src_path, dest_path)

        try:
            # Determine if it's an AIP (compressed file) or DIP (folder to extract)
            is_compressed_aip = src_path.endswith((".7z", ".zip", ".rar", ".tar.gz", ".tar", ".gz", ".pbzip2"))
            
            if is_compressed_aip:
                LOGGER.info("📦 Treating as AIP file: %s", src_path)
                url = f"{self.logalty_url}/file/download/aip"
            else:
                LOGGER.info("📂 Treating as DIP folder: %s", src_path)
                url = f"{self.logalty_url}/file/download/dip"

            # Build params with origin and metadata
            params = {"origin": src_path}
            
            # Add user_id and object_salt to GET params if available
            if user_id:
                params["user_id"] = user_id
            if object_salt:
                params["object_salt"] = object_salt
            
            response = requests.get(url, params=params, stream=True, auth=(self.logalty_user, self.logalty_pass))
            response.raise_for_status()

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            if is_compressed_aip:
                # Save AIP as a file without decompressing
                with open(dest_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                LOGGER.info("✅ AIP saved to %s", dest_path)
            else:
                # DIP comes as a 7z archive and needs to be extracted
                with py7zr.SevenZipFile(io.BytesIO(response.content), mode='r') as archive:
                    os.makedirs(dest_path, exist_ok=True)
                    archive.extractall(path=dest_path)
                LOGGER.info("✅ DIP extracted to %s", dest_path)

        except requests.RequestException as e:
            LOGGER.error("❌ HTTP request failed: %s", e)
            raise LogaltyRESTException(f"Error downloading file via GET: {e}")
        except py7zr.Bad7zFile as e:
            LOGGER.error("❌ Failed to extract 7z content: %s", e)
            raise LogaltyRESTException(f"Error extracting downloaded 7z file: {e}")
        except Exception as e:
            LOGGER.error("❌ Unexpected error: %s", e)
            raise LogaltyRESTException(f"Error in move_to_storage_service: {e}")

    def move_from_storage_service(self, source_path, destination_path, package=None):
        """
        Upload file or folder from local path to Logalty backend.
        """
        LOGGER.info("⬆️ [UPLOAD] source: %s ➡ destination: %s", source_path, destination_path)

        if os.path.isdir(source_path):
            LOGGER.info("📂 Source is a directory: %s", source_path)

            src_path = os.path.join(source_path, "")
            dest_path = os.path.join(destination_path.lstrip("/"), "")

            for path, _dirs, files in os.walk(src_path):
                for basename in files:
                    entry = os.path.join(path, basename)
                    dest = entry.replace(src_path, dest_path, 1)
                    self.upload_object(basename, dest, path, package, isFile=False)

        elif os.path.isfile(source_path):
            LOGGER.info("📄 Source is a file: %s", source_path)
            dest_path = destination_path.lstrip("/")
            self.upload_object(os.path.basename(source_path), dest_path, source_path, package, isFile=True)

    def upload_object(self, basename, dest, path, package, isFile=False):
        """Upload an individual file to the appropriate AIP or DIP endpoint."""
        base_url = f"{self.logalty_url}/file"
        LOGGER.info("📤 Uploading object: %s ➡ %s", basename, dest)

        try:
            file_path = path if isFile else os.path.join(path, basename)
            with open(file_path, "rb") as f:
                file_bytes = f.read()

            # Build payload with destination and metadata
            payload = {"destination": dest}
            
            # Add user_id and object_salt from package.misc_attributes if available
            if package and package.misc_attributes:
                user_id = package.misc_attributes.get("user_id")
                object_salt = package.misc_attributes.get("object_salt")
                
                if user_id:
                    payload["user_id"] = user_id
                if object_salt:
                    payload["object_salt"] = object_salt

            if package.package_type == "DIP":
                self._post(f"{base_url}/dip", file=file_bytes, json_data=payload)
            else:
                self._post(f"{base_url}/aip", file=file_bytes, json_data=payload)

        except Exception:
            LOGGER.error("❌ Upload failed for %s", basename)
            raise LogaltyRESTException(f"Error sending {basename} to {base_url}.")

    def _post(self, url, file=None, json_data=None, cookies=None):
        """Internal helper for POSTing a file."""
        files = {"file": ("filename", file)} if file else {}
        
        # Build data dict with all fields from json_data
        data = {}
        if json_data:
            data.update(json_data)  # Include all fields: destination, user_id, object_salt, etc.

        return requests.post(
            url,
            files=files,
            data=data,
            cookies=cookies,
            auth=(self.logalty_user, self.logalty_pass)
        )
