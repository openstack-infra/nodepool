#!/bin/bash -ex

LOGDIR=$1

NODEPOOL_INSTALL=${NODEPOOL_INSTALL:-/opt/stack/nodepool-venv}
NODEPOOL_CONFIG=${NODEPOOL_CONFIG:-/etc/nodepool/nodepool.yaml}
NODEPOOL_SECURE=${NODEPOOL_SECURE:-/etc/nodepool/secure.conf}
NODEPOOL="$NODEPOOL_INSTALL/bin/nodepool -c $NODEPOOL_CONFIG -s $NODEPOOL_SECURE"

# Source stackrc so that we get the variables about enabled images set
# from the devstack job.  That's the ones we'll wait for below.
if [[ ! -f /opt/stack/devstack/stackrc ]]; then
    echo "Can not find enabled images from devstack run!"
    exit 1
else
    source /opt/stack/devstack/stackrc
fi
NODEPOOL_PAUSE_CENTOS_7_DIB=${NODEPOOL_PAUSE_CENTOS_7_DIB:-True}
NODEPOOL_PAUSE_DEBIAN_JESSIE_DIB=${NODEPOOL_PAUSE_DEBIAN_JESSIE_DIB:-True}
NODEPOOL_PAUSE_FEDORA_25_DIB=${NODEPOOL_PAUSE_FEDORA_25_DIB:-True}
NODEPOOL_PAUSE_FEDORA_26_DIB=${NODEPOOL_PAUSE_FEDORA_26_DIB:-True}
NODEPOOL_PAUSE_UBUNTU_TRUSTY_DIB=${NODEPOOL_PAUSE_UBUNTU_TRUSTY_DIB:-True}
NODEPOOL_PAUSE_UBUNTU_XENIAL_DIB=${NODEPOOL_PAUSE_UBUNTU_XENIAL_DIB:-True}
NODEPOOL_PAUSE_OPENSUSE_423_DIB=${NODEPOOL_PAUSE_OPENSUSE_423_DIB:-True}

function waitforimage {
    name=$1
    state='ready'

    while ! $NODEPOOL image-list | grep $name | grep $state; do
        $NODEPOOL image-list > ${LOGDIR}/nodepool-image-list.txt
        $NODEPOOL list > ${LOGDIR}/nodepool-list.txt
        sleep 10
    done
}

function waitfornode {
    name=$1
    state='ready'

    while ! $NODEPOOL list | grep $name | grep $state | grep "unlocked"; do
        $NODEPOOL image-list > ${LOGDIR}/nodepool-image-list.txt
        $NODEPOOL list > ${LOGDIR}/nodepool-list.txt
        sleep 10
    done
}

if [ ${NODEPOOL_PAUSE_CENTOS_7_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage centos-7
    # check image was bootable
    waitfornode centos-7
fi

if [ ${NODEPOOL_PAUSE_DEBIAN_JESSIE_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage debian-jessie
    # check image was bootable
    waitfornode debian-jessie
fi

if [ ${NODEPOOL_PAUSE_FEDORA_25_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage fedora-25
    # check image was bootable
    waitfornode fedora-25
fi

if [ ${NODEPOOL_PAUSE_FEDORA_26_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage fedora-26
    # check image was bootable
    waitfornode fedora-26
fi

if [ ${NODEPOOL_PAUSE_UBUNTU_TRUSTY_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage ubuntu-trusty
    # check image was bootable
    waitfornode ubuntu-trusty
fi

if [ ${NODEPOOL_PAUSE_UBUNTU_XENIAL_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage ubuntu-xenial
    # check image was bootable
    waitfornode ubuntu-xenial
fi

if [ ${NODEPOOL_PAUSE_OPENSUSE_423_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage opensuse-423
    # check image was bootable
    waitfornode opensuse-423
fi

set -o errexit
# Show the built nodes
$NODEPOOL list

# Try to delete the nodes that were just built
$NODEPOOL delete --now 0000000000

# show the deleted nodes (and their replacements may be building)
$NODEPOOL list
