import os
import logging
import traceback
import requests
import boto3
import botocore

from django.db import models
from .location import Location

LOGGER = logging.getLogger(__name__)


class LogaltyRESTException(Exception):
    def __init__(self, msg, url=None, exc_info=False):
        parts = [msg]
        if url:
            parts.append(f" URL={url}")
        if exc_info:
            parts.append(traceback.format_exc())
        super().__init__("".join(parts))


class Logalty(models.Model):
    space = models.OneToOneField("Space", to_field="uuid", on_delete=models.CASCADE)

    logalty_user = models.CharField(max_length=64, blank=True,verbose_name="The username for Logalty Storage Service")
    logalty_pass = models.CharField(max_length=256, blank=True,verbose_name="The password for Logalty Storage Service")
    logalty_url = models.CharField(max_length=2048,verbose_name="The url for Logalty Storage Service")

    s3_access_key_id = models.CharField(max_length=64, blank=True,verbose_name="The user for AWS S3 on which upload the files before encryption")
    s3_secret_access_key = models.CharField(max_length=256, blank=True,verbose_name="The user for AWS S3 on which upload the files before encryption")
    s3_endpoint_url = models.CharField(max_length=2048,verbose_name="The user for AWS S3 on which upload the files before encryption")
    s3_region = models.CharField(max_length=64,verbose_name="The region for AWS S3 on which upload the files before encryption")
    s3_bucket = models.CharField(max_length=64, blank=True,verbose_name="The user for AWS S3 on which upload the files before encryption")

    class Meta:
        app_label = "locations"
        verbose_name = "Logalty Storage"

    ALLOWED_LOCATION_PURPOSE = [
        Location.AIP_STORAGE,
        Location.DIP_STORAGE,
    ]

    # -----------------------
    # S3 CLIENT
    # -----------------------
    @property
    def s3(self):
        if not hasattr(self, "_s3"):
            self._s3 = boto3.resource(
                "s3",
                region_name=self.s3_region,
                endpoint_url=self.s3_endpoint_url,
                aws_access_key_id=self.s3_access_key_id,
                aws_secret_access_key=self.s3_secret_access_key,
            )
        return self._s3

    @property
    def bucket(self):
        return self.s3.Bucket(self.s3_bucket)

    def _ensure_bucket_exists(self):
        try:
            self.s3.meta.client.head_bucket(Bucket=self.s3_bucket)
        except botocore.exceptions.ClientError:
            if self.s3_region == "us-east-1":
                self.s3.create_bucket(Bucket=self.s3_bucket)
            else:
                self.s3.create_bucket(
                    Bucket=self.s3_bucket,
                    CreateBucketConfiguration={"LocationConstraint": self.s3_region},
                )

    # -----------------------
    # DELETE OPTIONAL
    # -----------------------
    def delete_path(self, delete_path):
        url = f"{self.logalty_url}/file"

        try:
            r = requests.delete(
                url,
                params={"destination": delete_path},
                auth=(self.logalty_user, self.logalty_pass),
            )
            r.raise_for_status()
        except Exception as e:
            raise LogaltyRESTException("Delete failed", url=url, exc_info=True)

    # -----------------------
    # CORE FLOW
    # -----------------------
    def _upload_then_encrypt(self, file_path, s3_key, package=None, is_dip=False):

        self._ensure_bucket_exists()

        filename = os.path.basename(file_path)

        # -----------------------
        # METADATA
        # -----------------------
        meta = {}
        user_id = None
        object_salt = None

        if package and getattr(package, "misc_attributes", None):
            user_id = package.misc_attributes.get("user_id")
            object_salt = package.misc_attributes.get("object_salt")

            if user_id:
                meta["user_id"] = str(user_id)
            if object_salt:
                meta["object_salt"] = str(object_salt)

        extra_args = {"Metadata": meta} if meta else {}

        LOGGER.info("⬆️ Uploading to S3: %s → %s", file_path, s3_key)

        # -----------------------
        # 1. UPLOAD S3
        # -----------------------
        try:
            with open(file_path, "rb") as f:
                self.bucket.upload_fileobj(
                    Fileobj=f,
                    Key=s3_key,
                    ExtraArgs=extra_args if extra_args else None,
                )

            LOGGER.info("✅ S3 upload OK: %s", s3_key)

        except Exception as e:
            LOGGER.error("❌ S3 upload failed: %s", e)
            raise RuntimeError(f"S3 upload failed: {e}")

        # -----------------------
        # 2. CALL SPRING BOOT
        # -----------------------
        try:
            endpoint = "/file/dip" if is_dip else "/file/aip"
            url = f"{self.logalty_url}{endpoint}"

            payload = {
                "destination": s3_key,
                "filename": filename,   # ✅ IMPORTANT
            }

            if user_id:
                payload["user_id"] = user_id
            if object_salt:
                payload["object_salt"] = object_salt

            LOGGER.info("📡 Calling IPDS url %s payload=%s", url, payload)

            r = requests.post(
                url,
                data=payload,
                auth=(self.logalty_user, self.logalty_pass),
                timeout=300,
            )
            r.raise_for_status()

            LOGGER.info("🔐 Encryption triggered: %s", s3_key)
            return s3_key

        # -----------------------
        # 3. ROLLBACK S3
        # -----------------------
        except Exception as e:
            LOGGER.error("❌ Spring Boot failed → rollback S3: %s", e)
            try:
                self.bucket.Object(s3_key).delete()
                LOGGER.info("🗑️ Rollback OK: %s", s3_key)
            except Exception as del_err:
                LOGGER.error("❌ Rollback failed: %s", del_err)

            raise RuntimeError(f"Encryption API failed: {e}")

    # -----------------------
    # ENTRYPOINT
    # -----------------------
    def move_from_storage_service(self, source_path, destination_path, package=None):

        is_dip = package and getattr(package, "package_type", None) == "DIP"

        if os.path.isdir(source_path):
            base = source_path.rstrip("/") + "/"
            dest = destination_path.rstrip("/") + "/"

            for root, _, files in os.walk(base):
                for name in files:
                    full = os.path.join(root, name)
                    s3_key = full.replace(base, dest)

                    self._upload_then_encrypt(
                        full,
                        s3_key,
                        package=package,
                        is_dip=is_dip,
                    )

        elif os.path.isfile(source_path):

            self._upload_then_encrypt(
                source_path,
                destination_path,
                package=package,
                is_dip=is_dip,
            )

        else:
            raise ValueError("Invalid source path")