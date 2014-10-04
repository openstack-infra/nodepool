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

Command Line Tools
==================

Usage
-----
The general options that apply to all subcommands are:

.. program-output:: nodepool --help

The following subcommands deal with nodepool images:

dib-image-list
^^^^^^^^^^^^^^
.. program-output:: nodepool dib-image-list --help

image-list
^^^^^^^^^^
.. program-output:: nodepool image-list --help

image-build
^^^^^^^^^^^
.. program-output:: nodepool image-build --help

image-update
^^^^^^^^^^^^
.. program-output:: nodepool image-update --help

image-upload
^^^^^^^^^^^^
.. program-output:: nodepool image-upload --help

dib-image-delete
^^^^^^^^^^^^^^^^
.. program-output:: nodepool dib-image-delete --help

image-delete
^^^^^^^^^^^^
.. program-output:: nodepool image-delete --help


The following subcommands deal with nodepool nodes:

list
^^^^
.. program-output:: nodepool list --help

hold
^^^^
.. program-output:: nodepool hold --help

delete
^^^^^^
.. program-output:: nodepool delete --help

If Nodepool's database gets out of sync with reality, the following
commands can help identify compute instances or images that are
unknown to Nodepool:

alien-list
^^^^^^^^^^
.. program-output:: nodepool alien-list --help

alien-image-list
^^^^^^^^^^^^^^^^
.. program-output:: nodepool alien-image-list --help
