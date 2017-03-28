#!/bin/bash
#
# Copyright 2015 Hewlett-Packard Development Company, L.P.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

NODEPOOL_PUBKEY=$HOME/.ssh/id_nodepool.pub
NODEPOOL_INSTALL=$HOME/nodepool-venv
NODEPOOL_CACHE_GET_PIP=/opt/stack/cache/files/get-pip.py

# Install shade from git if requested. If not requested
# nodepool install will pull it in.
function install_shade {
    if use_library_from_git "shade"; then
        GITREPO["shade"]=$SHADE_REPO_URL
        GITDIR["shade"]=$DEST/shade
        GITBRANCH["shade"]=$SHADE_REPO_REF
        git_clone_by_name "shade"
        # Install shade globally, because the job config has LIBS_FROM_GIT
        # and if we don't install it globally, all hell breaks loose
        setup_dev_lib "shade"
        # BUT - install shade into a virtualenv so that we don't have issues
        # with OpenStack constraints affecting the shade dependency install.
        # This particularly shows up with os-client-config
        $NODEPOOL_INSTALL/bin/pip install -e $DEST/shade
    fi
}

function install_diskimage_builder {
    if use_library_from_git "diskimage-builder"; then
        GITREPO["diskimage-builder"]=$DISKIMAGE_BUILDER_REPO_URL
        GITDIR["diskimage-builder"]=$DEST/diskimage-builder
        GITBRANCH["diskimage-builder"]=$DISKIMAGE_BUILDER_REPO_REF
        git_clone_by_name "diskimage-builder"
        setup_dev_lib "diskimage-builder"
        $NODEPOOL_INSTALL/bin/pip install -e $DEST/diskimage-builder
    fi
}

function install_glean {
    if use_library_from_git "glean"; then
        GITREPO["glean"]=$GLEAN_REPO_URL
        GITDIR["glean"]=$DEST/glean
        GITBRANCH["glean"]=$GLEAN_REPO_REF
        git_clone_by_name "glean"
        setup_dev_lib "glean"
        $NODEPOOL_INSTALL/bin/pip install -e $DEST/glean
    fi
}


# Install nodepool code
function install_nodepool {
    virtualenv $NODEPOOL_INSTALL
    install_shade
    install_diskimage_builder
    install_glean

    setup_develop $DEST/nodepool
    $NODEPOOL_INSTALL/bin/pip install -e $DEST/nodepool
}

# requires some globals from devstack, which *might* not be stable api
# points. If things break, investigate changes in those globals first.

function nodepool_write_elements {
    sudo mkdir -p $(dirname $NODEPOOL_CONFIG)/elements/nodepool-setup/install.d
    cat > /tmp/01-nodepool-setup <<EOF
sudo mkdir -p /etc/nodepool
# Make it world writeable so nodepool can write here later.
sudo chmod 777 /etc/nodepool
EOF

    sudo mv /tmp/01-nodepool-setup \
        $(dirname $NODEPOOL_CONFIG)/elements/nodepool-setup/install.d/01-nodepool-setup
    sudo chmod a+x \
        $(dirname $NODEPOOL_CONFIG)/elements/nodepool-setup/install.d/01-nodepool-setup
    sudo mkdir -p $NODEPOOL_DIB_BASE_PATH/images
    sudo mkdir -p $NODEPOOL_DIB_BASE_PATH/tmp
    sudo mkdir -p $NODEPOOL_DIB_BASE_PATH/cache
    sudo chown -R stack:stack $NODEPOOL_DIB_BASE_PATH
}

function nodepool_write_config {
    sudo mkdir -p $(dirname $NODEPOOL_CONFIG)
    sudo mkdir -p $(dirname $NODEPOOL_SECURE)

    cat > /tmp/logging.conf <<EOF
[formatters]
keys=simple

[loggers]
keys=root,nodepool,shade,kazoo

[handlers]
keys=console

[logger_root]
level=WARNING
handlers=console

[logger_nodepool]
level=DEBUG
handlers=console
qualname=nodepool
propagate=0

[logger_shade]
level=DEBUG
handlers=console
qualname=shade
propagate=0

[logger_kazoo]
level=INFO
handlers=console
qualname=kazoo
propagate=0

[handler_console]
level=DEBUG
class=StreamHandler
formatter=simple
args=(sys.stdout,)

[formatter_simple]
format=%(asctime)s %(levelname)s %(name)s: %(message)s
datefmt=
EOF

    sudo mv /tmp/logging.conf $NODEPOOL_LOGGING

    cat > /tmp/secure.conf << EOF
# Empty
EOF
    sudo mv /tmp/secure.conf $NODEPOOL_SECURE

    if use_library_from_git "glean"; then
        git --git-dir=$DEST/glean/.git checkout -b devstack
        DIB_GLEAN_INSTALLTYPE="DIB_INSTALLTYPE_simple_init: 'repo'"
        DIB_GLEAN_REPOLOCATION="DIB_REPOLOCATION_glean: '$DEST/glean'"
        DIB_GLEAN_REPOREF="DIB_REPOREF_glean: 'devstack'"
    fi

    if [ -f $NODEPOOL_CACHE_GET_PIP ] ; then
        DIB_GET_PIP="DIB_REPOLOCATION_pip_and_virtualenv: file://$NODEPOOL_CACHE_GET_PIP"
    fi
    cat > /tmp/nodepool.yaml <<EOF
# You will need to make and populate this path as necessary,
# cloning nodepool does not do this. Further in this doc we have an
# example element.
elements-dir: $(dirname $NODEPOOL_CONFIG)/elements
images-dir: $NODEPOOL_DIB_BASE_PATH/images

zookeeper-servers:
  - host: localhost
    port: 2181

labels:
  - name: centos-7
    min-ready: 1
  - name: fedora-25
    min-ready: 1
  - name: ubuntu-precise
    min-ready: 1
  - name: ubuntu-trusty
    min-ready: 1
  - name: ubuntu-xenial
    min-ready: 1

providers:
  - name: devstack
    region-name: '$REGION_NAME'
    cloud: devstack
    # Long boot timeout to deal with potentially nested virt.
    boot-timeout: 600
    launch-timeout: 900
    rate: 0.25
    diskimages:
      - name: centos-7
        config-drive: true
      - name: fedora-25
        config-drive: true
      - name: ubuntu-precise
        config-drive: true
      - name: ubuntu-trusty
        config-drive: true
      - name: ubuntu-xenial
        config-drive: true
    pools:
      - name: main
        max-servers: 5
        labels:
          - name: centos-7
            diskimage: centos-7
            min-ram: 1024
            name-filter: 'nodepool'
          - name: fedora-25
            diskimage: fedora-25
            min-ram: 1024
            name-filter: 'nodepool'
          - name: ubuntu-precise
            diskimage: ubuntu-precise
            min-ram: 512
            name-filter: 'nodepool'
          - name: ubuntu-trusty
            diskimage: ubuntu-trusty
            min-ram: 512
            name-filter: 'nodepool'
          - name: ubuntu-xenial
            diskimage: ubuntu-xenial
            min-ram: 512
            name-filter: 'nodepool'

diskimages:
  - name: centos-7
    pause: $NODEPOOL_PAUSE_CENTOS_7_DIB
    rebuild-age: 86400
    elements:
      - centos-minimal
      - vm
      - simple-init
      - devuser
      - openssh-server
      - nodepool-setup
    env-vars:
      TMPDIR: $NODEPOOL_DIB_BASE_PATH/tmp
      DIB_CHECKSUM: '1'
      DIB_IMAGE_CACHE: $NODEPOOL_DIB_BASE_PATH/cache
      DIB_DEV_USER_AUTHORIZED_KEYS: $NODEPOOL_PUBKEY
      $DIB_GET_PIP
      $DIB_GLEAN_INSTALLTYPE
      $DIB_GLEAN_REPOLOCATION
      $DIB_GLEAN_REPOREF
  - name: fedora-25
    pause: $NODEPOOL_PAUSE_FEDORA_25_DIB
    rebuild-age: 86400
    elements:
      - fedora-minimal
      - vm
      - simple-init
      - devuser
      - openssh-server
      - nodepool-setup
    release: 25
    env-vars:
      TMPDIR: $NODEPOOL_DIB_BASE_PATH/tmp
      DIB_CHECKSUM: '1'
      DIB_IMAGE_CACHE: $NODEPOOL_DIB_BASE_PATH/cache
      DIB_DEV_USER_AUTHORIZED_KEYS: $NODEPOOL_PUBKEY
      $DIB_GET_PIP
      $DIB_GLEAN_INSTALLTYPE
      $DIB_GLEAN_REPOLOCATION
      $DIB_GLEAN_REPOREF
  - name: ubuntu-precise
    pause: $NODEPOOL_PAUSE_UBUNTU_PRECISE_DIB
    rebuild-age: 86400
    elements:
      - ubuntu-minimal
      - vm
      - simple-init
      - devuser
      - openssh-server
      - nodepool-setup
    release: precise
    env-vars:
      TMPDIR: $NODEPOOL_DIB_BASE_PATH/tmp
      DIB_CHECKSUM: '1'
      DIB_IMAGE_CACHE: $NODEPOOL_DIB_BASE_PATH/cache
      DIB_APT_LOCAL_CACHE: '0'
      DIB_DISABLE_APT_CLEANUP: '1'
      DIB_DEV_USER_AUTHORIZED_KEYS: $NODEPOOL_PUBKEY
      $DIB_GET_PIP
      $DIB_GLEAN_INSTALLTYPE
      $DIB_GLEAN_REPOLOCATION
      $DIB_GLEAN_REPOREF
  - name: ubuntu-trusty
    pause: $NODEPOOL_PAUSE_UBUNTU_TRUSTY_DIB
    rebuild-age: 86400
    elements:
      - ubuntu-minimal
      - vm
      - simple-init
      - devuser
      - openssh-server
      - nodepool-setup
    release: trusty
    env-vars:
      TMPDIR: $NODEPOOL_DIB_BASE_PATH/tmp
      DIB_CHECKSUM: '1'
      DIB_IMAGE_CACHE: $NODEPOOL_DIB_BASE_PATH/cache
      DIB_APT_LOCAL_CACHE: '0'
      DIB_DISABLE_APT_CLEANUP: '1'
      DIB_DEV_USER_AUTHORIZED_KEYS: $NODEPOOL_PUBKEY
      $DIB_GET_PIP
      $DIB_GLEAN_INSTALLTYPE
      $DIB_GLEAN_REPOLOCATION
      $DIB_GLEAN_REPOREF
  - name: ubuntu-xenial
    pause: $NODEPOOL_PAUSE_UBUNTU_XENIAL_DIB
    rebuild-age: 86400
    elements:
      - ubuntu-minimal
      - vm
      - simple-init
      - devuser
      - openssh-server
      - nodepool-setup
    release: xenial
    env-vars:
      TMPDIR: $NODEPOOL_DIB_BASE_PATH/tmp
      DIB_CHECKSUM: '1'
      DIB_IMAGE_CACHE: $NODEPOOL_DIB_BASE_PATH/cache
      DIB_APT_LOCAL_CACHE: '0'
      DIB_DISABLE_APT_CLEANUP: '1'
      DIB_DEV_USER_AUTHORIZED_KEYS: $NODEPOOL_PUBKEY
      $DIB_GET_PIP
      $DIB_GLEAN_INSTALLTYPE
      $DIB_GLEAN_REPOLOCATION
      $DIB_GLEAN_REPOREF
EOF

    sudo mv /tmp/nodepool.yaml $NODEPOOL_CONFIG
    cp /etc/openstack/clouds.yaml /tmp
    cat >>/tmp/clouds.yaml <<EOF
cache:
  expiration:
    floating-ip: 5
    server: 5
    port: 5
# TODO(pabelanger): Remove once glean fully supports IPv6.
client:
  force_ipv4: True
EOF
    sudo mv /tmp/clouds.yaml /etc/openstack/clouds.yaml
    mkdir -p $HOME/.cache/openstack/
}

# Create configs
# Setup custom flavor
function configure_nodepool {
    # write the nodepool config
    nodepool_write_config

    # write the elements
    nodepool_write_elements
}

function start_nodepool {
    # build a custom flavor that's more friendly to nodepool
    local available_flavors=$(nova flavor-list)
    if [[ ! ( $available_flavors =~ 'nodepool-512' ) ]]; then
        nova flavor-create nodepool-512 64 512 0 1
    fi
    if [[ ! ( $available_flavors =~ 'nodepool-1024' ) ]]; then
        nova flavor-create nodepool-1024 128 1024 0 1
    fi

    # build sec group rules to reach the nodes, we need to do this
    # this late because nova hasn't started until this phase.
    if [[ -z $(nova secgroup-list-rules default | grep 'tcp' | grep '65535') ]]; then
        nova --os-project-name demo --os-username demo \
             secgroup-add-rule default tcp 1 65535 0.0.0.0/0
        nova --os-project-name demo --os-username demo \
             secgroup-add-rule default udp 1 65535 0.0.0.0/0
    fi

    export PATH=$NODEPOOL_INSTALL/bin:$PATH

    # run a fake statsd so we test stats sending paths
    export STATSD_HOST=localhost
    export STATSD_PORT=8125
    run_process statsd "socat -u udp-recv:$STATSD_PORT -"

    run_process nodepool "$NODEPOOL_INSTALL/bin/nodepoold -c $NODEPOOL_CONFIG -s $NODEPOOL_SECURE -l $NODEPOOL_LOGGING -d"
    run_process nodepool-builder "$NODEPOOL_INSTALL/bin/nodepool-builder -c $NODEPOOL_CONFIG -l $NODEPOOL_LOGGING -d"
    :
}

function shutdown_nodepool {
    stop_process nodepool
    :
}

function cleanup_nodepool {
    :
}

# check for service enabled
if is_service_enabled nodepool; then

    if [[ "$1" == "stack" && "$2" == "install" ]]; then
        # Perform installation of service source
        echo_summary "Installing nodepool"
        install_nodepool

    elif [[ "$1" == "stack" && "$2" == "post-config" ]]; then
        # Configure after the other layer 1 and 2 services have been configured
        echo_summary "Configuring nodepool"
        configure_nodepool

    elif [[ "$1" == "stack" && "$2" == "extra" ]]; then
        # Initialize and start the nodepool service
        echo_summary "Initializing nodepool"
        start_nodepool
    fi

    if [[ "$1" == "unstack" ]]; then
        # Shut down nodepool services
        # no-op
        shutdown_nodepool
    fi

    if [[ "$1" == "clean" ]]; then
        # Remove state and transient data
        # Remember clean.sh first calls unstack.sh
        # no-op
        cleanup_nodepool
    fi
fi
