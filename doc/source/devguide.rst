.. _devguide:

Developer's Guide
=================

The following guide is intended for those interested in the inner workings
of nodepool and its various processes.

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
                 +--------->     Build     <----------+
                 |     +---+   Scheduler   +---+      |
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

