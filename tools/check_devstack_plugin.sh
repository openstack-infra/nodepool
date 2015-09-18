#!/bin/bash -ex

# Sleep long enough for the below checks to have a chance
# at being completed.
sleep 15m

NODEPOOL_CONFIG=${NODEPOOL_CONFIG:-/etc/nodepool/nodepool.yaml}
NODEPOOL_CMD="nodepool -c $NODEPOOL_CONFIG"
# Check that snapshot image built
$NODEPOOL_CMD image-list | grep ready | grep trusty-server
# check that dib image built
$NODEPOOL_CMD image-list | grep ready | grep ubuntu-dib
# check snapshot image was bootable
$NODEPOOL_CMD list | grep ready | grep trusty-server
# check dib image was bootable
$NODEPOOL_CMD list | grep ready | grep ubuntu-dib
