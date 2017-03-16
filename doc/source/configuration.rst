.. _configuration:

Configuration
=============

Nodepool reads its secure configuration from ``/etc/nodepool/secure.conf``
by default. The secure file is a standard ini config file. Note that this
file is currently unused, but may be in the future.

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

cron
----
This section is optional.

Nodepool runs several periodic tasks.  The ``cleanup`` task deletes
old images and servers which may have encountered errors during their
initial deletion.  The ``check`` task attempts to log into each node
that is waiting to be used to make sure that it is still operational.
The following illustrates how to change the schedule for these tasks
and also indicates their default values::

  cron:
    cleanup: '27 */6 * * *'
    check: '*/15 * * * *'

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

Defines the types of nodes that should be created.  Maps node types to
the images that are used to back them and the providers that are used
to supply them.  Jobs should be written to run on nodes of a certain
label. Example::

  labels:
    - name: my-precise
      image: precise
      min-ready: 2
      providers:
        - name: provider1
        - name: provider2
    - name: multi-precise
      image: precise
      min-ready: 2
      providers:
        - name: provider1

**required**

  ``name``
    Unique name used to tie jobs to those instances.

  ``image``
    Refers to providers images, see :ref:`images`.

  ``providers`` (list)
    Required if any nodes should actually be created (e.g., the label is not
    currently disabled, see ``min-ready`` below).

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
provider, the Nodepool image types are also defined (see
:ref:`images` for details).  Example::

  providers:
    - name: provider1
      cloud: example
      region-name: 'region1'
      max-servers: 96
      rate: 1.0
      availability-zones:
        - az1
      boot-timeout: 120
      launch-timeout: 900
      launch-retries: 3
      image-name-format: 'template-{image_name}-{timestamp}'
      hostname-format: '{label.name}-{provider.name}-{node.id}'
      ipv6-preferred: False
      networks:
        - name: 'some-network-name'
      images:
        - name: trusty
          min-ram: 8192
          name-filter: 'something to match'
          meta:
              key: value
              key2: value
        - name: precise
          min-ram: 8192
        - name: devstack-trusty
          min-ram: 30720
    - name: provider2
      username: 'username'
      password: 'password'
      auth-url: 'http://auth.provider2.example.com/'
      project-name: 'project'
      service-type: 'compute'
      service-name: 'compute'
      region-name: 'region1'
      max-servers: 96
      rate: 1.0
      image-name-format: 'template-{image_name}-{timestamp}'
      hostname-format: '{label.name}-{provider.name}-{node.id}'
      images:
        - name: precise
          min-ram: 8192
          meta:
              key: value
              key2: value

**cloud configuration***

**preferred**

  ``cloud``
  There are two methods supported for configuring cloud entries. The preferred
  method is to create an ``~/.config/openstack/clouds.yaml`` file containing
  your cloud configuration information. Then, use ``cloud`` to refer to a
  named entry in that file.

  More information about the contents of `clouds.yaml` can be found in
  `the os-client-config documentation <http://docs.openstack.org/developer/os-client-config/>`_.

**compatablity**

  For backwards compatibility reasons, you can also include
  portions of the cloud configuration directly in ``nodepool.yaml``. Not all
  of the options settable via ``clouds.yaml`` are available.

  ``username``

  ``password``

  ``project-id`` OR ``project-name``
    Some clouds may refer to the ``project-id`` as ``tenant-id``.
    Some clouds may refer to the ``project-name`` as ``tenant-name``.

  ``auth-url``
    Keystone URL.

  ``image-type``
    Specifies the image type supported by this provider.  The disk images built
    by diskimage-builder will output an image for each ``image-type`` specified
    by a provider using that particular diskimage.

    By default, ``image-type`` is set to the value returned from
    ``os-client-config`` and can be omitted in most cases.

**required**

  ``name``

  ``max-servers``
    Maximum number of servers spawnable on this provider.

**optional**

  ``availability-zones`` (list)
    Without it nodepool will rely on nova to schedule an availability zone.

    If it is provided the value should be a list of availability zone names.
    Nodepool will select one at random and provide that to nova. This should
    give a good distribution of availability zones being used. If you need more
    control of the distribution you can use multiple logical providers each
    providing a different list of availabiltiy zones.

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

  ``keypair``
    Default None

  ``networks`` (dict)
    Specify custom Neutron networks that get attached to each
    node. Specify the ``name`` of the network (a string).

  ``ipv6-preferred``
    If it is set to True, nodepool will try to find ipv6 in public net first
    as the ip address for the ssh connection. If ipv6 is not found or the key
    is not specified or set to False, ipv4 address will be used.

  ``api-timeout`` (compatability)
    Timeout for the OpenStack API calls client in seconds. Prefer setting
    this in `clouds.yaml`

  ``service-type`` (compatability)
    Prefer setting this in `clouds.yaml`.

  ``service-name`` (compatability)
    Prefer setting this in `clouds.yaml`.

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

.. _images:

images
~~~~~~

Each entry in a provider's `images` section must correspond to an
entry in :ref:`diskimages`.  Such an entry indicates that the
corresponding diskimage should be uploaded for use in this provider.
Additionally, any nodes that are created using the uploaded image will
have the associated attributes (such as flavor or metadata).

If an image is removed from this section, any previously uploaded
images will be deleted from the provider.

Example configuration::

  images:
    - name: precise
      pause: False
      min-ram: 8192
      name-filter: 'something to match'
      meta:
          key: value
          key2: value

**required**

  ``name``
    Identifier to refer this image from :ref:`labels` and :ref:`diskimages`
    sections.

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

  ``pause`` (bool)
    When set to True, nodepool-builder will not upload the image to the
    provider.

  ``config-drive`` (boolean)
    Whether config drive should be used for the image.

  ``meta`` (dict)
    Arbitrary key/value metadata to store for this server using the Nova
    metadata service. A maximum of five entries is allowed, and both keys and
    values must be 255 characters or less.
