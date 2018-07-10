Nodepool
========

Nodepool is a system for managing test node resources. It supports launching
single-use test nodes from cloud providers as well as managing access to
pre-defined pre-existing nodes. Nodepool is part of a suite of tools that form
a comprehensive test system, including Zuul.

The latest documentation for Nodepool is published at:
https://zuul-ci.org/docs/nodepool/

The latest documentation for Zuul is published at:
https://zuul-ci.org/docs/zuul/

Getting Help
------------

There are two Zuul-related mailing lists:

`zuul-announce <http://lists.zuul-ci.org/cgi-bin/mailman/listinfo/zuul-announce>`_
  A low-traffic announcement-only list to which every Zuul operator or
  power-user should subscribe.

`zuul-discuss <http://lists.zuul-ci.org/cgi-bin/mailman/listinfo/zuul-discuss>`_
  General discussion about Zuul, including questions about how to use
  it, and future development.

You will also find Zuul developers in the `#zuul` channel on Freenode
IRC.

Contributing
------------

To browse the latest code, see: https://git.zuul-ci.org/cgit/nodepool/tree/
To clone the latest code, use `git clone https://git.zuul-ci.org/nodepool`

Bugs are handled at: https://storyboard.openstack.org/#!/project/679

Code reviews are handled by gerrit at https://review.openstack.org

After creating a Gerrit account, use `git review` to submit patches.
Example::

    # Do your commits
    $ git review
    # Enter your username if prompted

Join `#zuul` on Freenode to discuss development or usage.

License
-------

Nodepool is free software, licensed under the Apache License, version 2.0.

Python Version Support
----------------------

Nodepool requires Python 3. It does not support Python 2.
