.. _configuration:

Configuration
=============

Nodepool reads its configuration from ``/etc/nodepool/nodepool.yaml``
by default.  The configuration file follows the standard YAML syntax
with a number of sections defined with top level keys.  For example, a
full configuration file may have the ``labels``, ``providers``, and
``targets`` sections::

  labels:
    ...
  providers:
    ...
  targets:
    ...

The following sections are available.  All are required unless
otherwise indicated.

script-dir
----------
When creating an image to use when launching new nodes, Nodepool will
run a script that is expected to prepare the machine before the
snapshot image is created.  The ``script-dir`` parameter indicates a
directory that holds all of the scripts needed to accomplish this.
Nodepool will copy the entire directory to the machine before invoking
the appropriate script for the image being created.

Example::

  script-dir: /path/to/script/dir

elements-dir
------------

If an image is configured to use disk-image-builder and glance to locally
create and upload images, then a collection of disk-image-builder elements
must be present. The ``elements-dir`` parameter indicates a directory
that holds one or more elements.

Example::

  elements-dir: /path/to/elements/dir

images-dir
----------

When we generate images using disk-image-builder they need to be
written to somewhere. The ``images-dir`` parameter is the place to
write them.

Example::

  images-dir: /path/to/images/dir

dburi
-----
Indicates the URI for the database connection.  See the `SQLAlchemy
documentation
<http://docs.sqlalchemy.org/en/latest/core/engines.html#database-urls>`_
for the syntax.  Example::

  dburi: 'mysql://nodepool@localhost/nodepool'

cron
----
This section is optional.

Nodepool runs several periodic tasks.  The ``image-update`` task
creates a new image for each of the defined images, typically used to
keep the data cached on the images up to date.  The ``cleanup`` task
deletes old images and servers which may have encountered errors
during their initial deletion.  The ``check`` task attempts to log
into each node that is waiting to be used to make sure that it is
still operational.  The following illustrates how to change the
schedule for these tasks and also indicates their default values::

  cron:
    image-update: '14 2 * * *'
    cleanup: '27 */6 * * *'
    check: '*/15 * * * *'

zmq-publishers
--------------
Lists the ZeroMQ endpoints for the Jenkins masters.  Nodepool uses
this to receive real-time notification that jobs are running on nodes
or are complete and nodes may be deleted.  Example::

  zmq-publishers:
    - tcp://jenkins1.example.com:8888
    - tcp://jenkins2.example.com:8888

gearman-servers
---------------
Lists the Zuul Gearman servers that should be consulted for real-time
demand.  Nodepool will use information from these servers to determine
if additional nodes should be created to satisfy current demand.
Example::

  gearman-servers:
    - host: zuul.example.com
      port: 4730

The ``port`` key is optional.

labels
------

Defines the types of nodes that should be created.  Maps node types to
the images that are used to back them and the providers that are used
to supply them.  Jobs should be written to run on nodes of a certain
label (so targets such as Jenkins don't need to know about what
providers or images are used to create them).  Example::

  labels:
    - name: my-precise
      image: precise
      min-ready: 2
      hostname: '{label.name}-{provider.name}-{node_id}.slave.openstack.org'
      subnode-hostname: '{label.name}-{provider.name}-{node_id}-{subnode_id}.slave.openstack.org'
      providers:
        - name: provider1
        - name: provider2
    - name: multi-precise
      image: precise
      subnodes: 2
      min-ready: 2
      hostname: '{label.name}-{provider.name}-{node_id}'
      subnode-hostname: '{label.name}-{provider.name}-{node_id}-{subnode_id}'
      ready-script: setup_multinode.sh
      providers:
        - name: provider1

The `name` and `image` keys are required.  The `providers` list is
also required if any nodes should actually be created (e.g., the
label is not currently disabled). The `min-ready` key is optional
and defaults to 2. If the value is -1 the label is considered
disabled.

The `subnodes` key is used to configure multi-node support.  If a
`subnodes` key is supplied to an image, it indicates that the specified
number of additional nodes of the same image type should be created
and associated with each node for that image.  Only one node from each
such group will be added to the target, the subnodes are expected to
communicate directly with each other.  In the example above, for each
Precise node added to the target system, two additional nodes will be
created and associated with it.

A script specified by `ready-script` can be used to perform any last minute
changes to a node after it has been launched but before it is put in the READY
state to receive jobs. For more information, see :ref:`scripts`.

providers
---------

Lists the OpenStack cloud providers Nodepool should use.  Within each
provider, the Nodepool image types are also defined.  If the resulting
images from different providers should be equivalent, give them the
same name.  Example::

  providers:
    - name: provider1
      username: 'username'
      password: 'password'
      auth-url: 'http://auth.provider1.example.com/'
      project-id: 'project'
      service-type: 'compute'
      service-name: 'compute'
      region-name: 'region1'
      max-servers: 96
      rate: 1.0
      availability-zones:
        - az1
      boot-timeout: 120
      launch-timeout: 900
      template-hostname: '{image.name}-{timestamp}.template.openstack.org'
      networks:
        - net-id: 'some-uuid'
        - net-label: 'some-network-name'
      images:
        - name: precise
          base-image: 'Precise'
          min-ram: 8192
          setup: prepare_node.sh
          reset: reset_node.sh
          username: jenkins
          private-key: /var/lib/jenkins/.ssh/id_rsa
        - name: quantal
          base-image: 'Quantal'
          min-ram: 8192
          setup: prepare_node.sh
          reset: reset_node.sh
          username: jenkins
          private-key: /var/lib/jenkins/.ssh/id_rsa
    - name: provider2
      username: 'username'
      password: 'password'
      auth-url: 'http://auth.provider2.example.com/'
      project-id: 'project'
      service-type: 'compute'
      service-name: 'compute'
      region-name: 'region1'
      max-servers: 96
      rate: 1.0
      template-hostname: '{image.name}-{timestamp}-nodepool-template'
      images:
        - name: precise
          base-image: 'Fake Precise'
          min-ram: 8192
          setup: prepare_node.sh
          reset: reset_node.sh
          username: jenkins
          private-key: /var/lib/jenkins/.ssh/id_rsa

For providers, the `name`, `username`, `password`, `auth-url`,
`project-id`, and `max-servers` keys are required.  For images, the
`name`, `base-image`, and `min-ram` keys are required.  The `username`
and `private-key` values default to the values indicated.  Nodepool
expects that user to exist after running the script indicated by
`setup`. See :ref:`scripts` for setup script details.

Both `boot-timeout` and `launch-timeout` keys are optional.  The
`boot-timeout` key defaults to 60 seconds and `launch-timeout` key
will default to 3600 seconds.

The optional `networks` section may be used to specify custom
Neutron networks that get attached to each node. You can specify
Neutron networks using either the `net-id` or `net-label`. If
only the `net-label` is specified the network UUID is automatically
queried via the Nova os-tenant-networks API extension (this requires
that the cloud provider has deployed this extension).

The `availability-zones` key is optional. Without it nodepool will rely
on nova to schedule an availability zone. If it is provided the value
should be a list of availability zone names. Nodepool will select one
at random and provide that to nova. This should give a good distribution
of availability zones being used. If you need more control of the
distribution you can use multiple logical providers each providing a
different list of availabiltiy zones.

targets
-------

Lists the Jenkins masters to which Nodepool should attach nodes after
they are created.  Nodes of each label will be evenly distributed
across all of the targets which are on-line::

  targets:
    - name: jenkins1
      jenkins:
        url: https://jenkins1.example.org/
        user: username
        apikey: key
        credentials-id: id
    - name: jenkins2
      jenkins:
        url: https://jenkins2.example.org/
        user: username
        apikey: key
        credentials-id: id

For targets, the `name` is required.  If using Jenkins, the `url`,
`user`, and `apikey` keys are required.  If the `credentials-id` key
is provided, Nodepool will configure the Jenkins slave to use the
Jenkins credential identified by that ID, otherwise it will use the
username and ssh keys configured in the image.
