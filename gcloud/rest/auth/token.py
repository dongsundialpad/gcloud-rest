"""Google Cloud auth via service account file"""
import datetime
import enum
import json
import os
import threading
import time
import warnings
from typing import Any   # pylint: disable=unused-import
from typing import Dict  # pylint: disable=unused-import
from typing import List  # pylint: disable=unused-import
from typing import Optional  # pylint: disable=unused-import

try:
    from urllib.parse import urlencode
except ImportError:
    from urllib import urlencode

import backoff
# N.B. the cryptography library is required when calling jwt.encrypt() with
# algorithm='RS256'. It does not need to be imported here, but this allows us
# to throw this error at load time rather than lazily during normal operations,
# where plumbing this error through will require several changes to otherwise-
# good error handling.
import cryptography  # pylint: disable=unused-import
import jwt
import requests


GCE_METADATA_BASE = 'http://metadata.google.internal/computeMetadata/v1'
GCE_METADATA_HEADERS = {'metadata-flavor': 'Google'}
GCE_ENDPOINT_PROJECT = '{}/project/project-id'.format(GCE_METADATA_BASE)
GCE_ENDPOINT_TOKEN = \
    '{}/instance/service-accounts/default/token?recursive=true'\
    .format(GCE_METADATA_BASE)
GCLOUD_TOKEN_DURATION = 3600
REFRESH_HEADERS = {'Content-Type': 'application/x-www-form-urlencoded'}


class Type(enum.Enum):
    AUTHORIZED_USER = 'authorized_user'
    GCE_METADATA = 'gce_metadata'
    SERVICE_ACCOUNT = 'service_account'


def get_service_data(service):
    # type: (Optional[str]) -> Dict[str, Any]
    service = service or os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not service:
        cloudsdk_config = os.environ.get('CLOUDSDK_CONFIG')
        sdkpath = (cloudsdk_config
                   or os.path.join(os.path.expanduser('~'), '.config',
                                   'gcloud'))
        service = os.path.join(sdkpath, 'application_default_credentials.json')
        set_explicitly = bool(cloudsdk_config)
    else:
        set_explicitly = True

    try:
        with open(service, 'r') as f:
            data = json.loads(f.read())
            return data
    except (IOError, OSError):
        # only warn users if they have explicitly set the service_file path
        if set_explicitly:
            raise
        return {}
    except Exception:  # pylint: disable=broad-except
        return {}


class Token(object):
    # pylint: disable=too-many-instance-attributes
    def __init__(self,
                 creds=None,  # type: Optional[str]
                 google_api_lock=None,  # type: Optional[threading.RLock]
                 scopes=None,  # type: Optional[List[str]]
                 timeout=None,  # type: Optional[int]
                 service_file=None,  # type: Optional[str]
                 session=None,  # type: Optional[requests.Session]
                 ):
        # type: (...) -> None
        if creds:
            warnings.warn('creds is now deprecated for Token(),'
                          'please use service_file instead',
                          DeprecationWarning)
            service_file = creds
        if timeout:
            warnings.warn(
                'timeout arg is now deprecated for Token()',
                DeprecationWarning)

        self.service_data = get_service_data(service_file)
        if self.service_data:
            self.token_type = Type(self.service_data['type'])
            self.token_uri = self.service_data.get(
                'token_uri', 'https://oauth2.googleapis.com/token')
        else:
            # At this point, all we can do is assume we're running somewhere
            # with default credentials, eg. GCE.
            self.token_type = Type.GCE_METADATA
            self.token_uri = GCE_ENDPOINT_TOKEN
        self.google_api_lock = google_api_lock or threading.RLock()
        self.session = session
        self.scopes = ' '.join(scopes or [])
        if self.token_type == Type.SERVICE_ACCOUNT and not self.scopes:
            raise Exception('scopes must be provided when token type is '
                            'service account')

        self.access_token = None
        self.access_token_duration = 0
        self.access_token_acquired_at = datetime.datetime(1970, 1, 1)

        self.acquiring = None

    def get_project(self):
        # type: () -> Optional[str]
        project = (os.environ.get('GOOGLE_CLOUD_PROJECT')
                   or os.environ.get('GCLOUD_PROJECT')
                   or os.environ.get('APPLICATION_ID'))

        if self.token_type == Type.GCE_METADATA:
            self.ensure_token()
            with self.google_api_lock:
                resp = self.session.get(GCE_ENDPOINT_PROJECT, timeout=10,
                                        headers=GCE_METADATA_HEADERS)
            resp.raise_for_status()
            project = project or resp.text
        elif self.token_type == Type.SERVICE_ACCOUNT:
            project = project or self.service_data.get('project_id')

        return project

    def get(self):
        # type: () -> str
        self.ensure_token()
        return self.access_token

    def __str__(self):
        # type: () -> str
        return str(self.get())

    def acquire(self):
        # type: () -> str
        warnings.warn('Token.acquire() is deprecated',
                      'please use Token.acquire_access_token()',
                      DeprecationWarning)
        return self.acquire_access_token()

    def ensure_token(self):
        # type: () -> None
        if not self.access_token:
            self.acquire_access_token()
            return

        now = datetime.datetime.utcnow()
        delta = (now - self.access_token_acquired_at).total_seconds()
        if delta <= self.access_token_duration / 2:
            return

        self.acquire_access_token()

    def ensure(self):
        # type: () -> None
        warnings.warn('Token.ensure() is deprecated',
                      'please use Token.ensure_token()',
                      DeprecationWarning)
        self.ensure_token()

    def _refresh_authorized_user(self, timeout):
        # type: (int) -> requests.Response
        payload = urlencode({
            'grant_type': 'refresh_token',
            'client_id': self.service_data['client_id'],
            'client_secret': self.service_data['client_secret'],
            'refresh_token': self.service_data['refresh_token'],
        })
        with self.google_api_lock:
            return self.session.post(self.token_uri, data=payload,
                                     headers=REFRESH_HEADERS, timeout=timeout)

    def _refresh_gce_metadata(self, timeout):
        # type: (int) -> requests.Response
        with self.google_api_lock:
            return self.session.get(self.token_uri,
                                    headers=GCE_METADATA_HEADERS,
                                    timeout=timeout)

    def _refresh_service_account(self, timeout):
        # type: (int) -> requests.Response
        now = int(time.time())
        assertion_payload = {
            'aud': self.token_uri,
            'exp': now + GCLOUD_TOKEN_DURATION,
            'iat': now,
            'iss': self.service_data['client_email'],
            'scope': self.scopes,
        }

        # N.B. algorithm='RS256' requires an extra 240MB in dependencies...
        assertion = jwt.encode(assertion_payload,
                               self.service_data['private_key'],
                               algorithm='RS256')
        payload = urlencode({
            'assertion': assertion,
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        })
        with self.google_api_lock:
            return self.session.post(self.token_uri, data=payload,
                                     headers=REFRESH_HEADERS, timeout=timeout)

    @backoff.on_exception(backoff.expo, Exception, max_tries=5)  # type: ignore
    def acquire_access_token(self, timeout=10):
        # type: (int) -> None
        if not self.session:
            self.session = requests.Session()

        if self.token_type == Type.AUTHORIZED_USER:
            resp = self._refresh_authorized_user(timeout=timeout)
        elif self.token_type == Type.GCE_METADATA:
            resp = self._refresh_gce_metadata(timeout=timeout)
        elif self.token_type == Type.SERVICE_ACCOUNT:
            resp = self._refresh_service_account(timeout=timeout)
        else:
            raise Exception(
                'unsupported token type {}'.format(self.token_type))

        resp.raise_for_status()
        content = resp.json()

        self.access_token = str(content['access_token'])
        self.access_token_duration = int(content['expires_in'])
        self.access_token_acquired_at = datetime.datetime.utcnow()
        self.acquiring = None
