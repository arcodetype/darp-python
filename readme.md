# Container Development

## Goals

- Easily be able to start a project using any programming language
- Have this project interact with other projects that are running locally

## Required Setup

1. set a domain

## Build

```sh
pyinstaller --name cdev --onefile run.py
```

## Setup

Have a directory for projects that get a port and DNS settings automatically assigned to them:

- products.<service-name>.arcodetype.test
- projects.<service-name>.arcodetype.test
- sandbox.<service-name>.arcodetype.test

```sh
podman machine init --cpus 6 --disk-size 100 --memory 8192 \
  -v /Users:/Users -v /private:/private -v /var/folders:/var/folders -v /Volumes/ritic/users:/Volumes/ritic/users

# Init and start the VM
podman machine init --now \
  --cpus 6 --disk-size 100 --memory 8192 \
  -v /Users:/Users -v /private:/private -v /var/folders:/var/folders -v /Volumes/ritic/users:/Volumes/ritic/users

# Set the sysctl inside the VM and apply it immediately
podman machine ssh "echo 'net.ipv4.ip_unprivileged_port_start=80' \
  | sudo tee /etc/sysctl.d/99-unprivileged-ports.conf >/dev/null && sudo sysctl --system"
```

### Ports

The API container port is always `8000`. The host API port is assigned on `cDev deploy` 

## Potential Commands

cdev serve <image>
cdev shell <image>
cdev serve -e go <go-image>
cdev shell -e go <go-image>
cdev serve -e laravel <php-image>
cdev shell -e laravel <php-image>
cdev serve -e node <node-image>
cdev shell -e node <node-image>
cdev serve -e python <python-image>
cdev shell -e python <python-image>
cdev serve -e vue <vue-image>
cdev shell -e vue <vue-image>
