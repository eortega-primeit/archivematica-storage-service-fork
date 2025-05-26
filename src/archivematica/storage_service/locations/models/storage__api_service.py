import os
import requests
import logging
import json
import traceback
import subprocess
import urllib.parse
from urllib.parse import urlparse

from django.db import models
from django.utils.translation import gettext_lazy as _
from lxml import etree
from requests import RequestException


from archivematica.storage_service.locations.models.location import Location
from archivematica.storage_service.common import utils




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
    as_url = models.URLField(
        blank=True,
        max_length=256,
        verbose_name=_("ArchivesSpace URL"),
        help_text=_(
            "URL of ArchivesSpace server. E.g."
            " http://sandbox.archivesspace.org:8089/ (default port"
            " 8089 if omitted)"
        ),
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
        LOGGER.info(
            "source_path: %s, destination_path: %s, package: %s",
            source_path,
            destination_path,
            package
        )

        if os.path.isdir(source_path):
            # ensure trailing slash on both paths
            src_path = os.path.join(source_path, "")
            dest_path = os.path.join(destination_path, "")

            # strip leading slash on dest_path
            dest_path = dest_path.lstrip("/")

            for path, _dirs, files in os.walk(src_path):
                for basename in files:
                    entry = os.path.join(path, basename)
                    dest = entry.replace(src_path, dest_path, 1)
                    self.upload_object(basename, dest, path)

        elif os.path.isfile(source_path):
            # strip leading slash on dest_path
            dest_path = destination_path.lstrip("/")
            self.upload_object(os.path.basename(source_path), destination_path, source_path)

        # if package is None:
        #     raise LogaltyRESTException("DSpace requires package param.")
        # if package.package_type == "AIP" and not os.path.isfile(source_path):
        #     raise LogaltyRESTException(
        #         "Storing in DSpace does not support uncompressed AIPs."
        #     )
        # self._parse_and_clean_urls()
        # # Item to be created in DSpace
        # metadata, destinations, package_title = self._get_metadata(
        #     source_path, package.uuid, package.package_type
        # )
        # ds_collection, as_archival_repo, as_archival_obj = self._assign_destination(
        #     package.package_type, destinations
        # )
        # # Logging in to REST api gives us a session id
        # ds_sessionid = self._login_to_storage_rest()
        # try:
        #     ds_item = self._create_dspace_record(metadata, ds_sessionid, ds_collection)
        #     if package.package_type == "DIP":
        #         self._handle_dip(
        #             source_path,
        #             ds_item,
        #             ds_sessionid,
        #             as_archival_repo,
        #             as_archival_obj,
        #             package,
        #             package_title,
        #         )
        #     else:
        #         self._handle_aip(source_path, ds_item, ds_sessionid)
        # finally:
        #     self._logout_from_storage_rest(ds_sessionid)

    def upload_object(self, basename, dest, path):
        base_url = "{}/items/{}".format(
            self._get_base_url(self.logalty_url), dest
        )
        bitstream_url = "{}/bitstreams?name={}".format(
            base_url, urllib.parse.quote(basename.encode("utf-8"))
        )
        try:
            with open(os.path.join(path, basename), "rb") as content:
                self._post(
                    bitstream_url,
                    data=content,
                    cookies=None,
                )
        except Exception:
            raise LogaltyRESTException(
                f"Error sending {basename} to {bitstream_url}.")

    def _logout_from_storage_rest(self, ds_sessionid):
        """Logout from DSpace API."""
        try:
            self._post(
                f"{self._get_base_url(self.ds_rest_url)}/logout",
                cookies={"JSESSIONID": ds_sessionid},
            )
        except Exception as err:
            LOGGER.warning("Failed to log out of DSpace REST API: %s.", err)

    def _handle_dip(
            self,
            source_path,
            ds_item,
            ds_sessionid,
            as_archival_repo,
            as_archival_obj,
            package,
            package_title,
    ):
        self._deposit_dip_to_storage(source_path, ds_item, ds_sessionid)
        # if all(
        #         [
        #             self.logalty_url,
        #             self.logalty_user,
        #             self.logalty_pass,
        #             self.as_repository,
        #             as_archival_obj,
        #         ]
        # ):
        #     self._link_dip_to_archivesspace(
        #         self._get_as_client(as_archival_repo),
        #         as_archival_repo,
        #         as_archival_obj,
        #         package.uuid,
        #         package_title,
        #         ds_item,
        #     )

    def _handle_aip(self, source_path, ds_item, ds_sessionid):
        self._deposit_aip_to_storage(source_path, ds_item, ds_sessionid)
        # if self.upload_to_tsm:
        #     self._upload_to_tsm(source_path)

    @staticmethod
    def _get_base_url(parsed_url):
        return f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"

    def _create_dspace_record(self, metadata, ds_sessionid, ds_collection):
        # Structure necessary to create DSpace record
        item = {"type": "item", "metadata": metadata}
        collection_url = (
            f"{self._get_base_url(self.logalty_url)}/collections/{ds_collection}/items"
        )
        try:  # Create item in DSpace
            response = self._post(
                collection_url,
                cookies={"JSESSIONID": ds_sessionid},
                data=json.dumps(item),
            )
            response.raise_for_status()
            return response.json()
        except RequestException as err:
            raise LogaltyRESTException(
                f"Could not create DSpace record: {collection_url}: {err}."
            )
        except ValueError:
            raise LogaltyRESTException("Not a JSON response.")

    def _deposit_dip_to_storage(self, source_path, ds_item, ds_sessionid):
        base_url = "{}/items/{}".format(
            self._get_base_url(self.logalty_url), ds_item["uuid"]
        )
        for root, __, files in os.walk(source_path):
            for name in files:
                bitstream_url = "{}/bitstreams?name={}".format(
                    base_url, urllib.parse.quote(name.encode("utf-8"))
                )
                try:
                    with open(os.path.join(root, name), "rb") as content:
                        self._post(
                            bitstream_url,
                            data=content,
                            cookies={"JSESSIONID": ds_sessionid},
                        )
                except Exception:
                    raise LogaltyRESTException(
                        f"Error sending {name} to {bitstream_url}.")

    def _deposit_aip_to_storage(self, source_path, ds_item, ds_sessionid):
        bitstream_url = "{base_url}/items/{uuid}/bitstreams?name={name}".format(
            base_url=self._get_base_url(self.logalty_url),
            uuid=ds_item["uuid"],
            name=urllib.parse.quote(os.path.basename(source_path).encode("utf-8")),
        )
        try:
            with open(source_path, "rb") as content:
                response = self._post(
                    bitstream_url, data=content, cookies={"JSESSIONID": ds_sessionid}
                )
            response.raise_for_status()
        except Exception:
            raise LogaltyRESTException(
                f"Error depositing AIP at {source_path} to DSpace via URL {bitstream_url}."
            )

    def _parse_and_clean_urls(self):
        self.logalty_url = urlparse(self.logalty_url)
        self.as_url = urlparse(self.as_url)
        if self.logalty_url.scheme != DS_SCHEME:
            self.logalty_url = self.logalty_url._replace(scheme=DS_SCHEME)
        if not self.as_url.port:
            self.as_url = self.as_url._replace(
                netloc=f"{self.as_url.netloc}:{DFLT_AS_PORT}"
            )
        if not self.logalty_url.port:
            self.ds_rest_url = self.logalty_url._replace(
                netloc=f"{self.logalty_url.netloc}:{DFLT_DS_PORT}"
                )

    def _get_metadata(self, input_path, aip_uuid, package_type):
        """Get metadata for DSpace from METS file.

        Returns a 3-tuple consisting of a metadata list, a repos dict and a
        package title string.
        """
        metadata = []
        repos = {}
        package_title = ""
        output_dir = os.path.dirname(input_path) + "/"
        dirname = os.path.splitext(os.path.basename(input_path))[0]

        mets_el = self._get_mets_el(
            package_type, output_dir, input_path, dirname, aip_uuid
        )
        root_objects_el = mets_el.find(
            "//mets:structMap[@TYPE='physical']/mets:div/mets:div[@LABEL='objects']",
            namespaces=utils.NSMAP,
        )
        if root_objects_el is None:
            return metadata, repos, package_title
        dmdids = root_objects_el.get("DMDID")
        if not dmdids:
            return metadata, repos, package_title
        metadata, repos, package_title = self._analyze_md_els(
            mets_el, dmdids, metadata, repos, package_title
        )
        if not metadata:  # We have nothing and therefore filename becomes title
            package_title = (
                dirname[: dirname.find(aip_uuid) - 1].replace("_", " ").title()
            )
            metadata = [self._format_metadata("dc.title", package_title)]
        return metadata, repos, package_title

    def _analyze_md_els(self, mets_el, dmdids, metadata, repos, package_title):
        """Find metadata elements in ``mets_el`` using id string ``dmdids`` and
        use the data in these elements to modify ``metadata``, ``repos`` and
        ``package_title``, returning these last as a 3-tuple.
        """
        for dmdid in dmdids.split():
            dc_metadata = mets_el.find(
                f'mets:dmdSec[@ID="{dmdid}"]/mets:mdWrap/mets:xmlData/'
                "dcterms:dublincore",
                namespaces=utils.NSMAP,
            )
            other_metadata = mets_el.find(
                f'mets:dmdSec[@ID="{dmdid}"]/mets:mdWrap[@MDTYPE="OTHER"]/mets:xmlData',
                namespaces=utils.NSMAP,
            )
            if other_metadata is not None:
                for repo_key in [
                    "dspace_dip_collection",
                    "dspace_aip_collection",
                    "archivesspace_dip_repository",
                    "archivesspace_dip_collection",
                ]:
                    val = other_metadata.findtext(repo_key)
                    if val is not None:
                        repos[repo_key] = val
            elif dc_metadata is not None:
                for md in dc_metadata:
                    dc_term = str(md.tag)[str(md.tag).find("}") + 1 :]
                    if dc_term == "title":
                        dc_term = "dc." + dc_term
                        package_title = md.text
                    else:
                        dc_term = "dcterms." + dc_term
                    metadata.append(self._format_metadata(dc_term, md.text))
        return metadata, repos, package_title

    @staticmethod
    def _format_metadata(dc, value):
        """Reformats the metadata for the REST API."""
        return {"key": dc, "value": value, "language": ""}

    @staticmethod
    def _get_mets_el(package_type, output_dir, input_path, dirname, aip_uuid):
        """Locate, extract (if necessary), XML-parse and return the METS file
        for this package.
        """
        if package_type == "AIP":
            relative_mets_path = os.path.join(
                dirname, "data", "METS." + aip_uuid + ".xml"
            )
            mets_path = os.path.join(output_dir, relative_mets_path)
            command = [
                "unar",
                "-force-overwrite",
                "-o",
                output_dir,
                input_path,
                relative_mets_path,
            ]
            try:
                subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                ).communicate()
                mets_el = etree.parse(mets_path)
                os.remove(mets_path)
                return mets_el
            except subprocess.CalledProcessError as err:
                raise LogaltyRESTException(
                    f"Could not extract {mets_path} from {input_path}: {err}.")

    def _assign_destination(self, package_type, destinations):
        ds_collection = as_archival_repo = as_archival_obj = None
        if package_type == "DIP":
            ds_collection = destinations.get(
                "dspace_dip_collection", self.ds_dip_collection
            )
            as_archival_repo = destinations.get(
                "archivesspace_dip_repository", self.as_repository
            )
            as_archival_obj = destinations.get(
                "archivesspace_dip_archival_object", self.as_archival_object
            )
        elif package_type == "AIP":
            ds_collection = destinations.get(
                "dspace_aip_collection", self.ds_aip_collection
            )
        return ds_collection, as_archival_repo, as_archival_obj

    def _login_to_storage_rest(self):
        """Log in to get DSpace REST API token."""
        body = {"email": self.logalty_user, "password": self.logalty_pass}
        login_url = f"{self._get_base_url(self.ds_rest_url)}/login"
        try:
            response = self._post(login_url, data=body, headers=None)
            response.raise_for_status()
        except requests.HTTPError as err:
            raise LogaltyRESTException(
                f"Bad response {response.status_code} received when attempting to login via the"
                f" Storage REST API: {err}.",
                url=login_url,
                email=self.logalty_user,
            )
        except Exception as err:
            raise LogaltyRESTException(
                "Unexpected error encountered when attempting to login via the"
                f" Storage REST API: {err}.",
                url=login_url,
                email=self.logalty_user,
            )
        else:
            try:
                set_cookie = response.headers["Set-Cookie"].split(";")[0]
            except KeyError:
                raise LogaltyRESTException(
                    "Unable to login to the Storage REST API: no"
                    ' "Set-Cookie" in response headers:'
                    f" {response.headers}."
                )
            return set_cookie[set_cookie.find("=") + 1 :]

    def _post(self, url, data=None, cookies=None, headers=HEADERS):
        return requests.post(
            url, cookies=cookies, data=data, headers=headers, verify=self.verify_ssl
        )
