#!/usr/bin/env python
#
# Copyright 2013 OpenStack Foundation
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
import logging.config
import sys
import time

from nodepool import nodedb
from nodepool import nodepool
from prettytable import PrettyTable


class NodePoolCmd(object):
    def __init__(self):
        self.args = None

    def parse_arguments(self):
        parser = argparse.ArgumentParser(description='Node pool.')
        parser.add_argument('-c', dest='config',
                            default='/etc/nodepool/nodepool.yaml',
                            help='path to config file')
        parser.add_argument('--version', dest='version', action='store_true',
                            help='show version')

        subparsers = parser.add_subparsers(title='commands',
                                           description='valid commands',
                                           help='additional help')

        cmd_list = subparsers.add_parser('list', help='list nodes')
        cmd_list.set_defaults(func=self.list)
        cmd_image_list = subparsers.add_parser('image-list',
                                               help='list images')
        cmd_image_list.set_defaults(func=self.image_list)

        cmd_image_update = subparsers.add_parser('image-update',
                                                 help='update image')
        cmd_image_update.add_argument('provider', help='provider name')
        cmd_image_update.add_argument('image', help='image name')
        cmd_image_update.set_defaults(func=self.image_update)

        cmd_alien_list = subparsers.add_parser(
            'alien-list',
            help='list nodes not accounted for by nodepool')
        cmd_alien_list.set_defaults(func=self.alien_list)
        cmd_alien_list.add_argument('provider', help='provider name',
                                    nargs='?')

        cmd_alien_image_list = subparsers.add_parser(
            'alien-image-list',
            help='list images not accounted for by nodepool')
        cmd_alien_image_list.set_defaults(func=self.alien_image_list)
        cmd_alien_image_list.add_argument('provider', help='provider name',
                                          nargs='?')

        cmd_hold = subparsers.add_parser(
            'hold',
            help='place a node in the HOLD state')
        cmd_hold.set_defaults(func=self.hold)
        cmd_hold.add_argument('id', help='node id')

        cmd_delete = subparsers.add_parser(
            'delete',
            help='place a node in the DELETE state')
        cmd_delete.set_defaults(func=self.delete)
        cmd_delete.add_argument('id', help='node id')
        cmd_delete.add_argument('--now',
                                action='store_true',
                                help='delete the node in the foreground')

        cmd_image_delete = subparsers.add_parser(
            'image-delete',
            help='delete an image')
        cmd_image_delete.set_defaults(func=self.image_delete)
        cmd_image_delete.add_argument('id', help='image id')

        self.args = parser.parse_args()

    def setup_logging(self):
        logging.basicConfig(level=logging.INFO)

    def list(self, node_id=None):
        t = PrettyTable(["ID", "Provider", "Image", "Target", "Hostname",
                         "NodeName", "Server ID", "IP", "State",
                         "Age (hours)"])
        t.align = 'l'
        now = time.time()
        with self.pool.getDB().getSession() as session:
            for node in session.getNodes():
                if node_id and node.id != node_id:
                    continue
                t.add_row([node.id, node.provider_name, node.image_name,
                           node.target_name, node.hostname, node.nodename,
                           node.external_id, node.ip,
                           nodedb.STATE_NAMES[node.state],
                           '%.02f' % ((now - node.state_time) / 3600)])
            print t

    def image_list(self):
        t = PrettyTable(["ID", "Provider", "Image", "Hostname", "Version",
                         "Image ID", "Server ID", "State", "Age (hours)"])
        t.align = 'l'
        now = time.time()
        with self.pool.getDB().getSession() as session:
            for image in session.getSnapshotImages():
                t.add_row([image.id, image.provider_name, image.image_name,
                           image.hostname, image.version,
                           image.external_id, image.server_external_id,
                           nodedb.STATE_NAMES[image.state],
                           '%.02f' % ((now - image.state_time) / 3600)])
            print t

    def image_update(self):
        self.pool.reconfigureManagers(self.pool.config)
        provider = self.pool.config.providers[self.args.provider]
        image = provider.images[self.args.image]

        with self.pool.getDB().getSession() as session:
            self.pool.updateImage(session, provider, image)

    def alien_list(self):
        self.pool.reconfigureManagers(self.pool.config)

        t = PrettyTable(["Provider", "Hostname", "Server ID", "IP"])
        t.align = 'l'
        with self.pool.getDB().getSession() as session:
            for provider in self.pool.config.providers.values():
                if (self.args.provider and
                    provider.name != self.args.provider):
                    continue
                manager = self.pool.getProviderManager(provider)

                for server in manager.listServers():
                    if not session.getNodeByExternalID(
                        provider.name, server['id']):
                        t.add_row([provider.name, server['name'], server['id'],
                                   server['public_v4']])
        print t

    def alien_image_list(self):
        self.pool.reconfigureManagers(self.pool.config)

        t = PrettyTable(["Provider", "Name", "Image ID"])
        t.align = 'l'
        with self.pool.getDB().getSession() as session:
            for provider in self.pool.config.providers.values():
                if (self.args.provider and
                    provider.name != self.args.provider):
                    continue
                manager = self.pool.getProviderManager(provider)

                for image in manager.listImages():
                    if image['metadata'].get('image_type') == 'snapshot':
                        if not session.getSnapshotImageByExternalID(
                            provider.name, image['id']):
                            t.add_row([provider.name, image['name'],
                                       image['id']])
        print t

    def hold(self):
        node_id = None
        with self.pool.getDB().getSession() as session:
            node = session.getNode(self.args.id)
            node.state = nodedb.HOLD
            node_id = node.id
        self.list(node_id=node_id)

    def delete(self):
        self.pool.reconfigureManagers(self.pool.config)
        with self.pool.getDB().getSession() as session:
            node = session.getNode(self.args.id)
            if self.args.now:
                self.pool._deleteNode(session, node)
            else:
                node.state = nodedb.DELETE
                self.list(node_id=node.id)

    def image_delete(self):
        self.pool.reconfigureManagers(self.pool.config)
        with self.pool.getDB().getSession() as session:
            snap_image = session.getSnapshotImage(self.args.id)
            self.pool.deleteImage(snap_image)

    def main(self):
        self.parse_arguments()

        if self.args.version:
            from nodepool.version import version_info as npc_version_info
            print "Nodepool version: %s" % npc_version_info.version_string()
            return(0)

        self.pool = nodepool.NodePool(self.args.config)
        config = self.pool.loadConfig()
        self.pool.reconfigureDatabase(config)
        self.pool.setConfig(config)
        self.args.func()


def main():
    npc = NodePoolCmd()
    npc.setup_logging()
    return npc.main()


if __name__ == "__main__":
    sys.exit(main())
