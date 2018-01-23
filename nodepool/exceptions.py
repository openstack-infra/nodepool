#!/usr/bin/env python
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.


class NotFound(Exception):
    pass


class LaunchNodepoolException(Exception):
    statsd_key = 'error.nodepool'


class LaunchStatusException(Exception):
    statsd_key = 'error.status'


class LaunchNetworkException(Exception):
    statsd_key = 'error.network'


class LaunchKeyscanException(Exception):
    statsd_key = 'error.keyscan'


class BuilderError(RuntimeError):
    pass


class BuilderInvalidCommandError(BuilderError):
    pass


class DibFailedError(BuilderError):
    pass


class QuotaException(Exception):
    pass


class TimeoutException(Exception):
    pass


class ConnectionTimeoutException(TimeoutException):
    statsd_key = 'error.ssh'


class IPAddTimeoutException(TimeoutException):
    statsd_key = 'error.ipadd'


class ServerDeleteException(TimeoutException):
    statsd_key = 'error.serverdelete'


class ImageCreateException(TimeoutException):
    statsd_key = 'error.imagetimeout'


class ZKException(Exception):
    pass


class ZKLockException(ZKException):
    pass
