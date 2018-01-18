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

import errno
import ipaddress
import time
import six
import socket
import logging

import paramiko

from nodepool import exceptions

log = logging.getLogger("nodepool.utils")

# How long to sleep while waiting for something in a loop
ITERATE_INTERVAL = 2


def iterate_timeout(max_seconds, exc, purpose):
    start = time.time()
    count = 0
    while (time.time() < start + max_seconds):
        count += 1
        yield count
        time.sleep(ITERATE_INTERVAL)
    raise exc("Timeout waiting for %s" % purpose)


def keyscan(ip, port=22, timeout=60):
    '''
    Scan the IP address for public SSH keys.

    Keys are returned formatted as: "<type> <base64_string>"
    '''
    if 'fake' in ip:
        return ['ssh-rsa FAKEKEY']

    if ipaddress.ip_address(six.text_type(ip)).version < 6:
        family = socket.AF_INET
        sockaddr = (ip, port)
    else:
        family = socket.AF_INET6
        sockaddr = (ip, port, 0, 0)

    keys = []
    key = None
    for count in iterate_timeout(
            timeout, exceptions.SSHTimeoutException, "ssh access"):
        sock = None
        t = None
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            t = paramiko.transport.Transport(sock)
            t.start_client(timeout=timeout)
            key = t.get_remote_server_key()
            break
        except socket.error as e:
            if e.errno not in [errno.ECONNREFUSED, errno.EHOSTUNREACH, None]:
                log.exception(
                    'Exception with ssh access to %s:' % ip)
        except Exception as e:
            log.exception("ssh-keyscan failure: %s", e)
        finally:
            try:
                if t:
                    t.close()
            except Exception as e:
                log.exception('Exception closing paramiko: %s', e)
            try:
                if sock:
                    sock.close()
            except Exception as e:
                log.exception('Exception closing socket: %s', e)

    # Paramiko, at this time, seems to return only the ssh-rsa key, so
    # only the single key is placed into the list.
    if key:
        keys.append("%s %s" % (key.get_name(), key.get_base64()))

    return keys
