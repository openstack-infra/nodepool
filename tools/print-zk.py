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
import logging

import nodepool.config
import nodepool.zk

# A script to print the zookeeper tree given a nodepool config file.

logging.basicConfig()

parser = argparse.ArgumentParser(description='Print the zookeeper tree')
parser.add_argument('-c', dest='config',
                    default='/etc/nodepool/nodepool.yaml',
                    help='path to config file')
args = parser.parse_args()

config = nodepool.config.loadConfig(args.config)

zk = nodepool.zk.ZooKeeper(enable_cache=False)
zk.connect(list(config.zookeeper_servers.values()))

def join(a, b):
    if a.endswith('/'):
        return a+b
    return a+'/'+b

def print_tree(node):
    data, stat = zk.client.get(node)
    print("Node: %s %s" % (node, stat))
    if data:
        print(data)

    for child in zk.client.get_children(node):
        print()
        print_tree(join(node, child))

print_tree('/')
zk.disconnect()
