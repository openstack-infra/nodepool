.. _devguide:

Developer's Guide
=================

The following guide is intended for those interested in the inner workings
of nodepool and its various processes.

Operation
---------

If you send a SIGUSR2 to one of the daemon processes, Nodepool will
dump a stack trace for each running thread into its debug log.  It is
written under the log bucket ``nodepool.stack_dump``.  This is useful
for tracking down deadlock or otherwise slow threads.

Nodepool Builder
----------------

The following is the overall diagram for the `nodepool-builder` process and
its most important pieces::

                          +-----------------+
                          |    ZooKeeper    |
                          +-----------------+
                            ^      |
                     bld    |      | watch
     +------------+  req    |      | trigger
     |   client   +---------+      |           +--------------------+
     +------------+                |           | NodepoolBuilderApp |
                                   |           +---+----------------+
                                   |               |
                                   |               | start/stop
                                   |               |
                           +-------v-------+       |
                           |               <-------+
                 +--------->   NodePool-   <----------+
                 |     +---+   Builder     +---+      |
                 |     |   |               |   |      |
                 |     |   +---------------+   |      |
                 |     |                       |      |
           done  |     | start           start |      | done
                 |     | bld             upld  |      |
                 |     |                       |      |
                 |     |                       |      |
             +---------v---+               +---v----------+
             | BuildWorker |               | UploadWorker |
             +-+-------------+             +-+--------------+
               | BuildWorker |               | UploadWorker |
               +-+-------------+             +-+--------------+
                 | BuildWorker |               | UploadWorker |
                 +-------------+               +--------------+

Drivers
-------

.. autoclass:: nodepool.driver.Driver
   :members:
.. autoclass:: nodepool.driver.Provider
   :members:
.. autoclass:: nodepool.driver.ProviderNotifications
   :members:
.. autoclass:: nodepool.driver.NodeRequestHandler
   :members:
.. autoclass:: nodepool.driver.NodeRequestHandlerNotifications
   :members:
.. autoclass:: nodepool.driver.ProviderConfig
   :members:


Writing A New Provider Driver
-----------------------------

Nodepool drivers are loaded from the nodepool/drivers directory. A driver
is composed of three main objects:

- A ProviderConfig to manage validation and loading of the provider.
- A Provider to manage resource allocations.
- A NodeRequestHandler to manage nodeset (collection of resource) allocations.

Those objects are referenced from the Driver main interface that needs to
be implemented in the __init__.py file of the driver directory.


ProviderConfig
~~~~~~~~~~~~~~

The ProviderConfig is constructed with the driver object and the provider
configuration dictionary.

The main procedures of the ProviderConfig are:

- getSchema() exposes a voluptuous schema of the provider configuration.
- load(config) parses the provider configuration. Note that the config
  argument is the global Nodepool.yaml configuration. Each provided labels
  need to be referenced back to the global config.labels dictionary so
  that the launcher service know which provider provide which labels.


Provider
~~~~~~~~

The Provider is constructed with the ProviderConfig.

The main procedures of the Provider are:

- cleanupNode(external_id) terminates a resource

- listNodes() returns the list of existing resources. This procedure needs to
  map the nodepool_node_id with each resource. If the provider doesn't support
  resource metadata, the driver needs to implement a storage facility to
  associate resource created by Nodepool with the internal nodepool_node_id.
  The launcher periodically look for non-existent node_id in listNodes() to
  delete any leaked resources.

- getRequestHandler(pool, request) returns a NodeRequestHandler object to manage
  the creation of resources. The contract between the handler and the provider is
  free form. As a rule of thumb, the handler should be in charge of interfacing
  with Nodepool's database while the provider should provides primitive to create
  resources. For example the Provider is likely to implement a
  createResource(pool, label) procedure that will be used by the handler.


NodeRequestHandler
~~~~~~~~~~~~~~~~~~

The NodeRequestHandler is constructed with the assigned pool and the request object.
Before the handler is used, the following attributes are set:

* self.provider : the provider configuration.
* self.pool : the pool configuration.
* self.zk : the database client.
* self.manager : the Provider object.

The main procedures of the NodeRequestHandler are:

- launch(node) starts the creation of a new resource.
- launchesComplete() returns True if all the node of the nodesets self
  attributes are READY.

An Handler may not have to launch each node of the nodesets as Nodepool will
re-use existing nodes.

The launch procedure usually consists of the following operations:

- Use the provider to create the resources associated with the node label.
  Once an external_id is obtained, it should be stored to the node.external_id.
- Once the resource is created, READY should be stored to the node.state.
  Otherwise raise an exception to restart the launch attempt.
