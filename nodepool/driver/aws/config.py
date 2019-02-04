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


class ProviderCloudImage(ConfigValue):
    def __init__(self):
        self.name = None
        self.image_id = None
        self.image_name = None
        self.username = None
        self.connection_type = None
        self.connection_port = None

    def __eq__(self, other):
        if isinstance(other, ProviderCloudImage):
            return (self.name == other.name
                    and self.image_id == other.image_id
                    and self.image_name == other.image_name
                    and self.username == other.username
                    and self.connection_type == other.connection_type
                    and self.connection_port == other.connection_port)
        return False

    def __repr__(self):
        return "<ProviderCloudImage %s>" % self.name

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return self.image_id or self.image_name or self.name


class ProviderLabel(ConfigValue):
    def __init__(self):
        self.name = None
        self.cloud_image = None
        self.flavor_name = None
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
                    and other.flavor_name == self.flavor_name
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
        self.ignore_provider_quota = False
        self.availability_zone = None
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

        self.max_cores = pool_config.get('max-cores', math.inf)
        self.max_ram = pool_config.get('max-ram', math.inf)
        self.ignore_provider_quota = pool_config.get(
            'ignore-provider-quota', False)
        self.availability_zone = pool_config.get('availability-zone')
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
            pl.flavor_name = label['flavor-name']
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
                    and other.max_cores == self.max_cores
                    and other.max_ram == self.max_ram
                    and other.ignore_provider_quota == (
                        self.ignore_provider_quota)
                    and other.availability_zone == self.availability_zone
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
        self.rate = None
        self.boot_timeout = None
        self.launch_retries = None
        self.launch_timeout = None
        self.cloud_images = {}
        self.hostname_format = None
        self.image_name_format = None
        super().__init__(provider)

    def __eq__(self, other):
        if isinstance(other, AwsProviderConfig):
            return (super().__eq__(other)
                    and other.profile_name == self.profile_name
                    and other.region_name == self.region_name
                    and other.pools == self.pools
                    and other.rate == self.rate
                    and other.boot_timeout == self.boot_timeout
                    and other.launch_retries == self.launch_retries
                    and other.launch_timeout == self.launch_timeout
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
        self.rate = float(self.provider.get('rate', 1.0))
        self.boot_timeout = self.provider.get('boot-timeout', 60)
        self.launch_retries = self.provider.get('launch-retries', 3)
        self.launch_timeout = self.provider.get('launch-timeout', 3600)
        self.hostname_format = self.provider.get(
            'hostname-format',
            '{label.name}-{provider.name}-{node.id}'
        )
        self.image_name_format = self.provider.get(
            'image-name-format',
            '{image_name}-{timestamp}'
        )

        default_port_mapping = {
            'ssh': 22,
            'winrm': 5986,
        }
        # TODO: diskimages

        for image in self.provider.get('cloud-images', []):
            i = ProviderCloudImage()
            i.name = image['name']
            i.image_id = image.get('image-id', None)
            i.image_name = image.get('image-name', None)
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
            v.Required('flavor-name'): str,
            v.Required('key-name'): str,
            'volume-type': str,
            'volume-size': int
        }

        pool = ConfigPool.getCommonSchemaDict()
        pool.update({
            v.Required('name'): str,
            v.Required('labels'): [pool_label],
            'max-cores': int,
            'max-ram': int,
            'availability-zone': str,
            'security-group-id': str,
            'subnet-id': str,
        })

        provider_cloud_images = {
            'name': str,
            'connection-type': str,
            'connection-port': int,
            v.Exclusive('image-id', 'cloud-image-name-or-id'): str,
            v.Exclusive('image-name', 'cloud-image-name-or-id'): str,
            'username': str,
        }

        provider = ProviderConfig.getCommonSchemaDict()
        provider.update({
            v.Required('pools'): [pool],
            v.Required('region-name'): str,
            'profile-name': str,
            'cloud-images': [provider_cloud_images],
            'rate': v.Coerce(float),
            'hostname-format': str,
            'image-name-format': str,
            'boot-timeout': int,
            'launch-timeout': int,
            'launch-retries': int,
        })
        return v.Schema(provider)

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
