Nodepool
========

Nodepool is a service used by the OpenStack CI team to deploy and manage a pool
of devstack images on a cloud server for use in OpenStack project testing.

Developer setup
===============

Make sure you have pip installed:

.. code-block:: bash

    wget https://bootstrap.pypa.io/get-pip.py
    sudo python get-pip.py

Install dependencies:

.. code-block:: bash

    sudo pip install bindep
    sudo apt-get install $(bindep -b nodepool)

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

Export variable for your ssh key so you can log into the created instances:

.. code-block:: bash

    export NODEPOOL_SSH_KEY=`cat ~/.ssh/id_rsa.pub | awk '{print $2}'`

Start nodepool with a demo config file (copy or edit fake.yaml
to contain your data):

.. code-block:: bash

    export STATSD_HOST=127.0.0.1
    export STATSD_PORT=8125
    nodepool-launcher -d -c tools/fake.yaml

All logging ends up in stdout.

Use the following tool to check on progress:

.. code-block:: bash

    nodepool image-list
