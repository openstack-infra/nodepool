#!/bin/bash -x

NODEPOOL_CONFIG=${NODEPOOL_CONFIG:-/etc/nodepool/nodepool.yaml}
NODEPOOL_SECURE=${NODEPOOL_SECURE:-/etc/nodepool/secure.conf}
NODEPOOL="nodepool -c $NODEPOOL_CONFIG -s $NODEPOOL_SECURE"

function waitforimage {
    name=$1
    state='ready'

    while ! $NODEPOOL image-list | grep $name | grep $state; do
        $NODEPOOL image-list > /tmp/.nodepool-image-list.txt
        $NODEPOOL list > /tmp/.nodepool-list.txt
        sudo mv /tmp/.nodepool-image-list.txt $WORKSPACE/logs/nodepool-image-list.txt
        sudo mv /tmp/.nodepool-list.txt $WORKSPACE/logs/nodepool-list.txt
        sleep 10
    done
}

function waitfornode {
    name=$1
    state='ready'

    while ! $NODEPOOL list | grep $name | grep $state; do
        $NODEPOOL image-list > /tmp/.nodepool-image-list.txt
        $NODEPOOL list > /tmp/.nodepool-list.txt
        sudo mv /tmp/.nodepool-image-list.txt $WORKSPACE/logs/nodepool-image-list.txt
        sudo mv /tmp/.nodepool-list.txt $WORKSPACE/logs/nodepool-list.txt
        sleep 10
    done
}

# Check that snapshot image built
waitforimage trusty-server
# check that dib image built
waitforimage ubuntu-dib

# check snapshot image was bootable
waitfornode trusty-server
# check dib image was bootable
waitfornode ubuntu-dib

set -o errexit
# Show the built nodes
$NODEPOOL list

# Try to delete the nodes that were just built
$NODEPOOL delete --now 1
$NODEPOOL delete --now 2

# show the deleted nodes (and their replacements may be building)
$NODEPOOL list
