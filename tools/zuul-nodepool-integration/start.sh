#!/bin/bash -e

cd "$(dirname "$0")"

mkdir -p /tmp/nodepool/images
mkdir -p /tmp/nodepool/log

export OS_CLIENT_CONFIG_FILE=`pwd`/clouds.yaml

nodepool-builder -c `pwd`/nodepool.yaml -l `pwd`/builder-logging.conf -p /tmp/nodepool/builder.pid --fake
nodepool-launcher -c `pwd`/nodepool.yaml -s `pwd`/secure.conf -l `pwd`/launcher-logging.conf -p /tmp/nodepool/launcher.pid
