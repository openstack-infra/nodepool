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

import voluptuous as v

from nodepool.driver import ConfigPool
from nodepool.driver import ConfigValue
from nodepool.driver import ProviderConfig


class KubernetesLabel(ConfigValue):
    def __eq__(self, other):
        if isinstance(other, KubernetesLabel):
            return (other.name == self.name and
                    other.type == self.type and
                    other.image_pull == self.image_pull and
                    other.image == self.image)
        return False

    def __repr__(self):
        return "<KubternetesLabel %s>" % self.name


class KubernetesPool(ConfigPool):
    def __eq__(self, other):
        if isinstance(other, KubernetesPool):
            return (super().__eq__(other) and
                    other.name == self.name and
                    other.labels == self.labels)
        return False

    def __repr__(self):
        return "<KubernetesPool %s>" % self.name

    def load(self, pool_config, full_config):
        super().load(pool_config)
        self.name = pool_config['name']
        self.labels = {}
        for label in pool_config.get('labels', []):
            pl = KubernetesLabel()
            pl.name = label['name']
            pl.type = label['type']
            pl.image = label.get('image')
            pl.image_pull = label.get('image-pull', 'IfNotPresent')
            pl.pool = self
            self.labels[pl.name] = pl
            full_config.labels[label['name']].pools.append(self)


class KubernetesProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        self.driver_object = driver
        self.__pools = {}
        super().__init__(provider)

    def __eq__(self, other):
        if isinstance(other, KubernetesProviderConfig):
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
        for pool in self.provider.get('pools', []):
            pp = KubernetesPool()
            pp.load(pool, config)
            pp.provider = self
            self.pools[pp.name] = pp

    def getSchema(self):
        k8s_label = {
            v.Required('name'): str,
            v.Required('type'): str,
            'image': str,
            'image-pull': str,
        }

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [k8s_label],
        })

        provider = {
            v.Required('pools'): [pool],
            v.Required('context'): str,
            'launch-retries': int,
        }

        schema = ProviderConfig.getCommonSchemaDict()
        schema.update(provider)
        return v.Schema(schema)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
