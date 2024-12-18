"""Envoy authentication methods."""

from abc import abstractmethod, abstractproperty
from typing import Any, cast

import httpx
import jwt
import orjson
from tenacity import retry, retry_if_exception_type, wait_random_exponential

from .const import LOCAL_TIMEOUT, URL_AUTH_CHECK_JWT
from .exceptions import EnvoyAuthenticationError, EnvoyAuthenticationRequired
from .ssl import SSL_CONTEXT


class EnvoyAuth:
    """Base class for local Envoy authentication."""

    def __init__(self, host: str) -> None:
        """Base class for local Envoy authentication.

        :param host: local Envoy DNS name or IP Address

        """

    @abstractmethod
    async def setup(self, client: httpx.AsyncClient) -> None:
        """Setup token based authentication with the local Envoy.

        Required for Envoy firmware >= 7.0

        :param client: a httpx Async client to communicate with the local Envoy,

        """

    @abstractproperty
    def cookies(self) -> dict[str, str]:
        """Return the Envoy cookie."""

    @abstractproperty
    def auth(self) -> httpx.DigestAuth | None:
        """Setup Digest authentication for local Envoy.

        Required for Envoy firmware < 7.0

        """

    @abstractproperty
    def headers(self) -> dict[str, str]:
        """Return the auth headers for Envoy communication."""

    @abstractmethod
    def get_endpoint_url(self, endpoint: str) -> str:
        """Return the URL for the endpoint.

        :param endpoint: Envoy Endpoint to access, start with leading /

        :return: formatted full URL string
        """


class EnvoyTokenAuth(EnvoyAuth):
    """Class to authenticate with Envoy using Tokens."""

    # autodoc docstring is supplied from __init__

    JSON_LOGIN_URL = "https://enlighten.enphaseenergy.com/login/login.json?"
    TOKEN_URL = "https://entrez.enphaseenergy.com/tokens"  # nosec

    def __init__(
        self,
        host: str,
        cloud_username: str | None = None,
        cloud_password: str | None = None,
        envoy_serial: str | None = None,
        token: str | None = None,
    ) -> None:
        """Class to authenticate with Envoy using Tokens.

        Use with Envoy firmware 7.x and newer

        :param host: local Envoy DNS name or IP Address
        :param cloud_username: Enligthen Cloud username, required to obtain new
            token when token is not specified or expired, defaults to None
        :param cloud_password: Enligthen Cloud password, required to obtain new
            token when token is not specified or expired, defaults to None
        :param envoy_serial: Envoy serial number, required to obtain new
            token when token is not specified or expired, defaults to None
        :param token: Token to use with authentication, if not specified,
            one will be obtained from Enlighten cloud if username, password
            and serial are specified, defaults to None

        """
        self.host = host
        self.cloud_username = cloud_username
        self.cloud_password = cloud_password
        self.envoy_serial = envoy_serial
        self._token = token
        self._is_consumer: bool = False
        self._manager_token: str = ""
        self._cookies: dict[str, str] = {}

    async def setup(self, client: httpx.AsyncClient) -> None:
        """Setup token based authentication with the local Envoy

        If no token is specified, a token is obtained from Enlighten Cloud using
        specified username, password and serialnumber. With the specified or obtained
        token, validates the token with the local Envoy. New or updated token
        can be accessed using the token property. Token is not stored persistent,
        caller should store and specify token over restarts.

        :param client: a httpx Async client to communicate with the local Envoy,
        :raises EnvoyAuthenticationError: Authentication failed with the local Envoy
            or no token could be obtained from Enlighten cloud due to error or
            missing parameters,

        """
        if not self._token:
            self._token = await self._obtain_token()

        # Verify we have adequate credentials
        if not self._token:
            raise EnvoyAuthenticationError(
                "Unable to obtain token for Envoy authentication."
            )

        await self._check_jwt(client)

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_random_exponential(multiplier=2, max=3),
    )
    async def _check_jwt(self, client: httpx.AsyncClient) -> None:
        """Check the JWT token for Envoy authentication."""
        req = await client.get(
            f"https://{self.host}{URL_AUTH_CHECK_JWT}",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=LOCAL_TIMEOUT,
        )
        if req.status_code == 200:
            self._cookies = req.cookies
            return

        raise EnvoyAuthenticationError(
            "Unable to verify token for Envoy authentication."
        )

    async def _obtain_token(self) -> str:
        """Obtain the token for Envoy authentication."""
        # Raise if we don't have cloud credentials
        if not self.cloud_username or not self.cloud_password:
            raise EnvoyAuthenticationError(
                "Your firmware requires token authentication, "
                " but no cloud credentials were provided to obtain the token."
            )
        # Raise if we are missing the envoy serial number
        if not self.envoy_serial:
            raise EnvoyAuthenticationError(
                "Your firmware requires token authentication, "
                "but no envoy serial number was provided to obtain the token."
            )
        # We require a new client that checks SSL certs
        async with httpx.AsyncClient(
            verify=SSL_CONTEXT, timeout=10, follow_redirects=True
        ) as cloud_client:
            # Login to Enlighten to obtain a session ID
            response = await self._post_json_with_cloud_client(
                cloud_client,
                self.JSON_LOGIN_URL,
                data={
                    "user[email]": self.cloud_username,
                    "user[password]": self.cloud_password,
                },
            )
            if response.status_code != 200:
                raise EnvoyAuthenticationError(
                    "Unable to login to Enlighten to obtain session ID from "
                    f"{self.JSON_LOGIN_URL}: "
                    f"{response.status_code}: {response.text}"
                )
            try:
                response = orjson.loads(response.text)
            except orjson.JSONDecodeError as err:
                raise EnvoyAuthenticationError(
                    "Unable to decode response from Enlighten: "
                    f"{response.status_code}: {response.text}"
                ) from err

            self._is_consumer = response["is_consumer"]
            self._manager_token = response["manager_token"]

            # Obtain the token
            response = await self._post_json_with_cloud_client(
                cloud_client,
                self.TOKEN_URL,
                json={
                    "session_id": response["session_id"],
                    "serial_num": self.envoy_serial,
                    "username": self.cloud_username,
                },
            )
            if response.status_code != 200:
                raise EnvoyAuthenticationError(
                    "Unable to obtain token for Envoy authentication from "
                    f"{self.TOKEN_URL}: "
                    f"{response.status_code}: {response.text}"
                )
            return response.text

    async def refresh(self) -> None:
        """Refresh the token for Envoy authentication.

        Retrieves a new token from the Enlighten cloud using
        specified username, password and Envoy serial number of
        the class object. Updated token can be accessed
        using the token property. Token is not stored persistent,
        caller should store it after refresh and specify token
        over restarts.

        """
        self._token = await self._obtain_token()

    @property
    def expire_timestamp(self) -> int:
        """Return the expiration time for the token.

        Owner useraccount type tokens are valid for a year
        while installer tokens are only valid for 12 hours.

        :return: epoch expiration time
        """
        jwt_payload = jwt.decode(self.token, options={"verify_signature": False})
        return cast(int, jwt_payload["exp"])

    @property
    def token_type(self) -> str:
        """Return the enphase user type for the token.

        Enlighten user accounts can be type 'owner' or 'installer'. Both
        have access to the envoy base data. Installer has access to more
        data and configuration setup.

        :raises: EnvoyAuthenticationRequired if no prior authentication was done
        :return: 'owner' or 'installer'
        """
        if not self._token:
            raise EnvoyAuthenticationRequired(
                "You must authenticate to the Envoy before inspecting token."
            )
        jwt_payload = jwt.decode(self.token, options={"verify_signature": False})
        return jwt_payload["enphaseUser"]

    @retry(
        retry=retry_if_exception_type(
            (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError)
        ),
        wait=wait_random_exponential(multiplier=2, max=3),
    )
    async def _post_json_with_cloud_client(
        self,
        cloud_client: httpx.AsyncClient,
        url: str,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Post to the Envoy API with the cloud client."""
        return await cloud_client.post(url, json=json, data=data)

    @property
    def token(self) -> str:
        """Return token used with the Envoy.

        Returns the current token, either the original specified token,
        or the token obtained from the Enlighten cloud if not specified
        or the refreshed token at expiration.

        Will assert if no token was ever specified or obtained.

        :return: jwt token string
        """
        assert self._token is not None  # nosec
        return self._token

    @property
    def manager_token(self) -> str:
        """Return manager token returned in enligthen login json.

        This property is only available if a token has been requested
        from the Enlighten cloud. This is only the case if no token
        was specified, or a token refresh was requested. If a valid
        token with a future expiration time was specified this method
        will assert.

        :return: token string
        """
        assert self._manager_token is not None  # nosec
        return self._manager_token

    @property
    def cookies(self) -> dict[str, str]:
        """return cookies returned during setup of the envoy.

        Cookies received from the local Envoy during setup and local
        jwt check are stored in the class, this method returns these.

        :return: cookies dict
        """
        return self._cookies

    @property
    def is_consumer(self) -> bool:
        """Return is_consumer state returned in enligthen login json

        This property is only available if a token has been requested
        from the Enlighten cloud. This is only the case if no token
        was specified, or a token refresh was requested. If a valid
        token with a future expiration time was specified no login was
        attempted and this method will return the default false.
        If an installer account was used it will return false as well.

        :return: true if enlighten login was performed and
            used credentials are for consumer account, otherwise false
        """
        return self._is_consumer

    @property
    def auth(self) -> None:
        """Digest authentication for local Envoy.

        Not used with token authentication. Placeholder
        for EnvoyAuth abstractproperty

        :return: None
        """
        return None

    @property
    def headers(self) -> dict[str, str]:
        """Return the authentication headers for Envoy communication.

        Token authorization with Envoy requires an Authorization header
        in Bearer format with token.

        :return: token authorization header
        """
        return {"Authorization": f"Bearer {self.token}"}

    def get_endpoint_url(self, endpoint: str) -> str:
        """Return the URL for the endpoint.

        :param endpoint: Envoy Endpoint to access, start with leading /

        :return: formatted https URL string
        """
        return f"https://{self.host}{endpoint}"


class EnvoyLegacyAuth(EnvoyAuth):
    """Class to authenticate with legacy Envoy using digest."""

    def __init__(self, host: str, username: str, password: str) -> None:
        """Class to authenticate with legacy Envoy using digest.

        Use with Envoy firmware before 7.0

        :param host: local Envoy DNS name or IP Address
        :param local_username: Username to access Envoy
        :param local_password: Password to access Envoy
        """
        self.host = host
        self.local_username = username
        self.local_password = password

    @property
    def auth(self) -> httpx.DigestAuth:
        """Digest authentication for local Envoy.

        Creates DigestAuth based on username and password.

        :return: DigestAuthentication for local Envoy or None
            if username and/or password are not specified
        """
        if not self.local_username or not self.local_password:
            return None
        return httpx.DigestAuth(self.local_username, self.local_password)

    async def setup(self, client: httpx.AsyncClient) -> None:
        """Setup authentication with the local Envoy

        DigestAuth does not use additional setup,
        placeholder for EnvoyAuth abstractpropery.

        :param client: Client to communicate with local Envoy
        """
        # No setup required for legacy authentication

    @property
    def headers(self) -> dict[str, str]:
        """Return the headers needed for Envoy authentication.

        DigestAuth does not use authorization header. Placeholder
        for EnvoyAuth abstractproperty.

        :return: empty dict
        """
        return {}

    def get_endpoint_url(self, endpoint: str) -> str:
        """Return the URL for the endpoint.

        :param endpoint: Envoy Endpoint to access, start with leading /

        :return: formatted http URL string
        """
        return f"http://{self.host}{endpoint}"

    @property
    def cookies(self) -> dict[str, str]:
        """return cookies returned during setup of the envoy.

        DigestAuth does not use cookies. Placeholder
        for EnvoyAuth abstractproperty.

        :return: empty dict
        """
        return {}
