import argparse
import json
import os
import subprocess
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

CONFIG_PATH = os.path.join(DARP_ROOT, "config.json")
PORTMAP_PATH = os.path.join(DARP_ROOT, "portmap.json")
DNSMASQ_DIR = os.path.join(DARP_ROOT, "dnsmasq.d")
VHOST_CONTAINER_CONF = os.path.join(DARP_ROOT, "vhost_container.conf")
HOSTS_CONTAINER_PATH = os.path.join(DARP_ROOT, "hosts_container")
NGINX_CONF_PATH = os.path.join(DARP_ROOT, "nginx.conf")
RESOLVER_FILE = "/etc/resolver/test"

REVERSE_PROXY_CONTAINER = "darp-reverse-proxy"
DNSMASQ_CONTAINER = "darp-masq"

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


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


def is_podman_running():
    """Check if podman machine is running."""
    try:
        output = run_command_capture(
            ["podman", "machine", "list", "--format", "{{.LastUp}}"]
        )
        running_machines = output.strip().splitlines()
        return "Currently running" in running_machines
    except Exception:
        return False


def is_unprivileged_port_start(expected_port):
    """Check that net.ipv4.ip_unprivileged_port_start <= expected_port."""
    try:
        result = run_command_capture(
            [
                "podman",
                "machine",
                "ssh",
                "sysctl",
                "net.ipv4.ip_unprivileged_port_start",
            ]
        )
        _, value = result.strip().split("=")
        actual_port = int(value.strip())
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


# ---------------------------------------------------------------------------
# Command Line Functions
# ---------------------------------------------------------------------------


def run_init(_args):
    print("Running initialization")

    # Create the resolver directory
    run_command(["sudo", "mkdir", "-p", "/etc/resolver"])

    # Write the resolver file
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


def run_deploy(_args):
    print("Deploying Container Development\n")

    user_config = get_config(CONFIG_PATH)

    domains = user_config.get("domains")
    if not domains:
        print("Please configure a domain.")
        sys.exit(1)

    # backup old files if they exist
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_dir = os.path.join(DARP_ROOT, f"backup_{timestamp}")
    os.makedirs(backup_dir, exist_ok=True)

    files_to_move = [
        HOSTS_CONTAINER_PATH,
        PORTMAP_PATH,
        VHOST_CONTAINER_CONF,
    ]
    existing_files = [f for f in files_to_move if os.path.exists(f)]
    if existing_files:
        run_command(["mv", *existing_files, backup_dir])

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

    # delete backup location
    run_command(["rm", "-rf", backup_dir])

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


def run_shell(args):
    user_config = get_config(CONFIG_PATH)
    portmap_config = get_config(PORTMAP_PATH)

    environment = get_nested(user_config, ["environments", args.environment])
    if environment is None:
        print("Environment does not exist.")
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

    # Extra volumes
    for volume in environment.get("volumes", []):
        host_path = volume["host"].replace("$(pwd)", current_directory)
        if not os.path.exists(host_path):
            print(f"Volume, {volume['host']}, does not appear to exist.")
            sys.exit(1)
        podman_command.extend(
            ["-v", f"{host_path}:{volume['container']}"]
        )

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
    podman_command.extend([args.container_image, "sh"])

    try:
        run_command(podman_command)
    except subprocess.CalledProcessError as e:
        if e.returncode == 137:
            print(f"restarting {Fore.CYAN}{container_name}{Style.RESET_ALL}")
            run_shell(args)


def run_set_darp_root(args):
    zshrc_path = os.path.expanduser(args.zhrc or "~/.zshrc")

    if not os.path.exists(zshrc_path):
        print(f"{zshrc_path} does not exist; creating it.")
        open(zshrc_path, "a").close()

    with open(zshrc_path, "r") as file:
        lines = file.readlines()

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
    print(
        "podman-machine-default is currently down "
        f"{Fore.RED}(podman machine start){Style.RESET_ALL}"
    )
    sys.exit(1)

if not is_unprivileged_port_start(53):
    print(
        "podman-machine-default is set with port 53 privileged "
        f"{Fore.RED}(see readme.md){Style.RESET_ALL}"
    )
    sys.exit(1)

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

domains = user_config.get("domains")
shell_needs_deploy = True

if domains:
    portmap_config_for_shell = get_config(PORTMAP_PATH)
    for _, domain in portmap_config_for_shell.items():
        if domain:
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
    "environment", help="The name of the environment to start the shell in"
)
parser_shell.add_argument(
    "container_image", help="The container image from which to create the shell instance"
)
parser_shell.set_defaults(func=run_shell)

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
