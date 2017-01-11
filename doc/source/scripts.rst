.. _scripts:

Node Ready Scripts
==================

Each label can specify a ready script with `ready-script`.  This script can be
used to perform any last minute changes to a node after it has been launched
but before it is put in the READY state to receive jobs.  In particular, it
can read the files in /etc/nodepool to perform multi-node related setup.

Those files include:

**/etc/nodepool/role**
  Either the string ``primary`` or ``sub`` indicating whether this
  node is the primary (the node added to the target and which will run
  the job), or a sub-node.
**/etc/nodepool/node**
  The IP address of this node.
**/etc/nodepool/node_private**
  The private IP address of this node.
**/etc/nodepool/primary_node**
  The IP address of the primary node, usable for external access.
**/etc/nodepool/primary_node_private**
  The Private IP address of the primary node, for internal communication.
**/etc/nodepool/sub_nodes**
  The IP addresses of the sub nodes, one on each line,
  usable for external access.
**/etc/nodepool/sub_nodes_private**
  The Private IP addresses of the sub nodes, one on each line.
**/etc/nodepool/id_rsa**
  An OpenSSH private key generated specifically for this node group.
**/etc/nodepool/id_rsa.pub**
  The corresponding public key.
**/etc/nodepool/provider**
  Information about the provider in a shell-usable form.  This
  includes the following information:

  **NODEPOOL_PROVIDER**
    The name of the provider
  **NODEPOOL_CLOUD**
    The name of the cloud
  **NODEPOOL_REGION**
    The name of the region
  **NODEPOOL_AZ**
    The name of the availability zone (if available)
