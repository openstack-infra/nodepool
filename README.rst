Nodepool
========

Nodepool is a service used by the OpenStack CI team to deploy and manage a pool
of devstack images on a cloud server for use in OpenStack project testing.

Developer setup
===============

Install dependencies:

.. code-block:: bash

    sudo apt-get update
    sudo apt-get -qy install git mysql-server libmysqlclient-dev g++\
                     python-dev python-pip libffi-dev libssl-dev qemu-utils\
                     libxml2-dev libxslt1-dev python-lxml
    mkdir src
    cd ~/src
    git clone git://git.openstack.org/openstack-infra/system-config
    git clone git://git.openstack.org/openstack-infra/nodepool
    cd nodepool
    sudo pip install -U -r requirements.txt
    sudo pip install -e .

If you're testing a specific patch that is already in gerrit, you will also
want to install git-review and apply that patch while in the nodepool
directory, ie:

.. code-block:: bash

    git review -x XXXXX


Create or adapt a nodepool yaml file. You can adapt an infra/system-config one, or
fake.yaml as desired. Note that fake.yaml's settings won't Just Work - consult
./modules/openstack_project/templates/nodepool/nodepool.yaml.erb in the
infra/system-config tree to see a production config.

If the cloud being used has no default_floating_pool defined in nova.conf,
you will need to define a pool name using the nodepool yaml file to use
floating ips.

Set up database for interactive testing:

.. code-block:: bash

    mysql -u root

    mysql> create database nodepool;
    mysql> GRANT ALL ON nodepool.* TO 'nodepool'@'localhost';
    mysql> flush privileges;

Set up database for unit tests:

.. code-block:: bash

    mysql -u root
    mysql> grant all privileges on *.* to 'openstack_citest'@'localhost' identified by 'openstack_citest' with grant option;
    mysql> flush privileges;
    mysql> create database openstack_citest;

Export variable for your ssh key so you can log into the created instances:

.. code-block:: bash

    export NODEPOOL_SSH_KEY=`cat ~/.ssh/id_rsa.pub | awk '{print $2}'`

Start nodepool with a demo config file (copy or edit fake.yaml
to contain your data):

.. code-block:: bash

    export STATSD_HOST=127.0.0.1
    export STATSD_PORT=8125
    nodepoold -d -c tools/fake.yaml

All logging ends up in stdout.

Use the following tool to check on progress:

.. code-block:: bash

    nodepool image-list

After each run (the fake nova provider is only in-memory):

.. code-block:: bash

    mysql> delete from snapshot_image; delete from node;
