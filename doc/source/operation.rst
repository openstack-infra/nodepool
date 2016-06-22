.. _operation:

Operation
=========

Nodepool generally runs as a daemon under the command ``nodepoold``.
Once started, it will frequently re-read the configuration file and
make any changes necessary (such as adding or removing a provider, or
altering image or quota configuration).  If any needed images are
missing, it will immediately begin trying to build those images.
Periodically (once a day by default but configurable in the ``cron:``
section of the config file) it will attempt to create new versions of
each image.

If a new image creation is successful, it will immediately start using
it when launching nodes (Nodepool always uses the most recent image in
the ``ready`` state).  Nodepool will delete images that are older than
8 hours if they are not the most recent or second most recent
``ready`` images.  In other words, Nodepool will always make sure that
in addition to the current image, it keeps the previous image around.
This way if you find that a newly created image is problematic, you
may simply delete it and Nodepool will revert to using the previous
image.

Daemon usage
------------

To start Nodepool daemon, run **nodepoold**:

.. program-output:: nodepoold --help
   :nostderr:

If you send a SIGINT to the nodepoold process, Nodepool will wait for
diskimages to finish building (if any) and disconnect all its internal
process.

If you send a SIGUSR2 to the nodepoold process, Nodepool  will dump a
stack trace for each running thread into its debug log. It is written
under the log bucket ``nodepool.stack_dump``.  This is useful for
tracking down deadlock or otherwise slow threads.

When `yappi <https://code.google.com/p/yappi/>`_ (Yet Another Python
Profiler) is available, additional functions' and threads' stats are
emitted as well. The first SIGUSR2 will enable yappi, on the second
SIGUSR2 it dumps the information collected, resets all yappi state and
stops profiling. This is to minimize the impact of yappi on a running
system.

Metadata
--------

When nodepool creates instances, it will assign the following nova
metadata:

  groups
    A json-encoded list containing the name of the image and the name
    of the provider.  This may be used by the Ansible OpenStack
    inventory plugin.

  nodepool
    A json-encoded dictionary with the following entries:

    image_name
      The name of the image as a string.

    provider_name
      The name of the provider as a string.

    node_id
      The nodepool id of the node as an integer.

Command Line Tools
==================

Usage
-----
The general options that apply to all subcommands are:

.. program-output:: nodepool --help
   :nostderr:

The following subcommands deal with nodepool images:

dib-image-list
^^^^^^^^^^^^^^
.. program-output:: nodepool dib-image-list --help
   :nostderr:

image-list
^^^^^^^^^^
.. program-output:: nodepool image-list --help
   :nostderr:

image-build
^^^^^^^^^^^
.. program-output:: nodepool image-build --help
   :nostderr:

image-update
^^^^^^^^^^^^
.. program-output:: nodepool image-update --help
   :nostderr:

image-upload
^^^^^^^^^^^^
.. program-output:: nodepool image-upload --help
   :nostderr:

dib-image-delete
^^^^^^^^^^^^^^^^
.. program-output:: nodepool dib-image-delete --help
   :nostderr:

image-delete
^^^^^^^^^^^^
.. program-output:: nodepool image-delete --help
   :nostderr:

The following subcommands deal with nodepool nodes:

list
^^^^
.. program-output:: nodepool list --help
   :nostderr:

hold
^^^^
.. program-output:: nodepool hold --help
   :nostderr:

delete
^^^^^^
.. program-output:: nodepool delete --help
   :nostderr:

If Nodepool's database gets out of sync with reality, the following
commands can help identify compute instances or images that are
unknown to Nodepool:

alien-list
^^^^^^^^^^
.. program-output:: nodepool alien-list --help
   :nostderr:

alien-image-list
^^^^^^^^^^^^^^^^
.. program-output:: nodepool alien-image-list --help
   :nostderr:

In the case that a job is randomly failing for an unknown cause, it
may be necessary to instruct nodepool to automatically hold a node on
which that job has failed.  To do so, use the the ``job-create``
command to specify the job name and how many failed nodes should be
held.  When debugging is complete, use ''job-delete'' to disable the
feature.

job-create
^^^^^^^^^^
.. program-output:: nodepool job-create --help
   :nostderr:

job-list
^^^^^^^^
.. program-output:: nodepool job-list --help
   :nostderr:

job-delete
^^^^^^^^^^
.. program-output:: nodepool job-delete --help
   :nostderr:

Removing a Provider
===================

To remove a provider set that providers max-servers to -1. This will
prevent nodepool from booting new nodes and building new images on that
provider. You can then let the nodes do their normal ready -> used ->
delete -> deleted lifecycle. Once all nodes are gone you can then
image-delete the remaining images and remove the config from nodepool
for that provider entirely (though leaving it in this state is effectively
the same and makes it easy to turn the provider back on).

If urgency is required you can delete the nodes directly instead of
waiting for them to go through their normal lifecycle but the effect is
the same.
