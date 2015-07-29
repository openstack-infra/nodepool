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

NODEPOOL_KEY=$HOME/.ssh/id_nodepool
NODEPOOL_PUBKEY=$HOME/.ssh/id_nodepool.pub

# Install nodepool code
function install_nodepool {
    # This function is currently blank because just installing a git
    # tree does not require any additional code
    setup_develop $DEST/nodepool
}

# requires some globals from devstack, which *might* not be stable api
# points. If things break, investigate changes in those globals first.

function nodepool_create_keypairs {
    if [[ ! -f $NODEPOOL_KEY ]]; then
        ssh-keygen -f $NODEPOOL_KEY -P ""
    fi
}

function nodepool_write_prepare {
    sudo mkdir -p $(dirname $NODEPOOL_CONFIG)/scripts
    local pub_key=$(cat $NODEPOOL_PUBKEY)

    cat > /tmp/prepare_node_ubuntu.sh <<EOF
#!/bin/bash -x
sudo adduser --disabled-password --gecos "" jenkins
sudo mkdir -p /home/jenkins/.ssh
cat > tmp_authorized_keys << INNEREOF
  $pub_key
INNEREOF
sudo mv tmp_authorized_keys /home/jenkins/.ssh/authorized_keys
sudo chmod 700 /home/jenkins/.ssh
sudo chmod 600 /home/jenkins/.ssh/authorized_keys
sudo chown -R jenkins:jenkins /home/jenkins
sleep 5
sync
EOF
    sudo mv /tmp/prepare_node_ubuntu.sh \
         $(dirname $NODEPOOL_CONFIG)/scripts/prepare_node_ubuntu.sh

    sudo chmod a+x $(dirname $NODEPOOL_CONFIG)/scripts/prepare_node_ubuntu.sh

}

function nodepool_write_config {
    sudo mkdir -p $(dirname $NODEPOOL_CONFIG)
    local dburi=$(database_connection_url nodepool)


    cat > /tmp/nodepool.yaml <<EOF
# You will need to make and populate these two paths as necessary,
# cloning nodepool does not do this. Further in this doc we have an
# example script for /path/to/nodepool/things/scripts.
script-dir: $(dirname $NODEPOOL_CONFIG)/scripts
elements-dir: $(dirname $NODEPOOL_CONFIG)/elements
# The mysql password here may be different depending on your
# devstack install, you should double check it (the devstack var
# is MYSQL_PASSWORD and if unset devstack should prompt you for
# the value).
dburi: '$dburi'

gearman-servers: []
zmq-publishers: []
# Need to have at least one target for node allocations, but
# this does not need to be a jenkins target.
targets:
  - name: dummy

cron:
  cleanup: '*/1 * * * *'
  check: '*/15 * * * *'
  image-update: '14 14 * * *'

# Devstack does not make an Ubuntu image by default. You can
# grab one from Ubuntu and upload it yourself. Note that we
# cannot use devstack's cirros default because cirros does not
# support sftp.
labels:
  - name: $NODEPOOL_IMAGE
    image: $NODEPOOL_IMAGE
    min-ready: 1
    providers:
      - name: devstack

providers:
  - name: devstack
    region-name: '$REGION_NAME'
    service-type: 'compute'
    username: 'demo'
    project-id: 'demo'
    password: '$ADMIN_PASSWORD'
    auth-url: '$KEYSTONE_AUTH_URI/v$IDENTITY_API_VERSION'
    api-timeout: 60
    # Long boot timeout to deal with potentially nested virt.
    boot-timeout: 600
    max-servers: 2
    rate: 0.25
    images:
      - name: $NODEPOOL_IMAGE
        base-image: '$NODEPOOL_IMAGE'
        min-ram: 1024
        # This script should setup the jenkins user to accept
        # the ssh key configured below. It goes in the script-dir
        # configured above and an example is below.
        setup: prepare_node_ubuntu.sh
        username: jenkins
        # Alter below to point to your local user private key
        private-key: $NODEPOOL_KEY
        config-drive: true
EOF

    sudo mv /tmp/nodepool.yaml $NODEPOOL_CONFIG
}

# Initialize database
# Create configs
# Setup custom flavor
function configure_nodepool {
    # build a dedicated keypair for nodepool to use with guests
    nodepool_create_keypairs

    # write the nodepool config
    nodepool_write_config

    # write the prepare node script
    nodepool_write_prepare

    # builds a fresh db
    recreate_database nodepool

}

function start_nodepool {
    # build a custom flavor that's more friendly to nodepool
    local available_flavors=$(nova flavor-list)
    if [[ ! ( $available_flavors =~ 'm1.nodepool' ) ]]; then
        nova flavor-create m1.nodepool 64 1024 0 1
    fi

    # build sec group rules to reach the nodes, we need to do this
    # this late because nova hasn't started until this phase.
    if [[ -z $(nova secgroup-list-rules default | grep 'tcp' | grep '65535') ]]; then
        nova --os-project-name demo --os-username demo \
             secgroup-add-rule default tcp 1 65535 0.0.0.0/0
        nova --os-project-name demo --os-username demo \
             secgroup-add-rule default udp 1 65535 0.0.0.0/0
    fi

    run_process nodepool "nodepoold -c /etc/nodepool/nodepool.yaml -d"
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
