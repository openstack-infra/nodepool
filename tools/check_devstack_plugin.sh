#!/bin/bash -ex

LOGDIR=$1

# Set to indiciate an error return
RETURN=0
FAILURE_REASON=""

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
NODEPOOL_PAUSE_DEBIAN_STRETCH_DIB=${NODEPOOL_PAUSE_DEBIAN_STRETCH_DIB:-True}
NODEPOOL_PAUSE_FEDORA_29_DIB=${NODEPOOL_PAUSE_FEDORA_29_DIB:-True}
NODEPOOL_PAUSE_UBUNTU_BIONIC_DIB=${NODEPOOL_PAUSE_UBUNTU_BIONIC_DIB:-True}
NODEPOOL_PAUSE_UBUNTU_TRUSTY_DIB=${NODEPOOL_PAUSE_UBUNTU_TRUSTY_DIB:-True}
NODEPOOL_PAUSE_UBUNTU_XENIAL_DIB=${NODEPOOL_PAUSE_UBUNTU_XENIAL_DIB:-True}
NODEPOOL_PAUSE_OPENSUSE_423_DIB=${NODEPOOL_PAUSE_OPENSUSE_423_DIB:-True}
NODEPOOL_PAUSE_OPENSUSE_150_DIB=${NODEPOOL_PAUSE_OPENSUSE_150_DIB:-True}
NODEPOOL_PAUSE_OPENSUSE_TUMBLEWEED_DIB=${NODEPOOL_PAUSE_OPENSUSE_TUMBLEWEED_DIB:-True}
NODEPOOL_PAUSE_GENTOO_17_0_SYSTEMD_DIB=${NODEPOOL_PAUSE_GENTOO_17_0_SYSTEMD_DIB:-True}

function sshintonode {
    name=$1
    state='ready'

    node=`$NODEPOOL list | grep $name | grep $state | cut -d '|' -f6 | tr -d ' '`
    /tmp/ssh_wrapper $node ls /

    # Check that the root partition grew on boot; it should be a 5GiB
    # partition minus some space for the boot partition.  However
    # emperical evidence suggests there is some modulo maths going on,
    # (possibly with alignment?) that means we can vary up to even
    # 64MiB.  Thus we choose an expected value that gives us enough
    # slop to avoid false matches, but still indicates we resized up.
    root_size=$(/tmp/ssh_wrapper $node -- lsblk -rbno SIZE /dev/vda1)
    expected_root_size=$(( 5000000000 ))
    if [[ $root_size -lt $expected_root_size ]]; then
        echo "*** Root device does not appear to have grown: $root_size"
        FAILURE_REASON="Root partition of $name does not appear to have grown: $root_size < $expected_root_size"
        RETURN=1
    fi

    # Check we saw metadata deployed to the config-drive
    /tmp/ssh_wrapper $node \
        "dd status=none if=/dev/sr0 | tr -cd '[:print:]' | grep -q nodepool_devstack"
    if [[ $? -ne 0 ]]; then
        echo "*** Failed to find metadata in config-drive"
        FAILURE_REASON="Failed to find meta-data in config-drive for $node"
        RETURN=1
    fi
}

function showserver {
    name=$1
    state='ready'

    node_id=`$NODEPOOL list | grep $name | grep $state | cut -d '|' -f5 | tr -d ' '`
    EXPECTED=$(mktemp)
    RESULT=$(mktemp)
    source /opt/stack/devstack/openrc admin admin

    nova show $node_id | grep -Eo "user_data[ ]+.*|[ ]*$" | awk {'print $3'} |\
    base64 --decode > $RESULT
    cat <<EOF >$EXPECTED
#cloud-config
write_files:
- content: |
    testpassed
  path: /etc/testfile_nodepool_userdata
EOF
    diff $EXPECTED $RESULT
    if [[ $? -ne 0 ]]; then
        echo "*** Failed to find userdata on server!"
        FAILURE_REASON="Failed to find userdata on server for $node"
        echo "Expected userdata:"
        cat $EXPECTED
        echo "Found userdata:"
        cat $RESULT
        RETURN=1
    fi
}

function checknm {
    name=$1
    state='ready'

    node=`$NODEPOOL list | grep $name | grep $state | cut -d '|' -f6 | tr -d ' '`
    nm_output=$(/tmp/ssh_wrapper $node -- nmcli c)

    # virtio device is eth0 on older, ens3 on newer
    if [[ ! ${nm_output} =~ (eth0|ens3) ]]; then
        echo "*** Failed to find interface in NetworkManager connections"
        /tmp/ssh_wrapper $node -- nmcli c
        /tmp/ssh_wrapper $node -- nmcli device
        FAILURE_REASON="Failed to find interface in NetworkManager connections"
        RETURN=1
    fi
}

function waitforimage {
    local name=$1
    local state='ready'
    local builds

    while ! $NODEPOOL image-list | grep $name | grep $state; do
        $NODEPOOL image-list > ${LOGDIR}/nodepool-image-list.txt
        $NODEPOOL list --detail > ${LOGDIR}/nodepool-list.txt

        builds=$(ls -l /var/log/nodepool/builds/ | grep $name | wc -l)
        if [[ ${builds} -ge 4 ]]; then
            echo "*** Build of $name failed at least 3 times, aborting"
            exit 1
        fi
        sleep 10
    done
}

function waitfornode {
    name=$1
    state='ready'

    while ! $NODEPOOL list | grep $name | grep $state | grep "unlocked"; do
        $NODEPOOL image-list > ${LOGDIR}/nodepool-image-list.txt
        $NODEPOOL list --detail > ${LOGDIR}/nodepool-list.txt
        sleep 10
    done
}

if [ ${NODEPOOL_PAUSE_CENTOS_7_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage centos-7
    # check image was bootable
    waitfornode centos-7
    # check ssh for root user
    sshintonode centos-7
    # networkmanager check
    checknm centos-7
    # userdata check
    showserver centos-7
fi

if [ ${NODEPOOL_PAUSE_DEBIAN_STRETCH_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage debian-stretch
    # check image was bootable
    waitfornode debian-stretch
    # check ssh for root user
    sshintonode debian-stretch
    # userdata check
    showserver debian-stretch
fi

if [ ${NODEPOOL_PAUSE_FEDORA_29_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage fedora-29
    # check image was bootable
    waitfornode fedora-29
    # check ssh for root user
    sshintonode fedora-29
    # networkmanager check
    checknm fedora-29
    # userdata check
    showserver fedora-29
fi

if [ ${NODEPOOL_PAUSE_UBUNTU_BIONIC_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage ubuntu-bionic
    # check image was bootable
    waitfornode ubuntu-bionic
    # check ssh for root user
    sshintonode ubuntu-bionic
    # userdata check
    showserver ubuntu-bionic
fi

if [ ${NODEPOOL_PAUSE_UBUNTU_TRUSTY_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage ubuntu-trusty
    # check image was bootable
    waitfornode ubuntu-trusty
    # check ssh for root user
    sshintonode ubuntu-trusty
    # userdata check
    showserver ubuntu-trusty
fi

if [ ${NODEPOOL_PAUSE_UBUNTU_XENIAL_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage ubuntu-xenial
    # check image was bootable
    waitfornode ubuntu-xenial
    # check ssh for root user
    sshintonode ubuntu-xenial
    # userdata check
    showserver ubuntu-xenial
fi

if [ ${NODEPOOL_PAUSE_OPENSUSE_423_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage opensuse-423
    # check image was bootable
    waitfornode opensuse-423
    # check ssh for root user
    sshintonode opensuse-423
    # userdata check
    showserver opensuse-423
fi
if [ ${NODEPOOL_PAUSE_OPENSUSE_150_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage opensuse-150
    # check image was bootable
    waitfornode opensuse-150
    # check ssh for root user
    sshintonode opensuse-150
    # userdata check
    showserver opensuse-150
fi
if [ ${NODEPOOL_PAUSE_OPENSUSE_TUMBLEWEED_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage opensuse-tumbleweed
    # check image was bootable
    waitfornode opensuse-tumbleweed
    # check ssh for root user
    sshintonode opensuse-tumbleweed
    # userdata check
    showserver opensuse-tumbleweed
fi
if [ ${NODEPOOL_PAUSE_GENTOO_17_0_SYSTEMD_DIB,,} = 'false' ]; then
    # check that image built
    waitforimage gentoo-17-0-systemd
    # check image was bootable
    waitfornode gentoo-17-0-systemd
    # check ssh for root user
    sshintonode gentoo-17-0-systemd
    # userdata check
    showserver gentoo-17-0-systemd
fi

set -o errexit
# Show the built nodes
$NODEPOOL list

# Try to delete the nodes that were just built
$NODEPOOL delete --now 0000000000

# show the deleted nodes (and their replacements may be building)
$NODEPOOL list

if [[ -n "${FAILURE_REASON}" ]]; then
    echo "${FAILURE_REASON}"
fi
exit $RETURN
