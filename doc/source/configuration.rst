.. _configuration:

Configuration
=============

Nodepool reads its configuration from ``/etc/nodepool/nodepool.yaml``
by default.  The configuration file follows the standard YAML syntax
with a number of sections defined with top level keys.  For example, a
full configuration file may have the ``diskimages``, ``labels``,
and ``providers`` sections::

  diskimages:
    ...
  labels:
    ...
  providers:
    ...

.. note:: The builder daemon creates a UUID to uniquely identify itself and
          to mark image builds in ZooKeeper that it owns. This file will be
          named ``builder_id.txt`` and will live in the directory named by the
          :ref:`images-dir` option. If this file does not exist, it will be
          created on builder startup and a UUID will be created automatically.

The following sections are available.  All are required unless
otherwise indicated.

.. _webapp-conf:

webapp
------

Define the webapp endpoint port and listen address.

Example::

  webapp:
    port: 8005
    listen_address: '0.0.0.0'

.. _elements-dir:

elements-dir
------------

If an image is configured to use diskimage-builder and glance to locally
create and upload images, then a collection of diskimage-builder elements
must be present. The ``elements-dir`` parameter indicates a directory
that holds one or more elements.

Example::

  elements-dir: /path/to/elements/dir

.. _images-dir:

images-dir
----------

When we generate images using diskimage-builder they need to be
written to somewhere. The ``images-dir`` parameter is the place to
write them.

Example::

  images-dir: /path/to/images/dir

.. _build-log-dir:

build-log-dir
-------------

The builder will store build logs in this directory.  It will create
one file for each build, named `<image>-<build-id>.log`; for example,
`fedora-0000000004.log`.  It defaults to ``/var/log/nodepool/builds``.

Example::

  build-log-dir: /path/to/log/dir

.. _build-log-retention:

build-log-retention
-------------------

At the start of each build, the builder will remove old build logs if
they exceed a certain number.  This option specifies how many will be
kept (usually you will see one more, as deletion happens before
starting a new build).  By default, the last 7 old build logs are
kept.

Example::

  build-log-retention: 14

zookeeper-servers
-----------------
Lists the ZooKeeper servers uses for coordinating information between
nodepool workers. Example::

  zookeeper-servers:
    - host: zk1.example.com
      port: 2181
      chroot: /nodepool

The ``port`` key is optional (default: 2181).

The ``chroot`` key, used for interpreting ZooKeeper paths relative to
the supplied root path, is also optional and has no default.

.. _labels:

labels
------

Defines the types of nodes that should be created.  Jobs should be
written to run on nodes of a certain label. Example::

  labels:
    - name: my-precise
      max-ready-age: 3600
      min-ready: 2
    - name: multi-precise
      min-ready: 2

**required**

  ``name``
    Unique name used to tie jobs to those instances.

**optional**

  ``max-ready-age`` (int)
    Maximum number of seconds the node shall be in ready state. If
    this is exceeded the node will be deleted. A value of 0 disables this.
    Defaults to 0.

  ``min-ready`` (default: 0)
    Minimum number of instances that should be in a ready
    state. Nodepool always creates more nodes as necessary in response
    to demand, but setting ``min-ready`` can speed processing by
    attempting to keep nodes on-hand and ready for immedate use.
    ``min-ready`` is best-effort based on available capacity and is
    not a guaranteed allocation.  The default of 0 means that nodepool
    will only create nodes of this label when there is demand.  Set
    to -1 to have the label considered disabled, so that no nodes will
    be created at all.

.. _maxholdage:

max-hold-age
------------

Maximum number of seconds a node shall be in "hold" state. If
this is exceeded the node will be deleted. A value of 0 disables this.
Defaults to 0.

This setting is applied to all nodes, regardless of label or provider.

.. _diskimages:

diskimages
----------

This section lists the images to be built using diskimage-builder. The
name of the diskimage is mapped to the :ref:`provider_diskimages` section
of the provider, to determine which providers should received uploads of each
image.  The diskimage will be built in every format required by the
providers with which it is associated.  Because Nodepool needs to know
which formats to build, if the diskimage will only be built if it
appears in at least one provider.

To remove a diskimage from the system entirely, remove all associated
entries in :ref:`provider_diskimages` and remove its entry from `diskimages`.
All uploads will be deleted as well as the files on disk.

Example configuration::

  diskimages:
    - name: ubuntu-precise
      pause: False
      rebuild-age: 86400
      elements:
        - ubuntu-minimal
        - vm
        - simple-init
        - openstack-repos
        - nodepool-base
        - cache-devstack
        - cache-bindep
        - growroot
        - infra-package-needs
      release: precise
      username: zuul
      env-vars:
        TMPDIR: /opt/dib_tmp
        DIB_CHECKSUM: '1'
        DIB_IMAGE_CACHE: /opt/dib_cache
        DIB_APT_LOCAL_CACHE: '0'
        DIB_DISABLE_APT_CLEANUP: '1'
        FS_TYPE: ext3
    - name: ubuntu-xenial
      pause: True
      rebuild-age: 86400
      formats:
        - raw
        - tar
      elements:
        - ubuntu-minimal
        - vm
        - simple-init
        - openstack-repos
        - nodepool-base
        - cache-devstack
        - cache-bindep
        - growroot
        - infra-package-needs
      release: precise
      username: ubuntu
      env-vars:
        TMPDIR: /opt/dib_tmp
        DIB_CHECKSUM: '1'
        DIB_IMAGE_CACHE: /opt/dib_cache
        DIB_APT_LOCAL_CACHE: '0'
        DIB_DISABLE_APT_CLEANUP: '1'
        FS_TYPE: ext3


**required**

  ``name``
    Identifier to reference the disk image in :ref:`provider_diskimages`
    and :ref:`labels`.

**optional**

  ``formats`` (list)
    The list of formats to build is normally automatically created based on the
    needs of the providers to which the image is uploaded.  To build images even
    when no providers are configured or to build additional formats which you
    know you may need in the future, list those formats here.

  ``rebuild-age``
    If the current diskimage is older than this value (in seconds),
    then nodepool will attempt to rebuild it.  Defaults to 86400 (24
    hours).

  ``release``
    Specifies the distro to be used as a base image to build the image using
    diskimage-builder.

  ``elements`` (list)
    Enumerates all the elements that will be included when building the image,
    and will point to the :ref:`elements-dir` path referenced in the same
    config file.

  ``env-vars`` (dict)
    Arbitrary environment variables that will be available in the spawned
    diskimage-builder child process.

  ``pause`` (bool)
    When set to True, nodepool-builder will not build the diskimage.

  ``username`` (string)
    The username that a consumer should use when connecting to the node. Defaults
    to ``zuul``.

.. _provider:

providers
---------

Lists the providers Nodepool should use. Each provider is associated to
a driver listed below.

**required**

  ``name``


**optional**

  ``driver``
    Default to *openstack*

  ``max-concurrency``
    Maximum number of node requests that this provider is allowed to handle
    concurrently. The default, if not specified, is to have no maximum. Since
    each node request is handled by a separate thread, this can be useful for
    limiting the number of threads used by the nodepool-launcher daemon.


OpenStack driver
^^^^^^^^^^^^^^^^

Within each OpenStack provider the available Nodepool image types are defined
(see :ref:`provider_diskimages`).

An OpenStack provider's resources are partitioned into groups called "pools"
(see :ref:`pools` for details), and within a pool, the node types which are
to be made available are listed (see :ref:`pool_labels` for
details).

Example::

  providers:
    - name: provider1
      driver: openstack
      cloud: example
      region-name: 'region1'
      rate: 1.0
      boot-timeout: 120
      launch-timeout: 900
      launch-retries: 3
      image-name-format: '{image_name}-{timestamp}'
      hostname-format: '{label.name}-{provider.name}-{node.id}'
      diskimages:
        - name: trusty
          meta:
              key: value
              key2: value
        - name: precise
        - name: devstack-trusty
      pools:
        - name: main
          max-servers: 96
          availability-zones:
            - az1
          networks:
            - some-network-name
          security-groups:
            - zuul-security-group
          labels:
            - name: trusty
              min-ram: 8192
              diskimage: trusty
              console-log: True
            - name: precise
              min-ram: 8192
              diskimage: precise
            - name: devstack-trusty
              min-ram: 8192
              diskimage: devstack-trusty
    - name: provider2
      driver: openstack
      cloud: example2
      region-name: 'region1'
      rate: 1.0
      image-name-format: '{image_name}-{timestamp}'
      hostname-format: '{label.name}-{provider.name}-{node.id}'
      diskimages:
        - name: precise
          meta:
              key: value
              key2: value
      pools:
        - name: main
          max-servers: 96
          labels:
            - name: trusty
              min-ram: 8192
              diskimage: trusty
            - name: precise
              min-ram: 8192
              diskimage: precise
            - name: devstack-trusty
              min-ram: 8192
              diskimage: devstack-trusty

**required**

  ``cloud``
  Name of a cloud configured in ``clouds.yaml``.

  The instances spawned by nodepool will inherit the default security group
  of the project specified in the cloud definition in `clouds.yaml` (if other
  values not specified). This means that when working with Zuul, for example,
  SSH traffic (TCP/22) must be allowed in the project's default security group
  for Zuul to be able to reach instances.

  More information about the contents of `clouds.yaml` can be found in
  `the os-client-config documentation <http://docs.openstack.org/developer/os-client-config/>`_.

**optional**

  ``boot-timeout``
    Once an instance is active, how long to try connecting to the
    image via SSH.  If the timeout is exceeded, the node launch is
    aborted and the instance deleted.

    In seconds. Default 60.

  ``launch-timeout``

    The time to wait from issuing the command to create a new instance
    until that instance is reported as "active".  If the timeout is
    exceeded, the node launch is aborted and the instance deleted.

    In seconds. Default 3600.

  ``nodepool-id`` (deprecated)

    A unique string to identify which nodepool instances is using a provider.
    This is useful if you want to configure production and development instances
    of nodepool but share the same provider.

    Default None

  ``launch-retries``

    The number of times to retry launching a server before considering the job
    failed.

    Default 3.

  ``region-name``

  ``hostname-format``
    Hostname template to use for the spawned instance.
    Default ``{label.name}-{provider.name}-{node.id}``

  ``image-name-format``
    Format for image names that are uploaded to providers.
    Default ``{image_name}-{timestamp}``

  ``rate``
    In seconds, amount to wait between operations on the provider.
    Defaults to ``1.0``.

  ``clean-floating-ips``
    If it is set to True, nodepool will assume it is the only user of the
    OpenStack project and will attempt to clean unattached floating ips that
    may have leaked around restarts.

.. _pools:

pools
~~~~~

A pool defines a group of resources from an OpenStack provider. Each pool has a
maximum number of nodes which can be launched from it, along with a
number of cloud-related attributes used when launching nodes.

Example::

  pools:
    - name: main
      max-servers: 96
      availability-zones:
        - az1
      networks:
        - some-network-name
      security-groups:
        - zuul-security-group
      auto-floating-ip: False
      host-key-checking: True
      labels:
        - name: trusty
          min-ram: 8192
          diskimage: trusty
          console-log: True
        - name: precise
          min-ram: 8192
          diskimage: precise
        - name: devstack-trusty
          min-ram: 8192
          diskimage: devstack-trusty

**required**

  ``name``


**optional**

  ``max-cores``
    Maximum number of cores usable from this pool. This can be used to limit
    usage of the tenant. If not defined nodepool can use all cores up to the
    quota of the tenant.

  ``max-servers``
    Maximum number of servers spawnable from this pool. This can be used to
    limit the number of servers. If not defined nodepool can create as many
    servers the tenant allows.

  ``max-ram``
    Maximum ram usable from this pool. This can be used to limit the amount of
    ram allocated by nodepool. If not defined nodepool can use as much ram as
    the tenant allows.

  ``ignore-provider-quota``
    Ignore the provider quota for this pool. Instead, only check against the
    configured max values for this pool and the current usage based on stored
    data. This may be useful in circumstances where the provider is incorrectly
    calculating quota.

  ``availability-zones`` (list)
    A list of availability zones to use.

    If this setting is omitted, nodepool will fetch the list of all
    availability zones from nova.  To restrict nodepool to a subset
    of availability zones, supply a list of availability zone names
    in this setting.

    Nodepool chooses an availability zone from the list at random
    when creating nodes but ensures that all nodes for a given
    request are placed in the same availability zone.

  ``networks`` (list)
    Specify custom Neutron networks that get attached to each
    node. Specify the name or id of the network as a string.

  ``security-groups`` (list)
    Specify custom Neutron security groups that get attached to each
    node. Specify the name or id of the security_group as a string.

  ``auto-floating-ip`` (bool)
    Specify custom behavior of allocating floating ip for each node.
    When set to False, nodepool-launcher will not apply floating ip
    for nodes. When zuul instances and nodes are deployed in the same
    internal private network, set the option to False to save floating ip
    for cloud provider. The default value is True.

  ``host-key-checking`` (bool)
    Specify custom behavior of validation of SSH host keys.  When set to False,
    nodepool-launcher will not ssh-keyscan nodes after they are booted. This
    might be needed if nodepool-launcher and the nodes it launches are on
    different networks.  The default value is True.

.. _provider_diskimages:

diskimages
~~~~~~~~~~

Each entry in a provider's `diskimages` section must correspond to an
entry in :ref:`diskimages`.  Such an entry indicates that the
corresponding diskimage should be uploaded for use in this provider.
Additionally, any nodes that are created using the uploaded image will
have the associated attributes (such as flavor or metadata).

If an image is removed from this section, any previously uploaded
images will be deleted from the provider.

Example configuration::

  diskimages:
    - name: precise
      pause: False
      meta:
          key: value
          key2: value
    - name: windows
      connection-type: winrm
      connection-port: 5986

**required**

  ``name``
    Identifier to refer this image from :ref:`labels` and :ref:`diskimages`
    sections.

**optional**

  ``pause`` (bool)
    When set to True, nodepool-builder will not upload the image to the
    provider.

  ``config-drive`` (boolean)
    Whether config drive should be used for the image. Defaults to unset which
    will use the cloud's default behavior.

  ``meta`` (dict)
    Arbitrary key/value metadata to store for this server using the Nova
    metadata service. A maximum of five entries is allowed, and both keys and
    values must be 255 characters or less.

  ``connection-type`` (string)
    The connection type that a consumer should use when connecting to the
    node. For most diskimages this is not necessary. However when creating
    Windows images this could be 'winrm' to enable access via ansible.

  ``connection-port`` (int)
    The port that a consumer should use when connecting to the
    node. For most diskimages this is not necessary. This defaults to 22 for
    ssh and 5986 for winrm.

.. _provider_cloud_images:

cloud-images
~~~~~~~~~~~~

Each cloud-image entry in :ref:`labels` refers to an entry in this section.
This is a way for modifying launch parameters of the nodes (currently only
config-drive).

Example configuration::

  cloud-images:
    - name: trusty-external
      config-drive: False
    - name: windows-external
      connection-type: winrm
      connection-port: 5986

**required**

  ``name``
    Identifier to refer this cloud-image from :ref:`labels` section.
    Since this name appears elsewhere in the nodepool configuration
    file, you may want to use your own descriptive name here and use
    one of ``image-id`` or ``image-name`` to specify the cloud image
    so that if the image name or id changes on the cloud, the impact
    to your Nodepool configuration will be minimal.  However, if
    neither of those attributes are provided, this is also assumed to
    be the image name or ID in the cloud.

**optional**

  ``config-drive`` (boolean)
    Whether config drive should be used for the cloud image. Defaults to
    unset which will use the cloud's default behavior.

  ``image-id`` (str)
    If this is provided, it is used to select the image from the cloud
    provider by ID, rather than name.  Mutually exclusive with ``image-name``.

  ``image-name`` (str)
    If this is provided, it is used to select the image from the cloud
    provider by this name or ID.  Mutually exclusive with ``image-id``.

  ``username`` (str)
    The username that a consumer should use when connecting to the node.

  ``connection-type`` (str)
    The connection type that a consumer should use when connecting to the
    node. For most diskimages this is not necessary. However when creating
    Windows images this could be 'winrm' to enable access via ansible.

  ``connection-port`` (int)
    The port that a consumer should use when connecting to the
    node. For most diskimages this is not necessary. This defaults to 22
    for ssh and 5986 for winrm.

.. _pool_labels:

labels
~~~~~~

Each entry in a pool`s `labels` section indicates that the
corresponding label is available for use in this pool.  When creating
nodes for a label, the flavor-related attributes in that label's
section will be used.

Example configuration::

  labels:
    - name: precise
      min-ram: 8192
      flavor-name: 'something to match'
      console-log: True

**required**

  ``name``
    Identifier to refer this image from :ref:`labels` and :ref:`diskimages`
    sections.

**one of**

  ``diskimage``
    Refers to provider's diskimages, see :ref:`provider_diskimages`.

  ``cloud-image``
    Refers to the name of an externally managed image in the cloud that already
    exists on the provider. The value of ``cloud-image`` should match the
    ``name`` of a previously configured entry from the ``cloud-images`` section
    of the provider. See :ref:`provider_cloud_images`.

**at least one of**

  ``flavor-name``
    Name or id of the flavor to use. If ``min-ram`` is omitted, it
    must be an exact match. If ``min-ram`` is given, ``flavor-name`` will
    be used to find flavor names that meet ``min-ram`` and also contain
    ``flavor-name``.

  ``min-ram``
    Determine the flavor to use (e.g. ``m1.medium``, ``m1.large``,
    etc).  The smallest flavor that meets the ``min-ram`` requirements
    will be chosen.

**optional**

  ``boot-from-volume`` (bool)
    If given, the label for use in this pool will create a volume from the
    image and boot the node from it.

    Default: False

  ``key-name``
    If given, is the name of a keypair that will be used when booting each
    server.

  ``console-log`` (default: False)
    On the failure of the ssh ready check, download the server console log to
    aid in debuging the problem.

  ``volume-size``
    When booting an image from volume, how big should the created volume be.

    In gigabytes. Default 50.


Static driver
^^^^^^^^^^^^^

The static provider driver is used to define static nodes. Nodes are also
partitioned into groups called "pools" (see :ref:`static_nodes` for details).

.. NOTE::

   Although you may define more than one pool, it is essentially useless to do
   so since a node's ``name`` must be unique across all pools.

Example::

  providers:
    - name: static-rack
      driver: static
      pools:
        - name: main
          nodes:
            - name: trusty.example.com
              labels: trusty-static
              host-key: fake-key
              timeout: 13
              connection-port: 22022
              username: zuul
              max-parallel-jobs: 1

.. _static_nodes:

static nodes
~~~~~~~~~~~~

Each entry in a pool's nodes section indicates a static node and it's
corresponding label.

**required**

  ``name``
  The hostname or ip address of the static node. This must be unique
  across all nodes defined within the configuration file.

  ``labels`` (list)
  The list of labels associated with the node.

**optional**

  ``username``
  The username nodepool will use to validate it can connect to the node.
  Default to *zuul*

  ``timeout``
  The timeout in second before the ssh ping is considered failed.
  Default to *5* seconds

  ``host-key``
  The ssh host key of the node.

  ``connection-type`` (string)
    The connection type that a consumer should use when connecting to the
    node. Should be set to either 'ssh' or 'winrm'. Defaults to 'ssh'.

  ``connection-port`` (int)
    The port that a consumer should use when connecting to the node.
    For most nodes this is not necessary. This defaults to 22 when
    ``connection-type`` is 'ssh' and 5986 when it is 'winrm'.

  ``max-parallel-jobs``
  The number of jobs that can run in parallel on this node, default to *1*.
