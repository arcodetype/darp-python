import argparse
import json
import os
import subprocess
import signal
import sys
from datetime import datetime

from colorama import Fore, Style, init

init()

# ---------------------------------------------------------------------------
# Constants / Paths
# ---------------------------------------------------------------------------

HOME_DIRECTORY = os.path.expanduser("~")
DARP_ROOT_ENV = os.environ.get("DARP_ROOT", f"{HOME_DIRECTORY}/.darp")
DARP_ROOT = os.path.join(DARP_ROOT_ENV, "")
PODMAN_MACHINE_ENV = os.environ.get("PODMAN_MACHINE", 'podman-machine-default')

CONFIG_PATH = os.path.join(DARP_ROOT, "config.json")
PORTMAP_PATH = os.path.join(DARP_ROOT, "portmap.json")
DNSMASQ_DIR = os.path.join(DARP_ROOT, "dnsmasq.d")
VHOST_CONTAINER_CONF = os.path.join(DARP_ROOT, "vhost_container.conf")
HOSTS_CONTAINER_PATH = os.path.join(DARP_ROOT, "hosts_container")
NGINX_CONF_PATH = os.path.join(DARP_ROOT, "nginx.conf")
RESOLVER_FILE = "/etc/resolver/test"

REVERSE_PROXY_CONTAINER = "darp-reverse-proxy"
DNSMASQ_CONTAINER = "darp-masq"

PSEUDO_PWD_TOKEN = "{pwd}"  # new proprietary token for "current directory"

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def run_podman_interactive(podman_command, container_name: str | None = None, restart_on: set[int] | None = None):
    """
    Run `podman run ...` in the foreground, handle Ctrl+C and optional auto-restart.

    - On Ctrl+C: wait for podman to exit; if it doesn't, `podman stop` the container.
    - If restart_on is provided and the exit code is in that set, auto-restart.
    """
    if restart_on is None:
        restart_on = set()

    while True:
        proc = subprocess.Popen(podman_command)

        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            # Python got SIGINT, podman also did (same process group).
            # Give it a moment to shut down gracefully; if not, stop it.
            print(f"\nStopping {Fore.CYAN}{container_name or 'container'}{Style.RESET_ALL} (Ctrl+C)")
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if container_name:
                    subprocess.run(
                        ["podman", "stop", container_name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
            break

        # Auto-restart logic
        if rc in restart_on:
            if container_name:
                print(f"restarting {Fore.CYAN}{container_name}{Style.RESET_ALL}")
            # loop back around and start a fresh `podman run`
            continue

        # Normal exit or non-restartable error
        break


def run_command(cmd, **kwargs):
    """Wrapper around subprocess.run with check=True by default."""
    kwargs.setdefault("check", True)
    return subprocess.run(cmd, **kwargs)


def run_command_capture(cmd, text=True):
    """Wrapper around subprocess.check_output."""
    return subprocess.check_output(cmd, text=text)


def get_nested(d, keys):
    """Safely traverse nested dict using a list of keys."""
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
        if d is None:
            return None
    return d


def get_config(filename):
    """Load a JSON config file; create an empty one if missing."""
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    if not os.path.exists(filename):
        with open(filename, "w") as f:
            json.dump({}, f, indent=4)
        print(f"Created {filename}")

    with open(filename) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            print(f"Not valid JSON: {filename}")
            sys.exit(1)


def get_running_darps():
    """Return list of running darp_* containers."""
    try:
        output = run_command_capture(["podman", "ps", "--format", "{{.Names}}"])
        running_containers = output.strip().splitlines()
        return [name for name in running_containers if name.startswith("darp_")]
    except Exception:
        return []


def is_init_initialized():
    """Check if /etc/resolver/test is set with nameserver 127.0.0.1."""
    try:
        result = run_command_capture(["cat", RESOLVER_FILE]).strip()
        return result == "nameserver 127.0.0.1"
    except Exception:
        return False

def is_machine_rootful(machine_name: str | None = None) -> bool:
    """
    Return True if the podman machine is configured as rootful.
    If detection fails, default to False (treat as rootless).
    """
    if machine_name is None:
        machine_name = PODMAN_MACHINE_ENV

    if not machine_name:
        return False

    try:
        output = run_command_capture(
            ["podman", "machine", "inspect", machine_name, "--format", "{{.Rootful}}"]
        )
        return output.strip().lower() == "true"
    except Exception:
        # If anything goes wrong, fall back to rootless behavior.
        return False


def is_podman_running():
    """
    Check if the relevant podman machine is running.

    - If PODMAN_MACHINE is set, ensure *that* machine has Running == true.
    - Otherwise, return True if *any* machine is Running == true.
    """
    try:
        output = run_command_capture(
            ["podman", "machine", "list", "--format", "{{.Name}} {{.Running}}"]
        )
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return False

        if PODMAN_MACHINE_ENV:
            # look for the specific machine
            for line in lines:
                parts = line.split(maxsplit=1)
                if len(parts) != 2:
                    continue
                raw_name, running = parts
                # strip trailing '*' that marks the active machine
                name = raw_name.rstrip("*")

                if name == PODMAN_MACHINE_ENV:
                    return running.lower() == "true"
            # machine name not found in list
            return False

        # otherwise, any running machine is fine
        for line in lines:
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            _, running = parts
            if running.lower() == "true":
                return True

        return False
    except Exception:
        return False



def is_unprivileged_port_start(expected_port):
    """
    Check that net.ipv4.ip_unprivileged_port_start <= expected_port
    on the target machine.

    For rootful machines, we skip this check (root can bind to privileged
    ports anyway), and simply return True.
    """
    try:
        machine_name = PODMAN_MACHINE_ENV

        # If the machine is rootful, we don't care about unprivileged_port_start.
        if is_machine_rootful(machine_name):
            return True

        cmd = ["podman", "machine", "ssh"]
        if machine_name:
            cmd.append(machine_name)
        # -n returns just the value, so parsing is simpler
        cmd.extend(["sysctl", "-n", "net.ipv4.ip_unprivileged_port_start"])

        result = run_command_capture(cmd).strip()
        actual_port = int(result)
        return actual_port <= expected_port
    except Exception:
        return False



def is_container_running(container_name):
    """Check if a specific container is running."""
    try:
        output = run_command_capture(
            ["podman", "container", "ls", "--format", "{{.Names}}"]
        )
        running_containers = output.strip().splitlines()
        return container_name in running_containers
    except subprocess.CalledProcessError as e:
        print(f"Error checking containers: {e}")
        return False


def start_reverse_proxy():
    """Start the nginx reverse proxy container if not running."""
    if is_container_running(REVERSE_PROXY_CONTAINER):
        return True

    start_command = [
        "podman",
        "run",
        "-d",
        "--rm",
        "--name",
        REVERSE_PROXY_CONTAINER,
        "-p",
        "80:80",
        "-v",
        f"{VHOST_CONTAINER_CONF}:/etc/nginx/conf.d/vhost_container.conf",
        "nginx",
    ]

    print(f"starting {Fore.GREEN}{REVERSE_PROXY_CONTAINER}{Style.RESET_ALL}\n")

    subprocess.Popen(
        start_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def restart_reverse_proxy():
    """Restart the reverse proxy container, or start it if not running."""
    if not is_container_running(REVERSE_PROXY_CONTAINER):
        return start_reverse_proxy()

    restart_command = ["podman", "restart", REVERSE_PROXY_CONTAINER]

    print(f"restarting {Fore.GREEN}{REVERSE_PROXY_CONTAINER}{Style.RESET_ALL}")

    subprocess.Popen(
        restart_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def start_darp_masq():
    """Start the dnsmasq container if not running."""
    if is_container_running(DNSMASQ_CONTAINER):
        return True

    start_command = [
        "podman",
        "run",
        "-d",
        "--rm",
        "--name",
        DNSMASQ_CONTAINER,
        "-p",
        "53:53/udp",
        "-p",
        "53:53/tcp",
        "-v",
        f"{DNSMASQ_DIR}:/etc/dnsmasq.d",
        "--cap-add=NET_ADMIN",
        "dockurr/dnsmasq",
    ]

    print(f"starting {Fore.GREEN}{DNSMASQ_CONTAINER}{Style.RESET_ALL}\n")

    subprocess.Popen(
        start_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def stop_running_darp(name):
    """Stop a single darp_* container."""
    print(f"stopping {Fore.CYAN}{name}{Style.RESET_ALL}")
    stop_command = ["podman", "stop", name]

    subprocess.Popen(
        stop_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def stop_running_darps():
    """Stop all darp_* containers."""
    for darp in get_running_darps():
        stop_running_darp(darp)


def resolve_host_path(template: str, current_directory: str) -> str:
    """
    Resolve the host path template using our proprietary token and legacy $(pwd).
    - {pwd}   -> current_directory (preferred)
    - $(pwd)  -> current_directory (backwards-compatible)
    """
    return (
        template.replace(PSEUDO_PWD_TOKEN, current_directory)
        .replace("$(pwd)", current_directory)
    )


def resolve_image_name(environment: dict | None, cli_image: str) -> str:
    """
    Resolve the image name using environment.image_repository if set.
    If image_repository exists, we build: "<image_repository>:<cli_image>".
    Otherwise, we just use cli_image.
    """
    if environment and "image_repository" in environment:
        return f"{environment['image_repository']}:{cli_image}"
    return cli_image


# ---------------------------------------------------------------------------
# Command Line Functions
# ---------------------------------------------------------------------------


def run_init(_args):
    print("Running initialization")

    # Create the resolver directory on the host
    run_command(["sudo", "mkdir", "-p", "/etc/resolver"])

    # Write the resolver file on the host
    run_command(
        ["sudo", "tee", RESOLVER_FILE],
        input="nameserver 127.0.0.1\n",
        text=True,
    )
    print(f"\n{Fore.GREEN}{RESOLVER_FILE}{Style.RESET_ALL} created")

    os.makedirs(DNSMASQ_DIR, exist_ok=True)

    # Copy nginx.conf to DARP_ROOT
    run_command(["cp", "nginx.conf", DARP_ROOT])

    test_conf_path = os.path.join(DNSMASQ_DIR, "test.conf")
    with open(test_conf_path, "w") as file:
        file.write("address=/.test/127.0.0.1\n")

    print(f"{Fore.GREEN}{test_conf_path}{Style.RESET_ALL} created")

    # ------------------------------------------------------------------
    # Configure unprivileged port start inside the Podman machine
    # ------------------------------------------------------------------
    machine_name = PODMAN_MACHINE_ENV

    if is_machine_rootful(machine_name):
        print(
            f"Podman machine '{machine_name}' is {Fore.GREEN}rootful{Style.RESET_ALL}; "
            "binding to port 53 does not require changing "
            "net.ipv4.ip_unprivileged_port_start. Skipping sysctl configuration."
        )
        return

    ssh_cmd = ["podman", "machine", "ssh"]
    if machine_name:
        ssh_cmd.append(machine_name)

    # 1) Remove any existing ip_unprivileged_port_start lines from /etc/sysctl.conf
    # 2) Append our desired value
    # 3) Reload sysctl settings
    ssh_cmd.extend([
        "sh",
        "-c",
        (
            "sudo sed -i '/^net\\.ipv4\\.ip_unprivileged_port_start/d' /etc/sysctl.conf; "
            "echo 'net.ipv4.ip_unprivileged_port_start=53' | "
            "sudo tee -a /etc/sysctl.conf >/dev/null; "
            "sudo sysctl --system"
        ),
    ])

    print(
        f"Configuring unprivileged ports in podman machine "
        f"{Fore.GREEN}{machine_name}{Style.RESET_ALL}..."
    )

    try:
        run_command(ssh_cmd)
        print(
            f"{Fore.GREEN}net.ipv4.ip_unprivileged_port_start=53{Style.RESET_ALL} "
            "set inside podman machine."
        )
    except subprocess.CalledProcessError:
        print(
            f"{Fore.RED}Warning:{Style.RESET_ALL} failed to configure "
            "unprivileged port 53 inside podman machine.\n"
            "You may need to run this manually.\n"
        )


def run_deploy(_args):
    print("Deploying Container Development\n")

    user_config = get_config(CONFIG_PATH)

    domains = user_config.get("domains")
    if not domains:
        print("Please configure a domain.")
        sys.exit(1)

    hosts_container_lines = []
    portmap = {}
    port_number = 50100
    vhost_container = []

    podman_host_template = """server {{
    listen 80;
    server_name {url};
    location / {{
        proxy_pass http://host.containers.internal:{port}/;
        proxy_set_header Host $host;
    }}
}}

"""

    for domain_name, domain in domains.items():
        portmap[domain_name] = {}
        location = domain["location"]

        process = run_command(
            ["sh", "-c", f"ls -l {location} | grep drwxr-xr | awk '{{print $9}}'"],
            capture_output=True,
            text=True,
            check=False,
        )
        folders = process.stdout.splitlines()

        for folder in folders:
            portmap[domain_name][folder] = port_number
            url = f"{folder}.{domain_name}.test"
            hosts_container_lines.append(f"0.0.0.0   {url}\n")
            vhost_container.append(
                podman_host_template.format(url=url, port=port_number)
            )
            port_number += 1

    with open(HOSTS_CONTAINER_PATH, "w") as file:
        file.writelines(hosts_container_lines)

    with open(PORTMAP_PATH, "w") as f:
        json.dump(portmap, f, indent=4)

    with open(VHOST_CONTAINER_CONF, "w") as file:
        file.writelines(vhost_container)

    restart_reverse_proxy()
    stop_running_darps()


def run_add_portmap(args):
    user_config = get_config(CONFIG_PATH)

    existing_host_portmapping = get_nested(
        user_config,
        [
            "domains",
            args.domain_name,
            "services",
            args.service_name,
            "host_portmappings",
            args.host_port,
        ],
    )

    if existing_host_portmapping is not None:
        print(
            f"Portmapping on host side '{args.domain_name}.{args.service_name}' "
            f"({args.host_port}:____) already exists"
        )
        sys.exit(1)

    domains = user_config.get("domains") or {}
    domain = domains.get(args.domain_name)
    if domain is None:
        print(f"domain, {args.domain_name}, does not exist")
        sys.exit(1)

    services = domain.setdefault("services", {})
    service = services.setdefault(args.service_name, {})
    host_portmappings = service.setdefault("host_portmappings", {})

    host_portmappings[args.host_port] = args.container_port

    with open(CONFIG_PATH, "w") as f:
        json.dump(user_config, f, indent=4)

    print(
        f"Created portmapping for '{args.domain_name}.{args.service_name}' "
        f"({args.host_port}:{args.container_port})"
    )


def run_remove_portmap(args):
    user_config = get_config(CONFIG_PATH)

    existing_host_portmapping = get_nested(
        user_config,
        [
            "domains",
            args.domain_name,
            "services",
            args.service_name,
            "host_portmappings",
            args.host_port,
        ],
    )

    if existing_host_portmapping is None:
        print(
            f"Portmapping on host side '{args.domain_name}.{args.service_name}' "
            f"({args.host_port}:____) does not exist"
        )
        sys.exit(1)

    del user_config["domains"][args.domain_name]["services"][args.service_name][
        "host_portmappings"
    ][args.host_port]

    with open(CONFIG_PATH, "w") as f:
        json.dump(user_config, f, indent=4)

    print(
        f"Removed portmapping for '{args.domain_name}.{args.service_name}' "
        f"({args.host_port}:____)"
    )


def run_add_domain(args):
    user_config = get_config(CONFIG_PATH)

    existing_domain = get_nested(user_config, ["domains", args.name])
    if existing_domain is not None:
        print(f"domain {args.name} already exists at {existing_domain['location']}")
        sys.exit(1)

    user_config.setdefault("domains", {})
    user_config["domains"][args.name] = {
        "location": args.location,
    }

    with open(CONFIG_PATH, "w") as f:
        json.dump(user_config, f, indent=4)

    print(f"created '{args.name}' at {args.location}")


def run_remove_domain(args):
    user_config = get_config(CONFIG_PATH)

    existing_domain = get_nested(user_config, ["domains", args.name])
    if existing_domain is None:
        print(f"domain, {args.name}, does not exist")
        sys.exit(1)

    del user_config["domains"][args.name]

    with open(CONFIG_PATH, "w") as f:
        json.dump(user_config, f, indent=4)

    print(f"removed '{args.name}'")


def run_add_volume(args):
    """
    darp add volume <environment> <container_dir> <host_dir>

    host_dir may include the proprietary {pwd} token to be resolved at runtime.
    """
    user_config = get_config(CONFIG_PATH)
    environments = user_config.get("environments") or {}

    if not environments:
        print("No environments configured. Use 'darp add domain' and update config.")
        sys.exit(1)

    env = environments.get(args.environment)
    if env is None:
        print(f"Environment '{args.environment}' does not exist.")
        sys.exit(1)

    volumes = env.setdefault("volumes", [])

    new_volume = {
        "container": args.container_dir,
        "host": args.host_dir,
    }

    # Avoid duplicate volume entries
    for v in volumes:
        if v.get("container") == new_volume["container"] and v.get("host") == new_volume["host"]:
            print(
                f"Volume mapping already exists for environment '{args.environment}': "
                f"{new_volume['host']} -> {new_volume['container']}"
            )
            sys.exit(1)

    volumes.append(new_volume)

    with open(CONFIG_PATH, "w") as f:
        json.dump(user_config, f, indent=4)

    print(
        f"Added volume to environment '{args.environment}': "
        f"{new_volume['host']} -> {new_volume['container']}"
    )


def run_set_serve_command(args):
    """
    darp set serve_command <environment> <serve_command>
    """
    user_config = get_config(CONFIG_PATH)
    environments = user_config.get("environments") or {}

    if not environments:
        print("No environments configured. Use 'darp add domain' and update config.")
        sys.exit(1)

    env = environments.get(args.environment)
    if env is None:
        print(f"Environment '{args.environment}' does not exist.")
        sys.exit(1)

    env["serve_command"] = args.serve_command

    with open(CONFIG_PATH, "w") as f:
        json.dump(user_config, f, indent=4)

    print(
        f"Set serve_command for environment '{args.environment}' to:\n"
        f"  {args.serve_command}"
    )


def run_set_image_repository(args):
    """
    darp set image_repository <environment> <image_repository>
    """
    user_config = get_config(CONFIG_PATH)
    environments = user_config.get("environments") or {}

    if not environments:
        print("No environments configured. Use 'darp add domain' and update config.")
        sys.exit(1)

    env = environments.get(args.environment)
    if env is None:
        print(f"Environment '{args.environment}' does not exist.")
        sys.exit(1)

    env["image_repository"] = args.image_repository

    with open(CONFIG_PATH, "w") as f:
        json.dump(user_config, f, indent=4)

    print(
        f"Set image_repository for environment '{args.environment}' to:\n"
        f"  {args.image_repository}"
    )


def run_rm_serve_command(args):
    """
    darp rm serve_command <environment>
    """
    user_config = get_config(CONFIG_PATH)
    environments = user_config.get("environments") or {}

    # Guard: only allowed if some env has serve_command (per spec)
    if not any(
        isinstance(env, dict) and "serve_command" in env for env in environments.values()
    ):
        print("No environments with serve_command set. Use 'darp set serve_command' first.")
        sys.exit(1)

    env = environments.get(args.environment)
    if env is None:
        print(f"Environment '{args.environment}' does not exist.")
        sys.exit(1)

    if "serve_command" not in env:
        print(
            f"Environment '{args.environment}' has no custom serve_command. "
            "Use 'darp set serve_command' first."
        )
        sys.exit(1)

    del env["serve_command"]

    with open(CONFIG_PATH, "w") as f:
        json.dump(user_config, f, indent=4)

    print(f"Removed serve_command from environment '{args.environment}'")


def run_rm_image_repository(args):
    """
    darp rm image_repository <environment>
    """
    user_config = get_config(CONFIG_PATH)
    environments = user_config.get("environments") or {}

    # Guard: only allowed if some env has image_repository (per spec)
    if not any(
        isinstance(env, dict) and "image_repository" in env for env in environments.values()
    ):
        print(
            "No environments with image_repository set. "
            "Use 'darp set image_repository' first."
        )
        sys.exit(1)

    env = environments.get(args.environment)
    if env is None:
        print(f"Environment '{args.environment}' does not exist.")
        sys.exit(1)

    if "image_repository" not in env:
        print(
            f"Environment '{args.environment}' has no custom image_repository. "
            "Use 'darp set image_repository' first."
        )
        sys.exit(1)

    del env["image_repository"]

    with open(CONFIG_PATH, "w") as f:
        json.dump(user_config, f, indent=4)

    print(f"Removed image_repository from environment '{args.environment}'")


def run_shell(args):
    """
    darp shell [-e ENV] <container_image>

    Starts nginx inside the container (if available) and drops you into a shell.
    """
    user_config = get_config(CONFIG_PATH)
    portmap_config = get_config(PORTMAP_PATH)

    # Optional environment
    environment = None
    if args.environment:
        environment = get_nested(user_config, ["environments", args.environment])
        if environment is None:
            print(f"Environment '{args.environment}' does not exist.")
            sys.exit(1)

    current_directory = os.getcwd()
    current_directory_name = os.path.basename(current_directory)

    parent_directory = os.path.dirname(current_directory)
    parent_directory_name = os.path.basename(parent_directory)

    domain = get_nested(user_config, ["domains", parent_directory_name])
    if domain is None:
        print(
            f"domain, {parent_directory_name}, does not exist in darp's "
            f"domain configuration."
        )
        sys.exit(1)

    container_name = f"darp_{parent_directory_name}_{current_directory_name}"

    podman_command = [
        "podman",
        "run",
        "--rm",
        "-it",
        "--name",
        container_name,
        "-v",
        f"{current_directory}:/app",
        "-v",
        f"{HOSTS_CONTAINER_PATH}:/etc/hosts",
        "-v",
        f"{NGINX_CONF_PATH}:/etc/nginx/nginx.conf",
        "-v",
        f"{VHOST_CONTAINER_CONF}:/etc/nginx/http.d/vhost_docker.conf",
    ]

    # Extra volumes from environment, if present
    if environment:
        for volume in environment.get("volumes", []):
            host_path = resolve_host_path(volume["host"], current_directory)
            if not os.path.exists(host_path):
                print(f"Volume, {volume['host']}, does not appear to exist.")
                sys.exit(1)
            podman_command.extend(["-v", f"{host_path}:{volume['container']}"])

    host_portmappings = get_nested(
        domain, ["services", current_directory_name, "host_portmappings"]
    )
    if host_portmappings:
        for host_port, container_port in host_portmappings.items():
            podman_command.extend(["-p", f"{host_port}:{container_port}"])

    # Reverse proxy port
    rev_proxy_port = get_nested(
        portmap_config, [parent_directory_name, current_directory_name]
    )
    if rev_proxy_port is None:
        print(
            f"port not yet assigned to {current_directory_name}, "
            f"run 'darp deploy'"
        )
        sys.exit(1)

    podman_command.extend(["-p", f"{rev_proxy_port}:8000"])

    image_name = resolve_image_name(environment, args.container_image)

    # Start nginx if present, then drop into an interactive shell.
    inner_cmd = (
        'if command -v nginx >/dev/null 2>&1; then '
        'echo "Starting nginx..."; nginx; '
        'else echo "nginx not found, skipping"; fi; '
        'echo ""; '
        'echo "To leave this shell and stop the container, type: \033[33mexit\033[0m"; '
        'echo ""; '
        'cd /app; exec sh'
    )



    podman_command.extend([image_name, "sh", "-c", inner_cmd])

    # Auto-restart on 137 (OOM or docker-style kill), but NOT on Ctrl+C.
    run_podman_interactive(podman_command, container_name=container_name, restart_on={137})


def run_serve(args):
    """
    darp serve -e ENV <container_image>

    Starts nginx inside the container (if available) and runs the environment's serve_command.
    """
    user_config = get_config(CONFIG_PATH)
    portmap_config = get_config(PORTMAP_PATH)

    if not args.environment:
        print("Environment is required for 'darp serve' (-e/--environment).")
        sys.exit(1)

    environment = get_nested(user_config, ["environments", args.environment])
    if environment is None:
        print(f"Environment '{args.environment}' does not exist.")
        sys.exit(1)

    serve_command = environment.get("serve_command")
    if not serve_command:
        print(
            f"Environment '{args.environment}' has no serve_command. "
            "Use 'darp set serve_command' first."
        )
        sys.exit(1)

    current_directory = os.getcwd()
    current_directory_name = os.path.basename(current_directory)

    parent_directory = os.path.dirname(current_directory)
    parent_directory_name = os.path.basename(parent_directory)

    domain = get_nested(user_config, ["domains", parent_directory_name])
    if domain is None:
        print(
            f"domain, {parent_directory_name}, does not exist in darp's "
            f"domain configuration."
        )
        sys.exit(1)

    container_name = f"darp_{parent_directory_name}_{current_directory_name}"

    while True:
        podman_command = [
            "podman",
            "run",
            "--rm",
            "--name",
            container_name,
            "-v",
            f"{current_directory}:/app",
            "-v",
            f"{HOSTS_CONTAINER_PATH}:/etc/hosts",
            "-v",
            f"{NGINX_CONF_PATH}:/etc/nginx/nginx.conf",
            "-v",
            f"{VHOST_CONTAINER_CONF}:/etc/nginx/http.d/vhost_docker.conf",
        ]

        # Extra volumes from environment, if present
        for volume in environment.get("volumes", []):
            host_path = resolve_host_path(volume["host"], current_directory)
            if not os.path.exists(host_path):
                print(f"Volume, {volume['host']}, does not appear to exist.")
                sys.exit(1)
            podman_command.extend(["-v", f"{host_path}:{volume['container']}"])

        host_portmappings = get_nested(
            domain, ["services", current_directory_name, "host_portmappings"]
        )
        if host_portmappings:
            for host_port, container_port in host_portmappings.items():
                podman_command.extend(["-p", f"{host_port}:{container_port}"])

        # Reverse proxy port
        rev_proxy_port = get_nested(
            portmap_config, [parent_directory_name, current_directory_name]
        )
        if rev_proxy_port is None:
            print(
                f"port not yet assigned to {current_directory_name}, "
                f"run 'darp deploy'"
            )
            sys.exit(1)

        podman_command.extend(["-p", f"{rev_proxy_port}:8000"])

        image_name = resolve_image_name(environment, args.container_image)

        inner_cmd = (
            'if command -v nginx >/dev/null 2>&1; then '
            'echo "Starting nginx..."; nginx; '
            'else echo "nginx not found, skipping"; fi; '
            f'cd /app; {serve_command}'
        )

        podman_command.extend([image_name, "sh", "-c", inner_cmd])

        # For serve, you wanted to auto-restart on rc == 2 (e.g. deploy raced).
        run_podman_interactive(podman_command, container_name=container_name, restart_on={2})
        break



def run_set_darp_root(args):
    zshrc_path = os.path.expanduser(args.zhrc or "~/.zshrc")

    if not os.path.exists(zshrc_path):
        print(f"{zshrc_path} does not exist; creating it.")
        open(zshrc_path, "a").close()

    with open(zshrc_path, "r") as file:
        lines = file.readlines()

    # Remove any existing DARP_ROOT export
    lines = [line for line in lines if not line.startswith("export DARP_ROOT=")]

    while lines and lines[-1].strip() == "":
        lines.pop()

    lines.append(f'\nexport DARP_ROOT="{args.NEW_DARP_ROOT}"\n')

    with open(zshrc_path, "w") as file:
        file.writelines(lines)

    print(
        f"DARP_ROOT set to '{args.NEW_DARP_ROOT}' in {zshrc_path}. "
        "Restart your shell or run 'source ~/.zshrc' to apply."
    )


def run_set_podman_machine(args):
    """
    darp set PODMAN_MACHINE <machine_name>

    Writes `export PODMAN_MACHINE="<machine_name>"` into the user's shell config.
    """
    zshrc_path = os.path.expanduser(args.zhrc or "~/.zshrc")

    if not os.path.exists(zshrc_path):
        print(f"{zshrc_path} does not exist; creating it.")
        open(zshrc_path, "a").close()

    with open(zshrc_path, "r") as file:
        lines = file.readlines()

    # Remove any existing PODMAN_MACHINE export
    lines = [line for line in lines if not line.startswith("export PODMAN_MACHINE=")]

    while lines and lines[-1].strip() == "":
        lines.pop()

    lines.append(f'\nexport PODMAN_MACHINE="{args.NEW_PODMAN_MACHINE}"\n')

    with open(zshrc_path, "w") as file:
        file.writelines(lines)

    print(
        f"PODMAN_MACHINE set to '{args.NEW_PODMAN_MACHINE}' in {zshrc_path}. "
        f"Restart your shell or run 'source {zshrc_path}' to apply."
    )


def run_rm_darp_root(args):
    """
    darp rm DARP_ROOT [-z PATH]

    Remove the DARP_ROOT export from the shell config.
    """
    zshrc_path = os.path.expanduser(args.zhrc or "~/.zshrc")

    if not os.path.exists(zshrc_path):
        print(f"{zshrc_path} does not exist.")
        sys.exit(1)

    with open(zshrc_path, "r") as file:
        lines = file.readlines()

    new_lines = [line for line in lines if not line.startswith("export DARP_ROOT=")]

    if len(new_lines) == len(lines):
        print(f"No DARP_ROOT entry found in {zshrc_path}.")
        return

    with open(zshrc_path, "w") as file:
        file.writelines(new_lines)

    print(
        f"Removed DARP_ROOT from {zshrc_path}. "
        f"Restart your shell or run 'source {zshrc_path}' to apply."
    )


def run_rm_podman_machine(args):
    """
    darp rm PODMAN_MACHINE [-z PATH]

    Remove the PODMAN_MACHINE export from the shell config.
    """
    zshrc_path = os.path.expanduser(args.zhrc or "~/.zshrc")

    if not os.path.exists(zshrc_path):
        print(f"{zshrc_path} does not exist.")
        sys.exit(1)

    with open(zshrc_path, "r") as file:
        lines = file.readlines()

    new_lines = [line for line in lines if not line.startswith("export PODMAN_MACHINE=")]

    if len(new_lines) == len(lines):
        print(f"No PODMAN_MACHINE entry found in {zshrc_path}.")
        return

    with open(zshrc_path, "w") as file:
        file.writelines(new_lines)

    print(
        f"Removed PODMAN_MACHINE from {zshrc_path}. "
        f"Restart your shell or run 'source {zshrc_path}' to apply."
    )


def run_urls(_args):
    portmap_config = get_config(PORTMAP_PATH)

    print()
    for domain_name, domain in sorted(portmap_config.items()):
        print(f"{Fore.GREEN}{domain_name}{Style.RESET_ALL}")
        for folder_name, port in sorted(domain.items()):
            print(
                f"  http://{Fore.BLUE}{folder_name}{Style.RESET_ALL}"
                f".{domain_name}.test ({port})"
            )
        print()


# ---------------------------------------------------------------------------
# Startup Checks
# ---------------------------------------------------------------------------

if not is_podman_running():
    if PODMAN_MACHINE_ENV:
        machine_msg = f"Podman machine '{PODMAN_MACHINE_ENV}' appears to be down"
        hint = f"podman machine start {PODMAN_MACHINE_ENV}"
    else:
        machine_msg = "No podman machine appears to be running"
        hint = "podman machine start"

    print(f"{machine_msg} {Fore.RED}({hint}){Style.RESET_ALL}")
    sys.exit(1)

if not is_unprivileged_port_start(53):
    print(
        f"Podman machine '{PODMAN_MACHINE_ENV}' has port 53 privileged "
        f"{Fore.RED}(run 'darp init' or see readme.md){Style.RESET_ALL}"
    )

start_reverse_proxy()
start_darp_masq()

user_config = get_config(CONFIG_PATH)

# ---------------------------------------------------------------------------
# Help Text Setup
# ---------------------------------------------------------------------------

init_help_text = "sudo (one time) initialization"
init_help_reqs = []

deploy_help_text = "deploys the environment"
deploy_help_reqs = []

shell_help_text = "starts a shell instance"
shell_help_reqs = []

urls_help_text = "list out your darps"
urls_help_reqs = []

serve_help_text = "runs the environment serve_command"
serve_help_reqs = []

add_volume_help_text = "add volume to an environment"
add_volume_help_reqs = []

set_serve_help_text = "set serve_command on an environment"
set_serve_help_reqs = []

set_image_repo_help_text = "set image_repository on an environment"
set_image_repo_help_reqs = []

rm_serve_help_text = "remove serve_command from an environment"
rm_serve_help_reqs = []

rm_image_repo_help_text = "remove image_repository from an environment"
rm_image_repo_help_reqs = []

domains = user_config.get("domains")
shell_needs_deploy = True

if domains:
    portmap_config_for_shell = get_config(PORTMAP_PATH)
    for _, d in portmap_config_for_shell.items():
        if d:
            shell_needs_deploy = False
            break
else:
    deploy_help_reqs.append("add domain")

if shell_needs_deploy:
    shell_help_reqs.append("deploy")
    urls_help_reqs.append("deploy")

dnsmasq_test_exists = is_init_initialized()

if dnsmasq_test_exists:
    init_help_reqs.append("initialized")
else:
    deploy_help_reqs.append("init")
    shell_help_reqs.append("init")
    urls_help_reqs.append("init")
    serve_help_reqs.append("init")

# Also require init if the podman machine hasn't lowered the unprivileged port yet
if not is_unprivileged_port_start(53):
    # Don't duplicate "init" if it's already there, but ensure it's a requirement
    if "init" not in deploy_help_reqs:
        deploy_help_reqs.append("init")
    if "init" not in shell_help_reqs:
        shell_help_reqs.append("init")
    if "init" not in urls_help_reqs:
        urls_help_reqs.append("init")
    if "init" not in serve_help_reqs:
        serve_help_reqs.append("init")

# Environment-related help dependencies
environments = user_config.get("environments") or {}
has_environments = bool(environments)

any_env_has_serve_command = any(
    isinstance(env, dict) and "serve_command" in env for env in environments.values()
)
any_env_has_image_repo = any(
    isinstance(env, dict) and "image_repository" in env for env in environments.values()
)

# For commands that require environments at all
if not has_environments:
    # Per your spec: show ( 'add domain' ) when there are no environments
    add_volume_help_reqs.append("add domain")
    set_serve_help_reqs.append("add domain")
    set_image_repo_help_reqs.append("add domain")
    serve_help_reqs.append("add domain")

# serve requires serve_command to be set on at least one env
if not any_env_has_serve_command:
    serve_help_reqs.append("set serve_command")

# rm serve_command requires someone to have it
if not any_env_has_serve_command:
    rm_serve_help_reqs.append("set serve_command")

# rm image_repository requires someone to have it
if not any_env_has_image_repo:
    rm_image_repo_help_reqs.append("set image_repository")


def decorate_help(text, requirements):
    if not requirements:
        return text
    text = Style.DIM + text + Style.RESET_ALL
    text += " ( "
    text += " ".join(
        f"'{Fore.BLUE}{action}{Style.RESET_ALL}'" for action in requirements
    )
    text += " )"
    return text


init_help_text = decorate_help(init_help_text, init_help_reqs)
deploy_help_text = decorate_help(deploy_help_text, deploy_help_reqs)
shell_help_text = decorate_help(shell_help_text, shell_help_reqs)
urls_help_text = decorate_help(urls_help_text, urls_help_reqs)
serve_help_text = decorate_help(serve_help_text, serve_help_reqs)
add_volume_help_text = decorate_help(add_volume_help_text, add_volume_help_reqs)
set_serve_help_text = decorate_help(set_serve_help_text, set_serve_help_reqs)
set_image_repo_help_text = decorate_help(set_image_repo_help_text, set_image_repo_help_reqs)
rm_serve_help_text = decorate_help(rm_serve_help_text, rm_serve_help_reqs)
rm_image_repo_help_text = decorate_help(rm_image_repo_help_text, rm_image_repo_help_reqs)

# ---------------------------------------------------------------------------
# Argument Parsing
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(
    prog=f"{Fore.GREEN}darp{Style.RESET_ALL}",
    description=(
        f"Your {Fore.LIGHTMAGENTA_EX}d{Style.RESET_ALL}irectories "
        f"{Fore.LIGHTMAGENTA_EX}a{Style.RESET_ALL}uto-"
        f"{Fore.LIGHTMAGENTA_EX}r{Style.RESET_ALL}everse "
        f"{Fore.LIGHTMAGENTA_EX}p{Style.RESET_ALL}roxied."
    ),
    epilog="Please enjoy.",
    usage=argparse.SUPPRESS,
)

subparsers = parser.add_subparsers(dest="command")

# darp init
parser_init = subparsers.add_parser("init", help=init_help_text, usage=argparse.SUPPRESS)
parser_init.set_defaults(func=run_init)

# darp deploy
parser_deploy = subparsers.add_parser(
    "deploy", help=deploy_help_text, usage=argparse.SUPPRESS
)
parser_deploy.set_defaults(func=run_deploy)

# darp shell
parser_shell = subparsers.add_parser(
    "shell", help=shell_help_text, usage=argparse.SUPPRESS
)
parser_shell.add_argument(
    "-e",
    "--environment",
    help="The name of the environment to start the shell in",
    required=False,
)
parser_shell.add_argument(
    "container_image", help="The container image from which to create the shell instance"
)
parser_shell.set_defaults(func=run_shell)

# darp serve
parser_serve = subparsers.add_parser(
    "serve", help=serve_help_text, usage=argparse.SUPPRESS
)
parser_serve.add_argument(
    "-e",
    "--environment",
    help="The name of the environment whose serve_command to run",
    required=True,
)
parser_serve.add_argument(
    "container_image", help="The container image from which to run the serve_command"
)
parser_serve.set_defaults(func=run_serve)

# darp set
parser_set = subparsers.add_parser("set", help="set config value", usage=argparse.SUPPRESS)
subparser_set = parser_set.add_subparsers(
    dest="set_command", help="set any of the following in the config"
)

parser_set_darp_root = subparser_set.add_parser(
    "DARP_ROOT",
    help=f"set DARP_ROOT (current: {Fore.GREEN}{DARP_ROOT}{Style.RESET_ALL})",
    usage=argparse.SUPPRESS,
)
parser_set_darp_root.add_argument(
    "NEW_DARP_ROOT",
    help=(
        "the new directory for contents of .darp "
        f"(current: {Fore.GREEN}{DARP_ROOT}{Style.RESET_ALL})"
    ),
)
parser_set_darp_root.add_argument(
    "-z", "--zhrc", help="the location of the .zshrc file", required=False
)
parser_set_darp_root.set_defaults(func=run_set_darp_root)

# darp set podman_machine
parser_set_podman_machine = subparser_set.add_parser(
    "PODMAN_MACHINE",
    help=(
        "set PODMAN_MACHINE "
        f"(current: {Fore.GREEN}{PODMAN_MACHINE_ENV or 'not set'}{Style.RESET_ALL})"
    ),
    usage=argparse.SUPPRESS,
)
parser_set_podman_machine.add_argument(
    "NEW_PODMAN_MACHINE",
    help="the podman machine name to target (e.g. podman-machine-default, darp)",
)
parser_set_podman_machine.add_argument(
    "-z", "--zhrc", help="the location of the .zshrc file", required=False
)
parser_set_podman_machine.set_defaults(func=run_set_podman_machine)

# darp set serve_command
parser_set_serve = subparser_set.add_parser(
    "serve_command", help=set_serve_help_text, usage=argparse.SUPPRESS
)
parser_set_serve.add_argument("environment", help="the name of the environment")
parser_set_serve.add_argument(
    "serve_command", help="the command to run inside the container for this environment"
)
parser_set_serve.set_defaults(func=run_set_serve_command)

# darp set image_repository
parser_set_image_repo = subparser_set.add_parser(
    "image_repository", help=set_image_repo_help_text, usage=argparse.SUPPRESS
)
parser_set_image_repo.add_argument("environment", help="the name of the environment")
parser_set_image_repo.add_argument(
    "image_repository",
    help="base image repository (e.g. git.company.org:4567/path/to/image)",
)
parser_set_image_repo.set_defaults(func=run_set_image_repository)

# darp add
parser_add = subparsers.add_parser("add", help="add to config", usage=argparse.SUPPRESS)
subparser_add = parser_add.add_subparsers(
    dest="add_command", help="add any of the following to the config"
)

parser_add_portmap = subparser_add.add_parser(
    "portmap", help="add port mapping to a service", usage=argparse.SUPPRESS
)
parser_add_portmap.add_argument("domain_name", help="the name of the domain")
parser_add_portmap.add_argument("service_name", help="the name of the service")
parser_add_portmap.add_argument("host_port", type=str, help="the host port")
parser_add_portmap.add_argument("container_port", type=str, help="the container port")
parser_add_portmap.set_defaults(func=run_add_portmap)

parser_add_domain = subparser_add.add_parser(
    "domain", help="add domain", usage=argparse.SUPPRESS
)
parser_add_domain.add_argument("name", help="the name of the domain")
parser_add_domain.add_argument("location", help="the location of the domain")
parser_add_domain.set_defaults(func=run_add_domain)

# darp add volume
parser_add_volume = subparser_add.add_parser(
    "volume", help=add_volume_help_text, usage=argparse.SUPPRESS
)
parser_add_volume.add_argument("environment", help="the name of the environment")
parser_add_volume.add_argument(
    "container_dir", help="the container directory mount path"
)
parser_add_volume.add_argument(
    "host_dir",
    help=(
        f"the host directory (may include {PSEUDO_PWD_TOKEN} as the current-directory "
        "placeholder)"
    ),
)
parser_add_volume.set_defaults(func=run_add_volume)

# darp remove
parser_remove = subparsers.add_parser(
    "rm", help="remove from config", usage=argparse.SUPPRESS
)
subparser_remove = parser_remove.add_subparsers(
    dest="remove_command", help="remove any of the following from the config"
)

parser_remove_portmap = subparser_remove.add_parser(
    "portmap", help="remove port mapping from a service", usage=argparse.SUPPRESS
)
parser_remove_portmap.add_argument("domain_name", help="the name of the domain")
parser_remove_portmap.add_argument("service_name", help="the name of the service")
parser_remove_portmap.add_argument("host_port", type=str, help="the host port")
parser_remove_portmap.add_argument(
    "container_port",
    nargs="?",
    type=str,
    help="(optional) container port (ignored)",
)
parser_remove_portmap.set_defaults(func=run_remove_portmap)

parser_remove_domain = subparser_remove.add_parser(
    "domain", help="remove domain", usage=argparse.SUPPRESS
)
parser_remove_domain.add_argument("name", help="the name of the domain")
parser_remove_domain.add_argument(
    "location",
    nargs="?",
    help="(optional) the location of the domain (ignored)",
)
parser_remove_domain.set_defaults(func=run_remove_domain)

# darp rm serve_command
parser_rm_serve = subparser_remove.add_parser(
    "serve_command", help=rm_serve_help_text, usage=argparse.SUPPRESS
)
parser_rm_serve.add_argument("environment", help="the name of the environment")
parser_rm_serve.set_defaults(func=run_rm_serve_command)

# darp rm image_repository
parser_rm_image_repo = subparser_remove.add_parser(
    "image_repository", help=rm_image_repo_help_text, usage=argparse.SUPPRESS
)
parser_rm_image_repo.add_argument("environment", help="the name of the environment")
parser_rm_image_repo.set_defaults(func=run_rm_image_repository)

# darp rm DARP_ROOT
parser_rm_darp_root = subparser_remove.add_parser(
    "DARP_ROOT", help="remove DARP_ROOT from shell config", usage=argparse.SUPPRESS
)
parser_rm_darp_root.add_argument(
    "-z", "--zhrc", help="the location of the .zshrc file", required=False
)
parser_rm_darp_root.set_defaults(func=run_rm_darp_root)

# darp rm PODMAN_MACHINE
parser_rm_podman_machine = subparser_remove.add_parser(
    "PODMAN_MACHINE", help="remove PODMAN_MACHINE from shell config", usage=argparse.SUPPRESS
)
parser_rm_podman_machine.add_argument(
    "-z", "--zhrc", help="the location of the .zshrc file", required=False
)
parser_rm_podman_machine.set_defaults(func=run_rm_podman_machine)

# darp urls
parser_urls = subparsers.add_parser(
    "urls", help=urls_help_text, usage=argparse.SUPPRESS
)
parser_urls.set_defaults(func=run_urls)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if len(sys.argv) == 1:
    parser.print_help()
    sys.exit(0)

args = parser.parse_args()

if args.command == "add" and args.add_command is None:
    parser_add.print_help()
    sys.exit(1)
elif args.command == "rm" and args.remove_command is None:
    parser_remove.print_help()
    sys.exit(1)
elif args.command == "set" and args.set_command is None:
    parser_set.print_help()
    sys.exit(1)

if hasattr(args, "func"):
    args.func(args)
else:
    parser.print_help()
