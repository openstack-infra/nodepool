.. _scripts:

Node Prep Scritps
=================

Nodepool requires the specification of a script directory
(`script-dir`) in its configuration.  When Nodepool starts a virtual
machine for the purpose of creating a snapshot image, all of the files
within this directory will be copied to the virtual machine so they
are available for use by the setup script.
