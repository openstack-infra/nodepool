#!/usr/bin/env python

# Copyright (C) 2011-2013 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
#
# See the License for the specific language governing permissions and
# limitations under the License.

import time
import socket
import logging
from sshclient import SSHClient

import fakeprovider

log = logging.getLogger("nodepool.utils")


def iterate_timeout(max_seconds, purpose):
    start = time.time()
    count = 0
    while (time.time() < start + max_seconds):
        count += 1
        yield count
        time.sleep(2)
    raise Exception("Timeout waiting for %s" % purpose)


def ssh_connect(ip, username, connect_kwargs={}, timeout=60):
    if ip == 'fake':
        return fakeprovider.FakeSSHClient()
    # HPcloud may return errno 111 for about 30 seconds after adding the IP
    for count in iterate_timeout(timeout, "ssh access"):
        try:
            client = SSHClient(ip, username, **connect_kwargs)
            break
        except socket.error, e:
            if e[0] not in [111, 113]:
                log.exception('Exception while testing ssh access:')

    out = client.ssh("test ssh access", "echo access okay")
    if "access okay" in out:
        return client
    return None
