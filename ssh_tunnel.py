"""
SSH tunnel for reaching PostgreSQL through a jump/bastion host.

Opens one SSH connection and listens on a random port on 127.0.0.1. Each local
connection is forwarded over SSH ("direct-tcpip", the same as `ssh -L`) to the
database host/port as seen from the SSH server. The tunnel is reused across
queries and reopened automatically if it drops.

Host keys: keys from ~/.ssh/known_hosts are trusted. SSH_HOST_KEY_POLICY decides
what happens with an unknown server:
    accept-new (default) - trust on first connect and save to ssh_known_hosts
                           next to this file (like StrictHostKeyChecking=accept-new)
    strict               - refuse servers that aren't already known
A server whose key CHANGED is always refused.
"""
import getpass
import io
import os
import select
import socket
import threading

try:
    import paramiko
except ImportError:  # optional dependency
    paramiko = None

HOST_KEY_POLICY = os.getenv("SSH_HOST_KEY_POLICY", "accept-new").strip().lower()
# Key-file browser: folder the Browse dialog may list (default: the app user's home).
# Set KEY_BROWSE_ROOT=off to disable browsing entirely.
KEY_BROWSE_ROOT = os.getenv("KEY_BROWSE_ROOT", "~").strip()
KEY_BROWSE_MAX = 500
KNOWN_HOSTS = os.getenv("SSH_KNOWN_HOSTS",
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "ssh_known_hosts"))

_lock = threading.Lock()
_tunnels = {}


def available():
    return paramiko is not None


def env_config():
    """SSH settings from the environment, or None if SSH_HOST isn't set."""
    host = os.getenv("SSH_HOST", "").strip()
    if not host:
        return None
    return {"host": host, "port": int(os.getenv("SSH_PORT", 22)), "user": os.getenv("SSH_USER", "").strip(),
            "password": os.getenv("SSH_PASSWORD") or None, "key_path": os.getenv("SSH_KEY_FILE") or None,
            "key_text": None, "passphrase": os.getenv("SSH_KEY_PASSPHRASE") or None}


def public(cfg):
    """Config without secrets, for status responses."""
    if not cfg:
        return None
    auth = "key file" if cfg.get("key_path") else "pasted key" if cfg.get("key_text") else \
        "password" if cfg.get("password") else "agent / default keys"
    return {"host": cfg["host"], "port": cfg["port"], "user": cfg["user"], "auth": auth,
            "key_path": cfg.get("key_path") or ""}


def resolve_key_path(raw):
    """Expand ~ and $VARS and strip stray quotes from a key path."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(raw.strip().strip('"').strip("'"))))


def _found_keys():
    """Private-key-looking files in this user's ~/.ssh, to help fix a wrong path."""
    d = os.path.expanduser("~/.ssh")
    skip = {"known_hosts", "known_hosts.old", "config", "authorized_keys", "authorized_keys2", "environment"}
    try:
        return [os.path.join(d, f) for f in sorted(os.listdir(d))
                if f not in skip and not f.endswith(".pub") and os.path.isfile(os.path.join(d, f))]
    except OSError:
        return []


def _key_kind(path):
    """Classify a file from its first bytes. Never returns any content."""
    try:
        if os.path.getsize(path) > 64 * 1024:
            return "other"
        with open(path, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return "unreadable"
    if head.startswith(b"-----BEGIN") and b"PRIVATE KEY" in head:
        return "private"
    if head.startswith(b"PuTTY-User-Key-File"):
        return "ppk"
    if head.startswith((b"ssh-", b"ecdsa-", b"sk-ssh-", b"sk-ecdsa-")):
        return "public"
    return "other"


def browse_root():
    """Absolute, symlink-resolved root of the key browser, or None if browsing is off."""
    if KEY_BROWSE_ROOT.lower() in ("", "off", "none", "false", "0"):
        return None
    return os.path.realpath(os.path.expanduser(os.path.expandvars(KEY_BROWSE_ROOT)))


def browse(path=None):
    """List one folder for the key-file picker: sub-folders and files with their key type.

    Confined to browse_root(); paths that resolve outside it (including via symlinks or '..')
    are refused. Only names, sizes and a type label are returned, never file contents.
    """
    root = browse_root()
    if not root:
        raise PermissionError("Key file browsing is turned off (KEY_BROWSE_ROOT=off). Type the path instead.")
    if path:
        target = os.path.realpath(resolve_key_path(path))
    else:
        ssh_dir = os.path.realpath(os.path.join(root, ".ssh"))
        target = ssh_dir if os.path.isdir(ssh_dir) else root
    if os.path.isfile(target):
        target = os.path.dirname(target)
    if target != root and not target.startswith(root + os.sep):
        raise PermissionError(f"Browsing is limited to {root}. Type the path instead, or change KEY_BROWSE_ROOT.")
    if not os.path.isdir(target):
        raise FileNotFoundError(f"Folder not found: {target}")
    try:
        names = sorted(os.listdir(target), key=lambda n: (n.startswith("."), n.lower()))
    except PermissionError:
        raise PermissionError(f"User '{getpass.getuser()}' can't open {target}.")
    dirs, files = [], []
    for name in names:
        full = os.path.join(target, name)
        real = os.path.realpath(full)
        if real != root and not real.startswith(root + os.sep):
            continue  # symlink pointing outside the root: hide it
        if os.path.isdir(full):
            dirs.append({"name": name, "path": full})
        elif os.path.isfile(full):
            try:
                size = os.path.getsize(full)
            except OSError:
                size = None
            files.append({"name": name, "path": full, "size": size, "kind": _key_kind(full)})
        if len(dirs) + len(files) >= KEY_BROWSE_MAX:
            break
    parent = os.path.dirname(target) if target != root else None
    return {"path": target, "root": root, "parent": parent, "dirs": dirs, "files": files,
            "truncated": len(dirs) + len(files) >= KEY_BROWSE_MAX,
            "user": getpass.getuser(), "host": socket.gethostname()}


def _check_key_path(raw):
    path = resolve_key_path(raw)
    where = f"the app runs as user '{getpass.getuser()}' on '{socket.gethostname()}'"
    if os.path.isdir(path):
        raise ValueError(f"{path} is a folder; enter the private key file inside it, e.g. {path}/id_ed25519.")
    if not os.path.exists(path):
        keys = _found_keys()
        hint = (f" Keys found in {os.path.expanduser('~/.ssh')}: {', '.join(keys)}." if keys
                else f" No private keys found in {os.path.expanduser('~/.ssh')}.")
        raise ValueError(f"SSH key file not found: {path} ({where}; the path must exist on that machine, "
                         f"not on the computer with your browser).{hint} Or choose 'Paste private key' instead.")
    if not os.access(path, os.R_OK):
        raise ValueError(f"SSH key file exists but can't be read by user '{getpass.getuser()}': {path}. "
                         "Fix its permissions or ownership, or choose 'Paste private key'.")
    with open(path, "rb") as fh:
        head = fh.read(64)
    if head.startswith(b"PuTTY-User-Key-File"):
        raise ValueError(f"{path} is a PuTTY .ppk key. Convert it to OpenSSH format first: in PuTTYgen use "
                         "Conversions > Export OpenSSH key, or run: puttygen key.ppk -O private-openssh -o id_key")
    if head.startswith(b"ssh-") or head.startswith(b"ecdsa-"):
        raise ValueError(f"{path} is a public key. Use the private key, the same name without .pub.")
    return path


def _load_key(cfg):
    if not (cfg.get("key_path") or cfg.get("key_text")):
        return None
    pp = cfg.get("passphrase") or None
    path = _check_key_path(cfg["key_path"]) if cfg.get("key_path") else None
    text = (cfg.get("key_text") or "").strip()
    if text.startswith("PuTTY-User-Key-File"):
        raise ValueError("That's a PuTTY .ppk key. In PuTTYgen use Conversions > Export OpenSSH key and paste that.")
    last = None
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            if path:
                return cls.from_private_key_file(path, password=pp)
            return cls.from_private_key(io.StringIO(text + "\n"), password=pp)
        except paramiko.PasswordRequiredException:
            raise ValueError("The SSH key is encrypted; enter its passphrase.")
        except paramiko.SSHException as e:
            last = e
    raise ValueError(f"Couldn't read the SSH private key (wrong passphrase or unsupported format): {last}")


def _open_client(cfg):
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if HOST_KEY_POLICY == "strict":
        if os.path.exists(KNOWN_HOSTS):
            client.load_host_keys(KNOWN_HOSTS)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        if not os.path.exists(KNOWN_HOSTS):
            open(KNOWN_HOSTS, "a").close()
        client.load_host_keys(KNOWN_HOSTS)  # new keys get saved here
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key = _load_key(cfg)
    try:
        client.connect(cfg["host"], port=int(cfg.get("port") or 22), username=cfg.get("user") or None,
                       password=cfg.get("password") or None, pkey=key,
                       passphrase=cfg.get("passphrase") or None,
                       allow_agent=key is None and not cfg.get("password"),
                       look_for_keys=key is None and not cfg.get("password"),
                       timeout=10, banner_timeout=10, auth_timeout=15)
    except paramiko.BadHostKeyException:
        client.close()
        raise RuntimeError(f"SSH host key for {cfg['host']} has CHANGED. Refusing to connect (possible "
                           f"man-in-the-middle). If the server was legitimately rebuilt, remove its line "
                           f"from {KNOWN_HOSTS} or ~/.ssh/known_hosts.")
    except paramiko.AuthenticationException:
        client.close()
        raise RuntimeError(f"SSH login failed for {cfg.get('user')}@{cfg['host']}: check the user, "
                           "password or key.")
    except paramiko.SSHException as e:
        client.close()
        if "not found in known_hosts" in str(e):
            raise RuntimeError(f"SSH server {cfg['host']} isn't in known_hosts and SSH_HOST_KEY_POLICY=strict. "
                               f"Connect once with ssh, or add its key to {KNOWN_HOSTS}.")
        raise RuntimeError(f"SSH error: {e}")
    except (socket.error, OSError) as e:
        client.close()
        raise RuntimeError(f"Can't reach SSH server {cfg['host']}:{cfg.get('port') or 22}: {e}")
    client.get_transport().set_keepalive(30)
    return client


class Tunnel:
    def __init__(self, cfg, remote_host, remote_port):
        self.key = _key(cfg, remote_host, remote_port)
        self.remote = (remote_host, int(remote_port))
        self.client = _open_client(cfg)
        self.transport = self.client.get_transport()
        try:  # check the SSH server can actually reach the database before we claim success
            self.transport.open_channel("direct-tcpip", self.remote, ("127.0.0.1", 0), timeout=10).close()
        except Exception as e:
            self.client.close()
            raise RuntimeError(f"Connected to SSH, but the SSH server couldn't reach the database at "
                               f"{remote_host}:{remote_port} ({e}). Check the database host/port as seen "
                               "from the SSH server (often localhost:5432), and that port forwarding "
                               "(AllowTcpForwarding) is allowed.")
        self.closed = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(32)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def active(self):
        return not self.closed and self.transport is not None and self.transport.is_active()

    def _accept(self):
        while not self.closed:
            try:
                conn, addr = self.sock.accept()
            except OSError:
                break
            try:
                chan = self.transport.open_channel("direct-tcpip", self.remote, addr, timeout=10)
            except Exception:
                conn.close()
                continue
            threading.Thread(target=self._pipe, args=(conn, chan), daemon=True).start()

    @staticmethod
    def _pipe(sock, chan):
        try:
            while True:
                r, _, _ = select.select([sock, chan], [], [], 120)
                if sock in r:
                    data = sock.recv(65536)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(65536)
                    if not data:
                        break
                    sock.sendall(data)
        except Exception:
            pass
        finally:
            chan.close()
            sock.close()

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        finally:
            self.client.close()


def _key(cfg, host, port):
    return (cfg["host"], int(cfg.get("port") or 22), cfg.get("user"), cfg.get("password"), cfg.get("key_path"),
            cfg.get("key_text"), cfg.get("passphrase"), host, int(port))


def ensure(cfg, remote_host, remote_port):
    """Return the local port of a working tunnel for this config, opening one if needed.

    Each distinct SSH config + database address gets its own tunnel, so several
    connections (even through different SSH servers) can be open at once.
    """
    if not available():
        raise RuntimeError("SSH support needs 'pip install paramiko'")
    k = _key(cfg, remote_host, remote_port)
    with _lock:
        t = _tunnels.get(k)
        if t and t.active():
            return t.port
        if t:
            t.close()
        _tunnels[k] = Tunnel(cfg, remote_host, remote_port)
        return _tunnels[k].port


def close(cfg=None, remote_host=None, remote_port=None):
    """Close one tunnel, or all of them when called with no arguments."""
    with _lock:
        keys = list(_tunnels) if cfg is None else [_key(cfg, remote_host, remote_port)]
        for k in keys:
            t = _tunnels.pop(k, None)
            if t:
                t.close()


def state(cfg, remote_host, remote_port):
    t = _tunnels.get(_key(cfg, remote_host, remote_port))
    ok = bool(t and t.active())
    return {"tunnel_open": ok, "local_port": t.port if ok else None}
