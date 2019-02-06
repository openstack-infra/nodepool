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


class ProviderCloudImage(ConfigValue):
    def __init__(self):
        self.name = None
        self.image_id = None
        self.username = None
        self.connection_type = None
        self.connection_port = None

    def __eq__(self, other):
        if isinstance(other, ProviderCloudImage):
            return (self.name == other.name
                    and self.image_id == other.image_id
                    and self.username == other.username
                    and self.connection_type == other.connection_type
                    and self.connection_port == other.connection_port)
        return False

    def __repr__(self):
        return "<ProviderCloudImage %s>" % self.name

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return self.image_id or self.name


class ProviderLabel(ConfigValue):
    def __init__(self):
        self.name = None
        self.cloud_image = None
        self.instance_type = None
        self.key_name = None
        self.volume_size = None
        self.volume_type = None
        # The ProviderPool object that owns this label.
        self.pool = None

    def __eq__(self, other):
        if isinstance(other, ProviderLabel):
            # NOTE(Shrews): We intentionally do not compare 'pool' here
            # since this causes recursive checks with ProviderPool.
            return (other.name == self.name
                    and other.cloud_image == self.cloud_image
                    and other.instance_type == self.instance_type
                    and other.key_name == self.key_name
                    and other.volume_size == self.volume_size
                    and other.volume_type == self.volume_type)
        return False

    def __repr__(self):
        return "<ProviderLabel %s>" % self.name


class ProviderPool(ConfigPool):
    def __init__(self):
        self.name = None
        self.max_cores = None
        self.max_ram = None
        self.subnet_id = None
        self.security_group_id = None
        self.host_key_checking = True
        self.labels = None
        # The ProviderConfig object that owns this pool.
        self.provider = None

        # Initialize base class attributes
        super().__init__()

    def load(self, pool_config, full_config, provider):
        super().load(pool_config)
        self.name = pool_config['name']
        self.provider = provider

        self.security_group_id = pool_config.get('security-group-id')
        self.subnet_id = pool_config.get('subnet-id')
        self.host_key_checking = bool(
            pool_config.get('host-key-checking', True))

        for label in pool_config.get('labels', []):
            pl = ProviderLabel()
            pl.name = label['name']
            pl.pool = self
            self.labels[pl.name] = pl
            cloud_image_name = label.get('cloud-image', None)
            if cloud_image_name:
                cloud_image = self.provider.cloud_images.get(
                    cloud_image_name, None)
                if not cloud_image:
                    raise ValueError(
                        "cloud-image %s does not exist in provider %s"
                        " but is referenced in label %s" %
                        (cloud_image_name, self.name, pl.name))
            else:
                cloud_image = None
            pl.cloud_image = cloud_image
            pl.instance_type = label['instance-type']
            pl.key_name = label['key-name']
            pl.volume_type = label.get('volume-type')
            pl.volume_size = label.get('volume-size')
            full_config.labels[label['name']].pools.append(self)

    def __eq__(self, other):
        if isinstance(other, ProviderPool):
            # NOTE(Shrews): We intentionally do not compare 'provider' here
            # since this causes recursive checks with OpenStackProviderConfig.
            return (super().__eq__(other)
                    and other.name == self.name
                    and other.subnet_id == self.subnet_id
                    and other.security_group_id == self.security_group_id
                    and other.host_key_checking == self.host_key_checking
                    and other.labels == self.labels)
        return False

    def __repr__(self):
        return "<ProviderPool %s>" % self.name


class AwsProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        self.driver_object = driver
        self.__pools = {}
        self.profile_name = None
        self.region_name = None
        self.boot_timeout = None
        self.launch_retries = None
        self.cloud_images = {}
        super().__init__(provider)

    def __eq__(self, other):
        if isinstance(other, AwsProviderConfig):
            return (super().__eq__(other)
                    and other.profile_name == self.profile_name
                    and other.region_name == self.region_name
                    and other.pools == self.pools
                    and other.boot_timeout == self.boot_timeout
                    and other.launch_retries == self.launch_retries
                    and other.cloud_images == self.cloud_images)
        return False

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        return True

    @staticmethod
    def reset():
        pass

    def load(self, config):
        self.profile_name = self.provider.get('profile-name')
        self.region_name = self.provider.get('region-name')
        self.boot_timeout = self.provider.get('boot-timeout', 60)
        self.launch_retries = self.provider.get('launch-retries', 3)

        default_port_mapping = {
            'ssh': 22,
            'winrm': 5986,
        }
        # TODO: diskimages

        for image in self.provider.get('cloud-images', []):
            i = ProviderCloudImage()
            i.name = image['name']
            i.image_id = image.get('image-id', None)
            i.username = image.get('username', None)
            i.connection_type = image.get('connection-type', 'ssh')
            i.connection_port = image.get(
                'connection-port',
                default_port_mapping.get(i.connection_type, 22))
            self.cloud_images[i.name] = i

        for pool in self.provider.get('pools', []):
            pp = ProviderPool()
            pp.load(pool, config, self)
            self.pools[pp.name] = pp

    def getSchema(self):
        pool_label = {
            v.Required('name'): str,
            v.Exclusive('cloud-image', 'label-image'): str,
            v.Required('instance-type'): str,
            v.Required('key-name'): str,
            'volume-type': str,
            'volume-size': int
        }

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [pool_label],
            'security-group-id': str,
            'subnet-id': str,
        })

        provider_cloud_images = {
            'name': str,
            'connection-type': str,
            'connection-port': int,
            'image-id': str,
            'username': str,
        }

        provider = ProviderConfig.getCommonSchemaDict()
        provider.update({
            v.Required('pools'): [pool],
            v.Required('region-name'): str,
            'profile-name': str,
            'cloud-images': [provider_cloud_images],
            'hostname-format': str,
            'boot-timeout': int,
            'launch-retries': int,
        })
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
