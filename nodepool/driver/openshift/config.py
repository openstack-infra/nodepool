# Copyright 2018 Red Hat
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

import math
import voluptuous as v

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class OpenshiftLabel(ConfigValue):
    def __eq__(self, other):
        if isinstance(other, OpenshiftLabel):
            return (other.name == self.name and
                    other.type == self.type and
                    other.image_pull == self.image_pull and
                    other.image == self.image and
                    other.cpu == self.cpu and
                    other.memory == self.memory)
        return False

    def __repr__(self):
        return "<OpenshiftLabel %s>" % self.name


class OpenshiftPool(ConfigPool):
    def __eq__(self, other):
        if isinstance(other, OpenshiftPool):
            return (super().__eq__(other) and
                    other.name == self.name and
                    other.labels == self.labels)
        return False

    def __repr__(self):
        return "<OpenshiftPool %s>" % self.name

    def load(self, pool_config, full_config):
        super().load(pool_config)
        self.name = pool_config['name']
        self.labels = {}
        for label in pool_config.get('labels', []):
            pl = OpenshiftLabel()
            pl.name = label['name']
            pl.type = label['type']
            pl.image = label.get('image')
            pl.image_pull = label.get('image-pull', 'IfNotPresent')
            pl.cpu = label.get('cpu')
            pl.memory = label.get('memory')
            pl.pool = self
            self.labels[pl.name] = pl
            full_config.labels[label['name']].pools.append(self)


class OpenshiftProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        self.driver_object = driver
        self.__pools = {}
        super().__init__(provider)

    def __eq__(self, other):
        if isinstance(other, OpenshiftProviderConfig):
            return (super().__eq__(other) and
                    other.context == self.context and
                    other.pools == self.pools)
        return False

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        return False

    def load(self, config):
        self.launch_retries = int(self.provider.get('launch-retries', 3))
        self.context = self.provider['context']
        self.max_projects = self.provider.get('max-projects', math.inf)
        for pool in self.provider.get('pools', []):
            pp = OpenshiftPool()
            pp.load(pool, config)
            pp.provider = self
            self.pools[pp.name] = pp

    def getSchema(self):
        openshift_label = {
            v.Required('name'): str,
            v.Required('type'): str,
            'image': str,
            'image-pull': str,
            'cpu': int,
            'memory': int,
        }

        pool = {
            v.Required('name'): str,
            v.Required('labels'): [openshift_label],
        }

        schema = ProviderConfig.getCommonSchemaDict()
        schema.update({
            v.Required('pools'): [pool],
            v.Required('context'): str,
            'launch-retries': int,
            'max-projects': int,
        })
        return v.Schema(schema)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
