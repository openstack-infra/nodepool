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
<http://git.openstack.org/cgit/openstack-infra/zmq-event-publisher/tree/README>`
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

Statsd and Graphite
~~~~~~~~~~~~~~~~~~~

If you have a Graphite system with statsd, Nodepool can be configured
to send information to statsd.

Install Nodepool
----------------

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

Nodepool has a single required configuration file and an optional
logging configuration file.

The logging configuration file is in the standard python logging
`configuration file format
<http://docs.python.org/2/library/logging.config.html#configuration-file-format>`.
The Nodepool configuration file is described in :ref:`configuration`.
