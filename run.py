import argparse
import json
import os
import sys

print('Deploying Container Development')


def run_deploy(args):
    print(args)
    print('Running deploy')

def run_add(args):
    filename = '.user_config.json'

    if not os.path.exists(filename):
        with open(filename, 'w') as f:
            json.dump({}, f, indent=4)
            print(f"Created {filename}")

    with open(filename) as f:
        try:
            user_config = json.load(f)
        except json.decoder.JSONDecodeError:
            print(f"Not Json {filename}")
            exit

    subdomains = user_config.get('subdomains')
    if subdomains is None:
        user_config['subdomains'] = []

    existing_subdomain = next((sd for sd in subdomains if (sd['name'] == args.name or sd['location'] == args.location)), None)
    if existing_subdomain is not None:
        print(f"subdomain {existing_subdomain['name']} already exists at {existing_subdomain['location']}")
        sys.exit()

    subdomain = {
        'name': args.name,
        'location': args.location,
    }
    user_config.get('subdomains').append(subdomain)

    with open(filename, 'w') as f:
        json.dump(user_config, f, indent=4)

    print(f"created '{args.name}' at {args.location}")

def run_remove(args):
    filename = '.user_config.json'

    if not os.path.exists(filename):
        with open(filename, 'w') as f:
            json.dump({}, f, indent=4)
            print(f"Created {filename}")

    with open(filename) as f:
        try:
            user_config = json.load(f)
        except json.decoder.JSONDecodeError:
            print(f"Not Json {filename}")
            exit

    subdomains = user_config.get('subdomains')
    if subdomains is None:
        user_config['subdomains'] = []

    if len(subdomains) == 0:
        print("No subdomains to remove")
        sys.exit()

    new_subdomains = [sd for sd in subdomains if sd['name'] != args.name]
    
    user_config['subdomains'] = new_subdomains

    with open(filename, 'w') as f:
        json.dump(user_config, f, indent=4)

    print(f"removed '{args.name}'")
        

parser = argparse.ArgumentParser(
    prog='Container Development',
    description='Spinning up local environments based on container images that can interact with each other.',
    epilog='The Epilogue'
)

subparsers = parser.add_subparsers(help='subcommand help')

parser_deploy = subparsers.add_parser('deploy', help='deploys the environment')
parser_deploy.set_defaults(func=run_deploy)

parser_add = subparsers.add_parser('add', help='add a subdomain to the container development')
parser_add.add_argument('-l', '--location', help='the location of the subdomain', required=True)
parser_add.add_argument('-n', '--name', help='the name of the subdomain', required=True)
parser_add.set_defaults(func=run_add)

parser_remove = subparsers.add_parser('remove', help='remove a subdomain to the container development')
parser_remove.add_argument('-n', '--name', help='the name of the subdomain', required=True)
parser_remove.add_argument('-l', '--location', help='the location of the subdomain (not used)', required=False)
parser_remove.set_defaults(func=run_remove)


if len(sys.argv) == 1:
    parser.print_help()
    print('exiting')
    sys.exit()

args = parser.parse_args()

args.func(args)
