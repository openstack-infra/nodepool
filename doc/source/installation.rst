:title: Installation

Installation
============

Nodepool consists of a long-running daemon which uses an SQL database
and communicates with Jenkins using ZeroMQ.

External Requirements
---------------------

Jenkins
~~~~~~~

You should have a Jenkins server running with the `ZMQ Event Publisher
<http://git.openstack.org/cgit/openstack-infra/zmq-event-publisher/tree/README>`_
plugin installed (it is available in the Jenkins Update Center).  Be
sure that the machine where you plan to run Nodepool can connect to
the ZMQ port specified by the plugin on your Jenkins master(s).

Zuul
~~~~

If you plan to use Nodepool with Zuul (it is optional), you should
ensure that Nodepool can connect to the gearman port on your Zuul
server (TCP 4730 by default).  This will allow Nodepool to respond to
current Zuul demand.  If you elect not to connect Nodepool to Zuul, it
will still operate in a node-replacement mode.

Database
~~~~~~~~

Nodepool requires an SQL server.  MySQL with the InnoDB storage engine
is tested and recommended.  PostgreSQL should work fine.  Due to the
high number of concurrent connections from Nodepool, SQLite is not
recommended.  When adding or deleting nodes, Nodepool will hold open a
database connection for each node.  Be sure to configure the database
server to support at least a number of connections equal to twice the
number of nodes you expect to be in use at once.

All that is necessary is that the database is created. Nodepool will
handle the schema by itself when it is run.

MySQL Example::

  CREATE USER 'nodepool'@'localhost' IDENTIFIED BY '<password>';
  CREATE DATABASE nodepooldb;
  GRANT ALL ON nodepooldb.* TO 'nodepool'@'localhost';

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

Nodepool requires Python 2.7 or newer.

RHEL 7 / CentOS 7::

  yum install libffi libffi-devel @development python python-devel

You may install Nodepool directly from PyPI with pip::

  pip install nodepool

Or install directly from a git checkout with::

  pip install .

Note that some distributions provide a libzmq1 which does not support
RCVTIMEO.  Removing this libzmq1 from the system libraries will ensure
pip compiles a libzmq1 with appropriate options for the version of
pyzmq used by nodepool.

Configuration
-------------

Nodepool has two required configuration files: secure.conf and
nodepool.yaml, and an optional logging configuration file logging.conf.
The secure.conf file is used to store nodepool configurations that contain
sensitive data, such as the Nodepool database password and Jenkins
api key. The nodepool.yaml files is used to store all other
configurations.

The logging configuration file is in the standard python logging
`configuration file format
<http://docs.python.org/2/library/logging.config.html#configuration-file-format>`_.
The Nodepool configuration file is described in :ref:`configuration`.
