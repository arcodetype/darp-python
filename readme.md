# Container Development

## Customer Goals

- Easily be able to start a project using any programming language will eventually be possible
- Have this project interact with other projects that are running locally

## Setup

- Have a directory for projects that get a port and DNS settings automatically assigned to them:

- products.<service-name>.arcodetype.test
- projects.<service-name>.arcodetype.test
- sandbox.<service-name>.arcodetype.test

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
