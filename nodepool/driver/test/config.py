# Copyright 2017 Red Hat
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

import voluptuous as v

from nodepool.driver import ConfigPool
from nodepool.driver import ProviderConfig


class TestPool(ConfigPool):
    def load(self, pool_config):
        super().load(pool_config)
        self.name = pool_config['name']
        self.labels = pool_config['labels']


class TestConfig(ProviderConfig):
    def __init__(self, *args, **kwargs):
        self.__pools = {}
        super().__init__(*args, **kwargs)

    def __eq__(self, other):
        return self.name == other.name

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        return False

    def load(self, newconfig):
        self.labels = set()
        for pool in self.provider.get('pools', []):
            testpool = TestPool()
            testpool.load(pool)
            testpool.provider = self
            for label in pool['labels']:
                self.labels.add(label)
                newconfig.labels[label].pools.append(testpool)
            self.pools[pool['name']] = testpool

    def getSchema(self):
        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            'name': str,
            'labels': [str]
        })
        schema = ProviderConfig.getCommonSchemaDict()
        schema.update({'pools': [pool]})
        return v.Schema(schema)

    def getSupportedLabels(self, pool_name=None):
        return self.labels
