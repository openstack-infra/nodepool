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
import threading

import daemon

from nodepool import builder
import nodepool.cmd


# as of python-daemon 1.6 it doesn't bundle pidlockfile anymore
# instead it depends on lockfile-0.9.1 which uses pidfile.
pid_file_module = extras.try_imports(['daemon.pidlockfile', 'daemon.pidfile'])

class NodePoolBuilder(nodepool.cmd.NodepoolApp):

    def sigint_handler(self, signal, frame):
        self.nb.stop()
        sys.exit(0)

    def parse_arguments(self):
        parser = argparse.ArgumentParser(description='NodePool Image Builder.')
        parser.add_argument('-c', dest='config',
                            default='/etc/nodepool/nodepool.yaml',
                            help='path to config file')
        parser.add_argument('-l', dest='logconfig',
                            help='path to log config file')
        parser.add_argument('-p', dest='pidfile',
                            help='path to pid file',
                            default='/var/run/nodepool-builder/'
                                    'nodepool-builder.pid')
        parser.add_argument('-d', dest='nodaemon', action='store_true',
                            help='do not run as a daemon')
        parser.add_argument('--build-workers', dest='build_workers',
                            default=1, help='number of build workers',
                            type=int)
        parser.add_argument('--upload-workers', dest='upload_workers',
                            default=4, help='number of upload workers',
                            type=int)
        self.args = parser.parse_args()

    def main(self):
        self.setup_logging()
        self.nb = builder.NodePoolBuilder(
            self.args.config, self.args.build_workers,
            self.args.upload_workers)

        signal.signal(signal.SIGINT, self.sigint_handler)

        nb_thread = threading.Thread(target=self.nb.runForever)
        nb_thread.start()

        while True:
            signal.pause()


def main():
    nb = NodePoolBuilder()
    nb.parse_arguments()

    if nb.args.nodaemon:
        nb.main()
    else:
        pid = pid_file_module.TimeoutPIDLockFile(nb.args.pidfile, 10)
        with daemon.DaemonContext(pidfile=pid):
            nb.main()


if __name__ == "__main__":
    sys.exit(main())
