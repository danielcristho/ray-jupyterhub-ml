from .base import MultiNodeSpawner
from traitlets import Bool, Unicode

class PatchedMultiNodeSpawner(MultiNodeSpawner):
    use_external_server_url = Bool(True).tag(config=True)
    server_ip = Unicode("").tag(config=True)
    server_port = Unicode("").tag(config=True)

    @property
    def server_url(self):
        """Override server_url to use external server when available"""
        if self.use_external_server_url and self.server_ip and self.server_port:
            url = f"http://{self.server_ip}:{self.server_port}"
            self.log.info(f"[PATCHED_SERVER_URL] Using external: {url}")
            return url

        # Fall back to parent's server_url
        parent_url = super().server_url
        self.log.info(f"[PATCHED_SERVER_URL] Using parent: {parent_url}")
        return parent_url

    @property
    def url(self):
        """Override URL to include default_url path"""
        base_url = self.server_url
        if hasattr(self, 'default_url') and self.default_url:
            if not base_url.endswith('/'):
                base_url += '/'
            if self.default_url.startswith('/'):
                base_url += self.default_url[1:]
            else:
                base_url += self.default_url

        self.log.info(f"[PATCHED_URL] Final URL: {base_url}")
        return base_url

    async def start(self):
        """Override start method with proper server configuration"""

        self.args = [
            "--ServerApp.ip=0.0.0.0",
            "--ServerApp.port=8888",
            f"--ServerApp.base_url=/user/{self.user.name}/",
            "--ServerApp.allow_origin=*",
            "--ServerApp.disable_check_xsrf=True",
            "--ServerApp.allow_remote_access=True",
        ]

        self.log.info(f"[PATCHED_START] Starting with args: {self.args}")

        # Call parent start method
        result = await super().start()

        # ensure server_ip and server_port are set properly, after container started
        if hasattr(self, 'ip') and hasattr(self, 'port') and self.ip and self.port:
            self.server_ip = str(self.ip)
            self.server_port = str(self.port)
            self.log.info(f"[PATCHED_START] Set server_ip={self.server_ip}, server_port={self.server_port}")

        return result

    def get_env(self):
        """Override environment variables"""
        env = super().get_env()

        env["JUPYTERHUB_SERVICE_PREFIX"] = f"/user/{self.user.name}/"
        env["JUPYTERHUB_USER"] = self.user.name
        if "JUPYTERHUB_BASE_URL" not in env:
            env["JUPYTERHUB_BASE_URL"] = "/"

        self.log.info(f"[PATCHED_ENV] Environment variables: {env}")
        return env

    async def get_ip_and_port(self):
        """Override to ensure proper IP and port resolution"""
        result = await super().get_ip_and_port()

        if result and len(result) == 2:
            self.server_ip = str(result[0])
            self.server_port = str(result[1])
            self.log.info(f"[PATCHED_GET_IP_PORT] Updated server_ip={self.server_ip}, server_port={self.server_port}")

        return result