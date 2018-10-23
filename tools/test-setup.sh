#!/bin/bash -xe

# This script will be run by OpenStack CI before unit tests are run,
# it sets up the test system as needed.
# Developers should setup their test systems in a similar way.

# This setup needs to be run as a user that can run sudo.

# Config Zookeeper to run on tmpfs
sudo service zookeeper stop
DATADIR=$(sed -n -e 's/^dataDir=//p' /etc/zookeeper/conf/zoo.cfg)
sudo mount -t tmpfs -o nodev,nosuid,size=500M none $DATADIR
sudo service zookeeper start
