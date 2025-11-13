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

def run_add(args):
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

def run_remove(args):
    filename = '.config.json'

    user_config = get_user_config(filename)

    existing_subdomain = get_nested(user_config, ['subdomains', args.name])
    if existing_subdomain is None:
        print("No subdomains to remove")
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


parser = argparse.ArgumentParser(
    prog='Container Development',
    description='Spinning up local environments based on container images that can interact with each other.',
    epilog='For any questions, please attend the Arcodetype livestream (when it\'s on!)'
)

subparsers = parser.add_subparsers(help='subcommand help')

parser_deploy = subparsers.add_parser('deploy', help='deploys the environment')
parser_deploy.set_defaults(func=run_deploy)

parser_shell = subparsers.add_parser('shell', help='starts a shell instance')
parser_shell.add_argument('environment')
parser_shell.add_argument('container_image')
parser_shell.set_defaults(func=run_shell)

parser_add = subparsers.add_parser('add_subdomain', help='add a subdomain to the container development')
parser_add.add_argument('-l', '--location', help='the location of the subdomain', required=True)
parser_add.add_argument('-n', '--name', help='the name of the subdomain', required=True)
parser_add.set_defaults(func=run_add)

parser_remove = subparsers.add_parser('remove_subdomain', help='remove a subdomain to the container development')
parser_remove.add_argument('-n', '--name', help='the name of the subdomain', required=True)
parser_remove.add_argument('-l', '--location', help='the location of the subdomain (not used)', required=False)
parser_remove.set_defaults(func=run_remove)


if len(sys.argv) == 1:
    parser.print_help()
    sys.exit()

args = parser.parse_args()

args.func(args)
