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
.. autoclass:: nodepool.driver.NodeRequestHandler
   :members:
.. autoclass:: nodepool.driver.ProviderConfig
   :members:
