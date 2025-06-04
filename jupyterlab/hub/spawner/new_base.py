import os
import asyncio
import logging
import docker
from dockerspawner import DockerSpawner
from traitlets import Unicode, Dict

class MultiNodeSpawner(DockerSpawner):
    host = Unicode("tcp://0.0.0.0:2375", config=True)
    tls_config = Dict({}, config=True)

    node = Unicode("", config=True)
    image = Unicode("", config=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.use_internal_ip = False

    def _get_override_value(self, attr, default, log_message):
        """Helper function to handle IP/Port override logic."""
        value = getattr(self, f"server_{attr}", None)
        if value:
            self.log.info(f"[{log_message}] Using: {value}")
            return value
        return getattr(self, f"_{attr}", default)

    @property
    def ip(self):
        """Override to return remote server IP."""
        return self._get_override_value('ip', '127.0.0.1', 'IP_OVERRIDE')

    @ip.setter
    def ip(self, value):
        """Set IP value."""
        self._ip = value

    @property
    def port(self):
        """Override to return remote server port."""
        return self._get_override_value('port', 8888, 'PORT_OVERRIDE')

    @port.setter
    def port(self, value):
        """Set port value."""
        self._port = int(value)

    @property
    def client(self):
        """Override to ensure the use of the updated Docker client."""
        if not hasattr(self, '_client') or self._client is None:
            self._client = self._get_client()
        elif self._client.base_url != self.host:
            self._client.close() if hasattr(self._client, 'close') else None
            self._client = self._get_client()
        return self._client

    def _get_client(self):
        """Create or get the Docker client."""
        try:
            client = docker.APIClient(base_url=self.host, tls=self.tls_config)
            client.ping()
            self.log.info(f"[DOCKER_CLIENT] Connected to remote Docker: {self.host}")
            return client
        except Exception as e:
            self.log.error(f"[DOCKER_CLIENT] Failed to connect to {self.host}: {e}")
            raise

    async def get_ip_and_port(self):
        """Return remote server IP and port."""
        if self.server_ip and self.server_port:
            result = (self.server_ip, int(self.server_port))
            self.log.info(f"[GET_IP_PORT_OVERRIDE] Using: {result}")
            return result
        return await self._get_ip_and_port()

    def _get_ip_and_port(self):
        """Return internal IP and port."""
        if self.server_ip and self.server_port:
            result = (self.server_ip, int(self.server_port))
            self.log.info(f"[_GET_IP_PORT_OVERRIDE] Using: {result}")
            return result
        result = super()._get_ip_and_port() if hasattr(super(), '_get_ip_and_port') else (self.ip, self.port)
        self.log.info(f"[_GET_IP_PORT_DEFAULT] Using: {result}")
        return result

    @property
    def url(self):
        """Override URL to ensure it points to remote server."""
        if self.server_ip and self.server_port:
            base_url = f"http://{self.server_ip}:{self.server_port}"
            self.log.info(f"[URL_OVERRIDE] Using URL: {base_url}")
            return base_url
        return super().url

    @property
    def server_url(self):
        """Override server URL to ensure consistency."""
        if self.server_ip and self.server_port:
            url = f"http://{self.server_ip}:{self.server_port}"
            self.log.info(f"[SERVER_URL_OVERRIDE] Using server_url: {url}")
            return url
        return super().server_url

    def _set_docker_client(self, client=None):
        """Set Docker client (for compatibility, use _get_client instead)."""
        self._client = client or self._get_client()
        return self._client

    async def start(self):
        """Start the container and setup necessary environment."""
        self.log.info(f"[SPAWNER] user_options: {self.user_options}")

        node_ip = self.user_options.get("node_ip")
        image = self.user_options.get("image", "danielcristh0/jupyterlab:cpu")

        if not node_ip or node_ip in ['127.0.0.1', 'localhost', '0.0.0.0']:
            raise ValueError(f"Invalid or missing remote node IP: {node_ip}")

        self.host = f"tcp://{node_ip}:2375"
        self.tls_config = {}
        self.use_internal_ip = False
        self.image = image
        self._client = None
        client = self.client

        hub_ip = "10.21.73.116"
        self.environment.update({
            'JUPYTERHUB_API_URL': f'http://{hub_ip}:18000/hub/api',
            'JUPYTERHUB_BASE_URL': '/',
            'JUPYTERHUB_SERVICE_PREFIX': f'/user/{self.user.name}/',
            'JUPYTERHUB_USER': self.user.name,
            'JUPYTERHUB_CLIENT_ID': f'jupyterhub-user-{self.user.name}',
            'JUPYTERHUB_API_TOKEN': self.api_token,
            'JUPYTERHUB_SERVICE_URL': f'http://{hub_ip}:18000',
        })

        self.args = [
            '--ServerApp.ip=0.0.0.0',
            '--ServerApp.port=8888',
            '--ServerApp.allow_origin=*',
            '--ServerApp.disable_check_xsrf=True',
            f'--ServerApp.base_url=/user/{self.user.name}/',
            '--ServerApp.allow_remote_access=True',
        ]

        self.extra_host_config = {"runtime": "nvidia"} if any(x in image for x in ["gpu", "cu", "tf", "rpl"]) else {}

        self.extra_host_config.update({
            "port_bindings": {8888: None},
            "extra_hosts": {
                "hub": hub_ip,
                "jupyterhub": hub_ip
            }
        })

        container_id = await super().start()
        self.log.info(f"[DEBUG] Container spawned on: {self.host}")
        self.log.info(f"[DEBUG] Container ID: {container_id}")

        # Let container fully start
        await asyncio.sleep(15)

        # Check container status
        container = self.client.inspect_container(self.container_id)
        container_state = container.get("State", {})
        self.log.info(f"[DEBUG] Container state: {container_state}")

        if not container_state.get("Running", False):
            logs = self.client.logs(self.container_id, tail=100, stdout=True, stderr=True).decode('utf-8')
            self.log.error(f"[ERROR] Container not running. Full logs:\n{logs}")
            raise Exception(f"Container failed to start. Status: {container_state}")

        ports = container["NetworkSettings"]["Ports"]
        self.log.info(f"[DEBUG] Container ports: {ports}")

        if "8888/tcp" not in ports or not ports["8888/tcp"]:
            logs = self.client.logs(self.container_id, tail=50).decode('utf-8')
            self.log.error(f"[ERROR] Port 8888 not exposed. Container logs:\n{logs}")
            raise Exception("Port 8888 not exposed or not found")

        host_port = ports["8888/tcp"][0]["HostPort"]
        self.ip = node_ip
        self.port = int(host_port)
        self.server_ip = node_ip
        self.server_port = str(host_port)

        self.log.info(f"[REMOTE-CONTAINER] Jupyter running at http://{self.ip}:{self.port}")
        self.log.info(f"[REMOTE-CONTAINER] server_url = {self.server_url}")

        return container_id

    async def poll(self):
        """Poll container status."""
        try:
            return await super().poll()
        except Exception as e:
            self.log.error(f"[SPAWNER] Poll failed: {e}")
            return 1

    async def stop(self, now=False):
        """Stop container."""
        try:
            return await super().stop(now)
        except Exception as e:
            self.log.error(f"[SPAWNER] Stop failed: {e}")

    def __del__(self):
        """Clean up Docker client on deletion."""
        if hasattr(self, '_client'):
            try:
                self._client.close()
            except:
                pass