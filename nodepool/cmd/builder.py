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

import argparse
import extras
import signal
import sys

import daemon

from nodepool import builder


# as of python-daemon 1.6 it doesn't bundle pidlockfile anymore
# instead it depends on lockfile-0.9.1 which uses pidfile.
pid_file_module = extras.try_imports(['daemon.pidlockfile', 'daemon.pidfile'])

def main():
    parser = argparse.ArgumentParser(description='NodePool Image Builder.')
    parser.add_argument('-c', dest='config',
                        default='/etc/nodepool/nodepool.yaml',
                        help='path to config file')
    parser.add_argument('-p', dest='pidfile',
                        help='path to pid file',
                        default='/var/run/nodepool/nodepool.pid')
    parser.add_argument('-d', dest='nodaemon', action='store_true',
                        help='do not run as a daemon')
    args = parser.parse_args()

    nb = builder.NodePoolBuilder(args.config)

    def sigint_handler(signal, frame):
        nb.stop()

    if args.nodaemon:
        signal.signal(signal.SIGINT, sigint_handler)
        nb.runForever()
    else:
        pid = pid_file_module.TimeoutPIDLockFile(args.pidfile, 10)
        with daemon.DaemonContext(pidfile=pid):
            signal.signal(signal.SIGINT, sigint_handler)
            nb.runForever()


if __name__ == "__main__":
    sys.exit(main())
