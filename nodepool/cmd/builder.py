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

import signal
import sys

from nodepool import builder
import nodepool.cmd


class NodePoolBuilderApp(nodepool.cmd.NodepoolDaemonApp):

    app_name = 'nodepool-builder'
    app_description = 'NodePool Image Builder.'

    def sigint_handler(self, signal, frame):
        self.nb.stop()
        sys.exit(0)

    def create_parser(self):
        parser = super(NodePoolBuilderApp, self).create_parser()

        parser.add_argument('-c', dest='config',
                            default='/etc/nodepool/nodepool.yaml',
                            help='path to config file')
        parser.add_argument('-s', dest='secure',
                            help='path to secure config file')
        parser.add_argument('--build-workers', dest='build_workers',
                            default=1, help='number of build workers',
                            type=int)
        parser.add_argument('--upload-workers', dest='upload_workers',
                            default=4, help='number of upload workers',
                            type=int)
        parser.add_argument('--fake', action='store_true',
                            help='Do not actually run diskimage-builder '
                            '(used for testing)')
        return parser

    def parse_args(self):
        super(NodePoolBuilderApp, self).parse_args()
        self.config_file = self.get_path(self.args.config)
        self.secure_file = self.get_path(self.args.secure)

    def run(self):
        self.nb = builder.NodePoolBuilder(
            self.config_file,
            secure_path=self.secure_file,
            num_builders=self.args.build_workers,
            num_uploaders=self.args.upload_workers,
            fake=self.args.fake)

        signal.signal(signal.SIGINT, self.sigint_handler)

        self.nb.start()

        while True:
            signal.pause()


def main():
    return NodePoolBuilderApp.main()


if __name__ == "__main__":
    sys.exit(main())
