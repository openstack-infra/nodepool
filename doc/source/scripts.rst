.. _scripts:

Node Prep Scripts
=================

Nodepool requires the specification of a script directory
(`script-dir`) in its configuration.  When Nodepool starts a virtual
machine for the purpose of creating a snapshot image, all of the files
within this directory will be copied to the virtual machine so they
are available for use by the setup script.

At various points in the image and node creation processes, these
scripts may be invoked by nodepool.  See :ref:`configuration` for
details.

Any environment variables present in the nodepool daemon environment
that begin with ``NODEPOOL_`` will be set in the calling environment
for the script.  This is useful during testing to alter script
behavior, for instance, to add a local ssh key that would not
otherwise be set in production.
