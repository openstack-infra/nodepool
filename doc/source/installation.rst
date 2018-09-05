:title: Installation

Installation
============

Nodepool consists of a long-running daemon which uses ZooKeeper
for coordination with Zuul.

External Requirements
---------------------

ZooKeeper
~~~~~~~~~

Nodepool uses ZooKeeper to coordinate image builds with its separate
image builder component.  A single ZooKeeper instance running on the
Nodepool server is fine.  Larger installations may wish to use a
multi-node ZooKeeper installation, in which case three nodes are
usually recommended.

Nodepool only needs to be told how to contact the ZooKeeper cluster;
it will automatically populate the ZNode structure as needed.

.. _statsd_configuration:

Statsd and Graphite
~~~~~~~~~~~~~~~~~~~

If you have a Graphite system with ``statsd``, Nodepool can be
configured to send information to it.  Set the environment variable
``STATSD_HOST`` to the ``statsd`` hostname (and optionally
``STATSD_PORT`` if this should be different to the default ``8125``)
for the Nodepool daemon to enable this support.

Install Nodepool
----------------

Install Nodepool prerequisites.

Nodepool requires Python 3.5 or newer.

RHEL 7 / CentOS 7::

  yum install libffi libffi-devel @development python python-devel

You may install Nodepool directly from PyPI with pip::

  pip install nodepool

Or install directly from a git checkout with::

  pip install .

Configuration
-------------

Nodepool has one required configuration file, which defaults to
``/etc/nodepool/nodepool.yaml``. This can be changed with the ``-c`` option.
The Nodepool configuration file is described in :ref:`configuration`.

There is support for a secure file that is used to store nodepool
configurations that contain sensitive data. It currently only supports
specifying ZooKeeper credentials and diskimage env-vars.
If ZooKeeper credentials or diskimage env-vars are defined in both
configuration files, the data in the secure file takes precedence.
The secure file location can be changed with the ``-s`` option and follows
the same file format as the Nodepool configuration file.

.. warning::

   Secrets stored in diskimage env-vars may be leaked by the elements
   or in the image build logs. Before using sensitive information in
   env-vars, please carefully audit the elements that are enabled and
   ensure they are handling the environment safely.

There is an optional logging configuration file, specified with the ``-l``
option. The logging configuration file can accept either:

* the traditional ini python logging `configuration file format
  <https://docs.python.org/2/library/logging.config.html#configuration-file-format>`_.

* a `.yml` or `.yaml` suffixed file that will be parsed and loaded as the newer
  `dictConfig format
  <https://docs.python.org/2/library/logging.config.html#configuration-dictionary-schema>`_.

The Nodepool configuration file is described in :ref:`configuration`.
