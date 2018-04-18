# Copyright 2017 Red Hat
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

import voluptuous as v

from nodepool.driver import ConfigPool
from nodepool.driver import ProviderConfig
from nodepool.config import as_list


class StaticPool(ConfigPool):
    def __eq__(self, other):
        if (other.labels != self.labels or
            other.nodes != self.nodes):
            return False
        return True

    def __repr__(self):
        return "<StaticPool %s>" % self.name


class StaticProviderConfig(ProviderConfig):
    def __eq__(self, other):
        if other.pools != self.pools:
            return False
        return True

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.pools = {}
        for pool in self.provider.get('pools', []):
            pp = StaticPool()
            pp.name = pool['name']
            pp.provider = self
            self.pools[pp.name] = pp
            pp.labels = set()
            pp.nodes = []
            for node in pool.get('nodes', []):
                pp.nodes.append({
                    'name': node['name'],
                    'labels': as_list(node['labels']),
                    'host-key': as_list(node.get('host-key', [])),
                    'timeout': int(node.get('timeout', 5)),
                    'ssh-port': int(node.get('ssh-port', 22)),
                    'username': node.get('username', 'zuul'),
                    'max-parallel-jobs': int(node.get('max-parallel-jobs', 1)),
                })
                for label in node['labels'].split():
                    pp.labels.add(label)
                    config.labels[label].pools.append(pp)

    def getSchema(self):
        pool_node = {
            v.Required('name'): str,
            v.Required('labels'): v.Any(str, [str]),
            'username': str,
            'timeout': int,
            'host-key': v.Any(str, [str]),
            'ssh-port': int,
            'max-parallel-jobs': int,
        }
        pool = {
            'name': str,
            'nodes': [pool_node],
        }
        return v.Schema({'pools': [pool]})

    def getSupportedLabels(self):
        labels = set()
        for pool in self.pools.values():
            labels.update(pool.labels)
        return labels
