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
      providers:
        - name: provider1
        - name: provider2
    - name: multi-precise
      image: precise
      subnodes: 2
      min-ready: 2
      ready-script: setup_multinode.sh
      providers:
        - name: provider1

The `name`, `image`, and `min-ready` keys are required.  The
`providers` list is also required if any nodes should actually be
created (e.g., the label is not currently disabled).

The `subnodes` key is used to configure multi-node support.  If a
`subnodes` key is supplied to an image, it indicates that the specified
number of additional nodes of the same image type should be created
and associated with each node for that image.  Only one node from each
such group will be added to the target, the subnodes are expected to
communicate directly with each other.  In the example above, for each
Precise node added to the target system, two additional nodes will be
created and associated with it.

The script specified by `ready-script` (which is expected to be in
`/opt/nodepool-scripts` along with the setup script) can be used to
perform any last minute changes to a node after it has been launched
but before it is put in the READY state to receive jobs.  In
particular, it can read the files in /etc/nodepool to perform
multi-node related setup.

Those files include:

**/etc/nodepool/role**
  Either the string ``primary`` or ``sub`` indicating whether this
  node is the primary (the node added to the target and which will run
  the job), or a sub-node.
**/etc/nodepool/primary_node**
  The IP address of the primary node.
**/etc/nodepool/sub_nodes**
  The IP addresses of the sub nodes, one on each line.
**/etc/nodepool/id_rsa**
  An OpenSSH private key generated specifically for this node group.
**/etc/nodepool/id_rsa.pub**
  The corresponding public key.

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
      boot-timeout: 120
      launch-timeout: 900
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
`setup`.

Both `boot-timeout` and `launch-timeout` keys are optional.  The
`boot-timeout` key defaults to 60 seconds and `launch-timeout` key
will default to 3600 seconds.

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
