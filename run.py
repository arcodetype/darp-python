import argparse
import json
import os
import subprocess
import sys

from colorama import Fore, Style, init

init()

home_directory = os.path.expanduser("~")
darp_root_env = os.environ.get('DARP_ROOT', f"{home_directory}/.container_development")
DARP_ROOT = os.path.join(darp_root_env, '')

# Helper Functions

def get_nested(d, keys):
    for key in keys:
        d = d.get(key)
        if d is None:
            return d
    return d

def get_user_config(filename):
    if not os.path.exists(filename):
        with open(filename, 'w') as f:
            json.dump({}, f, indent=4)
            print(f"Created {filename}")

    with open(filename) as f:
        try:
            return json.load(f)
        except json.decoder.JSONDecodeError:
            print(f"Not Json {filename}")
            sys.exit()
    sys.exit()

def get_running_darps():
    try:
        output = subprocess.check_output(
            ['podman', 'ps', '--format', '{{.Names}}']
        )
        running_containers = output.decode().strip().splitlines()
        return [x for x in running_containers if x.startswith('darp_')]
    except:
        return []

def is_init_initalized():
    try:
        output = subprocess.check_output(
            ['cat', '/etc/resolver/test']
        )
        result = output.decode().strip()
        return 'nameserver 127.0.0.1' == result
    except:
        return False

def is_podman_running():
    try:
        output = subprocess.check_output(
            ['podman', 'machine', 'list', '--format', '{{.LastUp}}']
        )
        running_machines = output.decode().strip().splitlines()
        return 'Currently running' in running_machines
    except:
        return False

def is_unprivileged_port_start(expected_port):
    try:
        # Run `sysctl` inside the Podman VM
        result = subprocess.check_output(
            ['podman', 'machine', 'ssh', 'sysctl', 'net.ipv4.ip_unprivileged_port_start'],
            text=True
        )
        # Extract the port value from the output
        _, value = result.strip().split('=')
        actual_port = int(value.strip())
        return actual_port <= expected_port
    except subprocess.CalledProcessError as e:
        return False

def is_container_running(container_name):
    try:
        output = subprocess.check_output(
            ['podman', 'container', 'ls', '--format', '{{.Names}}']
        )
        running_containers = output.decode().strip().splitlines()
        return container_name in running_containers
    except subprocess.CalledProcessError as e:
        print(f"Error checking containers: {e}")
        return False

def restart_reverse_proxy():
    if not is_container_running('darp-reverse-proxy'):
        return start_reverse_proxy()
    
    restart_command = []
    restart_command.extend(['podman', 'restart', 'darp-reverse-proxy'])

    print(f'restarting {Fore.GREEN}darp-reverse-proxy{Style.RESET_ALL}')

    subprocess.Popen(
        restart_command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def start_reverse_proxy():
    if is_container_running('darp-reverse-proxy'):
        return True
    
    start_command = []
    start_command.extend(['podman', 'run', '-d'])
    start_command.extend(['--rm'])
    start_command.extend(['--name', 'darp-reverse-proxy'])
    start_command.extend(['-p', '80:80'])
    start_command.extend(['-v', f'{DARP_ROOT}/vhost_local.conf:/etc/nginx/conf.d/vhost_local.conf' ])
    start_command.extend(['nginx'])

    print(f'starting {Fore.GREEN}darp-reverse-proxy{Style.RESET_ALL}\n')

    subprocess.Popen(
        start_command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def stop_running_darp(name):
    print(f'stopping {Fore.CYAN + name + Style.RESET_ALL}')
    stop_command = []
    stop_command.extend(['podman', 'stop', name])

    subprocess.Popen(
        stop_command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def stop_running_darps():
    darps = get_running_darps()

    for darp in darps:
        stop_running_darp(darp)

# Command Line Functions

def run_init(args):
    print(f'Running initialization')
    # Create the directory
    subprocess.run(["sudo", "mkdir", "-p", "/etc/resolver"], check=True)

    # Write the resolver file
    subprocess.run(
        ["sudo", "tee", "/etc/resolver/test"],
        input="nameserver 127.0.0.1\n",
        text=True,
        check=True
    )
    print(f'\n{Fore.GREEN}/etc/resolver/test{Style.RESET_ALL} created')

    with open(f'{DARP_ROOT}dnsmasq.conf', 'w') as file:
        lines = [
            'listen-address=0.0.0.0\n',
            'no-hosts\n',
            'no-resolv\n',
            'address=/.test/127.0.0.1\n',
            '\n',
            '# Optional logging\n',
            'log-queries\n',
            'log-facility=/var/log/dnsmasq.log\n',
        ]
        file.writelines(lines)

    print(f'{Fore.GREEN}{DARP_ROOT}dnsmasq.conf{Style.RESET_ALL} created')


def run_deploy(args):
    print('Deploying Container Development\n')
    restart_reverse_proxy()
    stop_running_darps()

def run_add_portmap(args):
    filename = f"{DARP_ROOT}config.json"

    user_config = get_user_config(filename)

    existing_host_portmapping = get_nested(user_config, [
        'domains',
        args.domain_name,
        'services',
        args.service_name,
        'host_portmappings',
        args.host_port
    ])

    if existing_host_portmapping is not None:
        print(f"Portmapping on host side '{args.domain_name}.{args.service_name}' ({args.host_port}:____) already exists")
        sys.exit()

    domains = user_config.get('domains')
    if domains is None:
        print(f"domain, {args.domain_name}, does not exist")
        sys.exit()

    domain = domains.get(args.domain_name)
    if domain is None:
        print(f"domain, {args.domain_name}, does not exist")
        sys.exit()

    services = domain.get('services')
    if services is None:
        services = {}

    service = services.get(args.service_name)
    if service is None:
        service = {}

    host_portmappings = service.get('host_portmappings')
    if host_portmappings is None:
        host_portmappings = {}

    services[args.service_name] = service
    service = host_portmappings
    host_portmappings[args.host_port] = args.container_port

    user_config['domains'][args.domain_name]['services'] = services

    with open(filename, 'w') as f:
        json.dump(user_config, f, indent=4)

    print(f"Created portmapping for '{args.domain_name}.{args.service_name}' ({args.host_port}:{args.container_port})")

def run_remove_portmap(args):
    filename = f"{DARP_ROOT}config.json"

    user_config = get_user_config(filename)

    existing_host_portmapping = get_nested(user_config, [
        'domains',
        args.domain_name,
        'services',
        args.service_name,
        'host_portmappings',
        args.host_port
    ])
    if existing_host_portmapping is None:
        print(f"Portmapping on host side '{args.domain_name}.{args.service_name}' ({args.host_port}:____) does not exist")
        sys.exit()

    del user_config['domains'][args.domain_name]['services'][args.service_name]['host_portmappings'][args.host_port]

    with open(filename, 'w') as f:
        json.dump(user_config, f, indent=4)

    print(f"Created portmapping for '{args.domain_name}.{args.service_name}' ({args.host_port}:____)")

def run_add_domain(args):
    filename = f"{DARP_ROOT}config.json"

    user_config = get_user_config(filename)

    existing_domain = get_nested(user_config, ['domains', args.name])
    if existing_domain is not None:
        print(f"domain {args.name} already exists at {existing_domain['location']}")
        sys.exit()

    domains = user_config.get('domains')
    if domains is None:
        user_config['domains'] = {}

    domain = {
        'location': args.location,
    }
    user_config['domains'][args.name] = domain

    with open(filename, 'w') as f:
        json.dump(user_config, f, indent=4)

    print(f"created '{args.name}' at {args.location}")

def run_remove_domain(args):
    filename = f"{DARP_ROOT}config.json"

    user_config = get_user_config(filename)

    existing_domain = get_nested(user_config, ['domains', args.name])
    if existing_domain is None:
        print(f"domains, {args.name}, does not exist")
        sys.exit()

    del user_config['domains'][args.name]

    with open(filename, 'w') as f:
        json.dump(user_config, f, indent=4)

    print(f"removed '{args.name}'")
        
def run_shell(args):
    filename = f"{DARP_ROOT}config.json"

    user_config = get_user_config(filename)
    environment = get_nested(user_config, ['environments', args.environment])

    if environment is None:
        print("Environment does not exist.")
        sys.exit()


    current_directory = os.getcwd()
    current_directory_name = os.path.basename(current_directory)
    
    parent_directory = os.path.dirname(current_directory)
    parent_directory_name = os.path.basename(parent_directory)

    domain = get_nested(user_config, ['domains', parent_directory_name])

    if domain is None:
        print(f"domain, {parent_directory_name}, does not exist in darp's domain configuration.")
        sys.exit()

    container_name = 'darp_' + parent_directory_name + '_' + current_directory_name

    podman_command = []
    podman_command.extend(["podman", "run"])
    podman_command.extend(["--rm", "-it"])
    podman_command.extend(['--name', container_name])
    podman_command.extend(['-v', f"{current_directory}:/app"])

    for volume in environment.get('volumes', []):
        if not os.path.exists(volume['host'].replace("$(pwd)", current_directory)):
            print(f"Volume, {volume['host']}, does not appear to exist.")
            sys.exit()
        podman_command.extend(['-v', f"{volume['host']}:{volume['container']}".replace("$(pwd)", current_directory)])

    host_portmappings = get_nested(domain, ['services', current_directory_name, 'host_portmappings'])
    if host_portmappings is not None:
        for host_port, container_port in host_portmappings.items():
            podman_command.extend(['-p', f"{host_port}:{container_port}"])

    podman_command.extend([args.container_image, 'sh'])

    # Inherits your terminal's stdin/stdout/stderr and TTY.
    try:
        subprocess.run(podman_command, check=True)
    except subprocess.CalledProcessError as e:
        if e.returncode == 137:
            print(f'restarting {Fore.CYAN + container_name + Style.RESET_ALL}')
            run_shell(args)

def run_set_darp_root(args):
    zshrc_path = os.path.expanduser('~/.zshrc')
    
    with open(zshrc_path, 'r') as file:
        lines = file.readlines()
    
    lines = [line for line in lines if not line.startswith('export DARP_ROOT=')]

    while lines and lines[-1].strip() == '':
        lines.pop()

    lines.append(f'\nexport DARP_ROOT="{args.NEW_DARP_ROOT}"')
    
    with open(zshrc_path, 'w') as file:
        file.writelines(lines)

    print(f"DARP_ROOT set to '{args.NEW_DARP_ROOT}' and loaded into darp. Note that other terminals may still have the old DARP_ROOT loaded. Restart or run 'source ~/.zshrc' in those terminals to update.")

    # Reload the .zshrc file
    subprocess.run(['zsh'], check=True)

# Start Up
machine_running = is_podman_running()

if not machine_running:
    print(f'podman-machine-default is currently down {Fore.RED}(podman machine start){Style.RESET_ALL}')
    sys.exit()

if not is_unprivileged_port_start(80):
    print(f'podman-machine-default is set with port 80 privilged {Fore.RED}(see readme.md){Style.RESET_ALL}')
    sys.exit()

start_reverse_proxy()

# Configuration Checks

filename = f"{DARP_ROOT}config.json"
user_config = get_user_config(filename)

domain_is_set = False
domains = user_config.get('domains')
if domains is not None and len(domains) > 0:
    domain_is_set = True


# Command Line Interactions
parser = argparse.ArgumentParser(
    prog=f'{Fore.GREEN}darp{Style.RESET_ALL}',
    description=f'Your {Fore.LIGHTMAGENTA_EX}d{Style.RESET_ALL}irectories {Fore.LIGHTMAGENTA_EX}a{Style.RESET_ALL}uto-{Fore.LIGHTMAGENTA_EX}r{Style.RESET_ALL}everse {Fore.LIGHTMAGENTA_EX}p{Style.RESET_ALL}roxied.',
    epilog='For any questions, please attend the Arcodetype livestream (when it\'s on!)',
    usage=argparse.SUPPRESS
)

subparsers = parser.add_subparsers(dest='command')

dns_mask_test_exists = is_init_initalized()

# darp init
init_help_text = 'sudo (one time) initialization'
init_help_reqs = []
deploy_help_text = 'deploys the environment'
deploy_help_reqs = []
shell_help_text = 'starts a shell instance'
shell_help_reqs = []

if (dns_mask_test_exists):
    init_help_reqs.append('initialized')
else:
    deploy_help_reqs.append('init')
    shell_help_reqs.append('init')

if len(init_help_reqs) > 0:
    init_help_text = Style.DIM + init_help_text + Style.RESET_ALL
    init_help_text += ' ('
    for action in init_help_reqs:
        init_help_text += f" '{Fore.BLUE + action + Style.RESET_ALL}'"
    init_help_text += ' )'

if not domain_is_set:
    deploy_help_reqs.append('add domain')

if len(deploy_help_reqs) > 0:
    shell_help_reqs.append('deploy')
    deploy_help_text = Style.DIM + deploy_help_text + Style.RESET_ALL
    deploy_help_text += ' ('
    for action in deploy_help_reqs:
        deploy_help_text += f" '{Fore.BLUE + action + Style.RESET_ALL}'"
    deploy_help_text += ' )'

if len(shell_help_reqs) > 0:
    shell_help_text = Style.DIM + shell_help_text + Style.RESET_ALL
    shell_help_text += ' ('
    for action in shell_help_reqs:
        shell_help_text += f" '{Fore.BLUE + action + Style.RESET_ALL}'"
    shell_help_text += ' )'

# darp init
parser_init = subparsers.add_parser(f'init', help=init_help_text, usage=argparse.SUPPRESS)
parser_init.set_defaults(func=run_init)

# darp deploy
parser_deploy = subparsers.add_parser(f'deploy', help=deploy_help_text, usage=argparse.SUPPRESS)
parser_deploy.set_defaults(func=run_deploy)

# darp shell
parser_shell = subparsers.add_parser('shell', help=shell_help_text, usage=argparse.SUPPRESS)
parser_shell.add_argument('environment', help='The name of the environment to start the shell in')
parser_shell.add_argument('container_image', help='The container image from which to create the shell instance')
parser_shell.set_defaults(func=run_shell)

# darp set
parser_set = subparsers.add_parser('set', help='set config value', usage=argparse.SUPPRESS)
subparser_set = parser_set.add_subparsers(dest='set_command', help='set any of the following in the config')

# darp set DARP_ROOT
parser_set_darp_root = subparser_set.add_parser('DARP_ROOT', help=f"set DARP_ROOT (current: {DARP_ROOT})", usage=argparse.SUPPRESS)
parser_set_darp_root.add_argument('NEW_DARP_ROOT', help=f"the new directory for contents of .container_development (current: {DARP_ROOT})")
parser_set_darp_root.add_argument('-z', '--zhrc', help='the location of the .zshrc file', required=False)
parser_set_darp_root.set_defaults(func=run_set_darp_root)

# darp add
parser_add = subparsers.add_parser('add', help='add to config', usage=argparse.SUPPRESS)
subparser_add = parser_add.add_subparsers(dest='add_command', help='add any of the following to the config')

# darp add port_override
parser_add_portmap = subparser_add.add_parser('portmap', help='add port mapping to a service', usage=argparse.SUPPRESS)
parser_add_portmap.add_argument('domain_name', help='the name of the domain')
parser_add_portmap.add_argument('service_name', help='the name of the service')
parser_add_portmap.add_argument('host_port', type=str, help='the host port')
parser_add_portmap.add_argument('container_port', type=str, help='the container port')
parser_add_portmap.set_defaults(func=run_add_portmap)

# darp add domain
parser_add_domain = subparser_add.add_parser('domain', help='add domain', usage=argparse.SUPPRESS)
parser_add_domain.add_argument('name', help='the name of the domain')
parser_add_domain.add_argument('location', help='the location of the domain')
parser_add_domain.set_defaults(func=run_add_domain)

# darp remove
parser_remove = subparsers.add_parser('rm', help='remove from config', usage=argparse.SUPPRESS)
subparser_remove = parser_remove.add_subparsers(dest='remove_command', help='remove any of the following from the config')

# darp add port_override
parser_remove_portmap = subparser_remove.add_parser('portmap', help='remove port mapping to a service', usage=argparse.SUPPRESS)
parser_remove_portmap.add_argument('domain_name', help='the name of the domain')
parser_remove_portmap.add_argument('service_name', help='the name of the service')
parser_remove_portmap.add_argument('host_port', type=str, help='the host port')
parser_remove_portmap.add_argument('container_port (optional)', nargs='?', type=str, help='the container port')
parser_remove_portmap.set_defaults(func=run_remove_portmap)

# darp remove domain
parser_remove_domain = subparser_remove.add_parser('domain', help='remove domain', usage=argparse.SUPPRESS)
parser_remove_domain.add_argument('name', help='the name of the domain')
parser_remove_domain.add_argument('location (optional)', nargs='?', help='the location of the domain')
parser_remove_domain.set_defaults(func=run_remove_domain)

if len(sys.argv) == 1:
    parser.print_help()
    sys.exit()

args = parser.parse_args()

if args.command == 'add' and args.add_command is None:
    parser_add.print_help()
    sys.exit()
elif args.command == 'rm' and args.remove_command is None:
    parser_remove.print_help()
    sys.exit()
elif args.command == 'set' and args.set_command is None:
    parser_set.print_help()
    sys.exit()

if hasattr(args, 'func'):
    args.func(args)
else:
    parser.print_help()