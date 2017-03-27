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

The following sections are available.  All are required unless
otherwise indicated.

.. _elements-dir:

elements-dir
------------

If an image is configured to use diskimage-builder and glance to locally
create and upload images, then a collection of diskimage-builder elements
must be present. The ``elements-dir`` parameter indicates a directory
that holds one or more elements.

Example::

  elements-dir: /path/to/elements/dir

images-dir
----------

When we generate images using diskimage-builder they need to be
written to somewhere. The ``images-dir`` parameter is the place to
write them.

Example::

  images-dir: /path/to/images/dir

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
      min-ready: 2
    - name: multi-precise
      min-ready: 2

**required**

  ``name``
    Unique name used to tie jobs to those instances.

**optional**

  ``min-ready`` (default: 2)
    Minimum instances that should be in a ready state. Set to -1 to have the
    label considered disabled. ``min-ready`` is best-effort based on available
    capacity and is not a guaranteed allocation.

.. _diskimages:

diskimages
----------

This section lists the images to be built using diskimage-builder. The
name of the diskimage is mapped to the :ref:`images` section of the
provider, to determine which providers should received uploads of each
image.  The diskimage will be built in every format required by the
providers with which it is associated.  Because Nodepool needs to know
which formats to build, if the diskimage will only be built if it
appears in at least one provider.

To remove a diskimage from the system entirely, remove all associated
entries in :ref:`images` and remove its entry from `diskimages`.  All
uploads will be deleted as well as the files on disk.

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
      env-vars:
        TMPDIR: /opt/dib_tmp
        DIB_CHECKSUM: '1'
        DIB_IMAGE_CACHE: /opt/dib_cache
        DIB_APT_LOCAL_CACHE: '0'
        DIB_DISABLE_APT_CLEANUP: '1'
        FS_TYPE: ext3


**required**

  ``name``
    Identifier to reference the disk image in :ref:`images` and :ref:`labels`.

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

.. _provider:

provider
---------

Lists the OpenStack cloud providers Nodepool should use.  Within each
provider the available Nodepool image types are defined (see
:ref:`provider_diskimages`.

A provider's resources are partitioned into groups called "pools" (see
:ref:`pools` for details), and within a pool, the node types which are
to be made available are listed (see :ref:`pool_labels` for
details).

Example::

  providers:
    - name: provider1
      cloud: example
      region-name: 'region1'
      rate: 1.0
      boot-timeout: 120
      launch-timeout: 900
      launch-retries: 3
      image-name-format: 'template-{image_name}-{timestamp}'
      hostname-format: '{label.name}-{provider.name}-{node.id}'
      ipv6-preferred: False
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
    - name: provider2
      region-name: 'region1'
      rate: 1.0
      image-name-format: 'template-{image_name}-{timestamp}'
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

**cloud configuration***

**preferred**

  ``cloud``
  There are two methods supported for configuring cloud entries. The preferred
  method is to create an ``~/.config/openstack/clouds.yaml`` file containing
  your cloud configuration information. Then, use ``cloud`` to refer to a
  named entry in that file.

  More information about the contents of `clouds.yaml` can be found in
  `the os-client-config documentation <http://docs.openstack.org/developer/os-client-config/>`_.

**required**

  ``name``

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
    Default ``template-{image_name}-{timestamp}``

  ``rate``
    In seconds. Default 1.0.

  ``clean-floating-ips``
    If it is set to True, nodepool will assume it is the only user of the
    OpenStack project and will attempt to clean unattached floating ips that
    may have leaked around restarts.

  ``max-concurrency``
    Maximum number of node requests that this provider is allowed to handle
    concurrently. The default, if not specified, is to have no maximum. Since
    each node request is handled by a separate thread, this can be useful for
    limiting the number of threads used by the nodepoold daemon.

.. _pools:

pools
~~~~~

A pool defines a group of resources from a provider.  Each pool has a
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

  ``name``

  ``max-servers``
    Maximum number of servers spawnable from this pool.

**optional**

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

**required**

  ``name``
    Identifier to refer this image from :ref:`labels` and :ref:`diskimages`
    sections.

**optional**

  ``pause`` (bool)
    When set to True, nodepool-builder will not upload the image to the
    provider.

  ``config-drive`` (boolean)
    Whether config drive should be used for the image.

  ``meta`` (dict)
    Arbitrary key/value metadata to store for this server using the Nova
    metadata service. A maximum of five entries is allowed, and both keys and
    values must be 255 characters or less.


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
      name-filter: 'something to match'

**required**

  ``name``
    Identifier to refer this image from :ref:`labels` and :ref:`diskimages`
    sections.

  ``diskimage``
    Refers to provider's diskimages, see :ref:`images`.

  ``min-ram``
    Determine the flavor to use (e.g. ``m1.medium``, ``m1.large``,
    etc).  The smallest flavor that meets the ``min-ram`` requirements
    will be chosen. To further filter by flavor name, see optional
    ``name-filter`` below.

**optional**

  ``name-filter``
    Additional filter complementing ``min-ram``, will be required to match on
    the flavor-name (e.g. Rackspace offer a "Performance" flavour; setting
    `name-filter` to ``Performance`` will ensure the chosen flavor also
    contains this string as well as meeting `min-ram` requirements).
