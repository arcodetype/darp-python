import argparse
import json
import os
import subprocess
import sys

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

def run_deploy(args):
    print('Deploying Container Development')

def run_add_subdomain(args):
    filename = '.config.json'

    user_config = get_user_config(filename)

    existing_subdomain = get_nested(user_config, ['subdomains', args.name])
    if existing_subdomain is not None:
        print(f"subdomain {args.name} already exists at {existing_subdomain['location']}")
        sys.exit()

    subdomains = user_config.get('subdomains')
    if subdomains is None:
        user_config['subdomains'] = {}

    subdomain = {
        'location': args.location,
    }
    user_config['subdomains'][args.name] = subdomain

    with open(filename, 'w') as f:
        json.dump(user_config, f, indent=4)

    print(f"created '{args.name}' at {args.location}")

def run_remove_subdomain(args):
    filename = '.config.json'

    user_config = get_user_config(filename)

    existing_subdomain = get_nested(user_config, ['subdomains', args.name])
    if existing_subdomain is None:
        print(f"Subdomains, {args.name}, does not exist")
        sys.exit()

    del user_config['subdomains'][args.name]

    with open(filename, 'w') as f:
        json.dump(user_config, f, indent=4)

    print(f"removed '{args.name}'")
        
def run_shell(args):
    filename='.config.json'    

    user_config = get_user_config(filename)
    environment = get_nested(user_config, ['environments', args.environment])

    if environment is None:
        print("Environment does not exist.")
        sys.exit()

    podman_command = []
    podman_command.extend(["podman", "run"])
    podman_command.extend(["--rm", "-it"])

    current_directory = os.getcwd()
    current_directory_name = os.path.basename(current_directory)
    
    parent_directory = os.path.dirname(current_directory)
    parent_directory_name = os.path.basename(parent_directory)

    subdomain = get_nested(user_config, ['subdomains', parent_directory_name])

    if subdomain is None:
        print(f"Subdomain, {parent_directory_name}, does not exist in cdev's subdomain configuration.")
        sys.exit()

    container_name = 'local_' + parent_directory_name + '_' + current_directory_name
    podman_command.extend(['--name', container_name])
    podman_command.extend(['-v', f"{current_directory}:/app"])

    for volume in environment.get('volumes', []):
        if not os.path.exists(volume['host'].replace("$(pwd)", current_directory)):
            print(f"Volume, {volume['host']}, does not appear to exist.")
            sys.exit()
        podman_command.extend(['-v', f"{volume['host']}:{volume['container']}".replace("$(pwd)", current_directory)])

    podman_command.extend([args.container_image, 'sh'])

    # Inherits your terminal's stdin/stdout/stderr and TTY.
    subprocess.run(podman_command, check=True)

def run_set_domain(args):
    filename = '.config.json'

    user_config = get_user_config(filename)
    user_config['domain'] = args.name

    with open(filename, 'w') as f:
        json.dump(user_config, f, indent=4)

    print(f"Domain set to  '{args.name}'")

# Command Line Interactions

parser = argparse.ArgumentParser(
    prog='Container Development',
    description='Spinning up local environments based on container images that can interact with each other.',
    epilog='For any questions, please attend the Arcodetype livestream (when it\'s on!)'
)

subparsers = parser.add_subparsers(dest='command', help='subcommand help')

# cdev deploy
parser_deploy = subparsers.add_parser('deploy', help='deploys the environment')
parser_deploy.set_defaults(func=run_deploy)

# cdev shell
parser_shell = subparsers.add_parser('shell', help='starts a shell instance')
parser_shell.add_argument('environment', help='The name of the environment to start the shell in')
parser_shell.add_argument('container_image', help='The container image from which to create the shell instance')
parser_shell.set_defaults(func=run_shell)

# cdev set
parser_set = subparsers.add_parser('set', help='set config value')
subparser_set = parser_set.add_subparsers(dest='set_command', help='set any of the following in the config')

# cdev set domain
parser_set_domain = subparser_set.add_parser('domain', help='set domain')
parser_set_domain.add_argument('name', help='the name of the domain')
parser_set_domain.set_defaults(func=run_set_domain)

# cdev add
parser_add = subparsers.add_parser('add', help='add to config')
subparser_add = parser_add.add_subparsers(dest='add_command', help='add any of the following to the config')

# cdev add subdomain
parser_add_subdomain = subparser_add.add_parser('subdomain', help='add subdomain')
parser_add_subdomain.add_argument('name', help='the name of the subdomain')
parser_add_subdomain.add_argument('location', help='the location of the subdomain')
parser_add_subdomain.set_defaults(func=run_add_subdomain)

# cdev remove
parser_remove = subparsers.add_parser('rm', help='remove from config')
subparser_remove = parser_remove.add_subparsers(dest='remove_command', help='remove any of the following from the config')

# cdev remove subdomain
parser_remove_domain = subparser_remove.add_parser('subdomain', help='remove subdomain')
parser_remove_domain.add_argument('name', help='the name of the subdomain')
parser_remove_domain.add_argument('location (optional)', nargs='?', help='the location of the subdomain')
parser_remove_domain.set_defaults(func=run_remove_subdomain)

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