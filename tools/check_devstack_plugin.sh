#!/bin/bash -x

NODEPOOL_INSTALL=${NODEPOOL_INSTALL:-/opt/stack/new/nodepool-venv}
NODEPOOL_CONFIG=${NODEPOOL_CONFIG:-/etc/nodepool/nodepool.yaml}
NODEPOOL_SECURE=${NODEPOOL_SECURE:-/etc/nodepool/secure.conf}
NODEPOOL="$NODEPOOL_INSTALL/bin/nodepool -c $NODEPOOL_CONFIG -s $NODEPOOL_SECURE"

# Flags to control which images we build.
# NOTE(pabelanger): Be sure to also update devstack/settings if you change the
# defaults.
NODEPOOL_PAUSE_CENTOS_7_DIB=${NODEPOOL_PAUSE_CENTOS_7_DIB:-true}
NODEPOOL_PAUSE_FEDORA_24_DIB=${NODEPOOL_PAUSE_FEDORA_24_DIB:-true}
NODEPOOL_PAUSE_UBUNTU_PRECISE_DIB=${NODEPOOL_PAUSE_UBUNTU_PRECISE_DIB:-true}
NODEPOOL_PAUSE_UBUNTU_TRUSTY_DIB=${NODEPOOL_PAUSE_UBUNTU_TRUSTY_DIB:-false}
NODEPOOL_PAUSE_UBUNTU_XENIAL_DIB=${NODEPOOL_PAUSE_UBUNTU_XENIAL_DIB:-true}

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

if [ $NODEPOOL_PAUSE_CENTOS_7_DIB = 'false' ]; then
    # check that image built
    waitforimage centos-7
    # check image was bootable
    waitfornode centos-7
fi

if [ $NODEPOOL_PAUSE_FEDORA_24_DIB = 'false' ]; then
    # check that image built
    waitforimage fedora-24
    # check image was bootable
    waitfornode fedora-24
fi

if [ $NODEPOOL_PAUSE_UBUNTU_PRECISE_DIB = 'false' ]; then
    # check that image built
    waitforimage ubuntu-precise
    # check image was bootable
    waitfornode ubuntu-precise
fi

if [ $NODEPOOL_PAUSE_UBUNTU_TRUSTY_DIB = 'false' ]; then
    # check that image built
    waitforimage ubuntu-trusty
    # check image was bootable
    waitfornode ubuntu-trusty
fi

if [ $NODEPOOL_PAUSE_UBUNTU_XENIAL = 'false' ]; then
    # check that image built
    waitforimage ubuntu-xenial
    # check image was bootable
    waitfornode ubuntu-xenial
fi

set -o errexit
# Show the built nodes
$NODEPOOL list

# Try to delete the nodes that were just built
$NODEPOOL delete --now 1

# show the deleted nodes (and their replacements may be building)
$NODEPOOL list
