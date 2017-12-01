#!/usr/bin/env python

# Copyright (C) 2011-2013 OpenStack Foundation
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

import time
import yaml

from nodepool import zk
from nodepool.driver import ConfigValue
from nodepool.driver import Drivers


class Config(ConfigValue):
    pass


class Label(ConfigValue):
    def __repr__(self):
        return "<Label %s>" % self.name


class DiskImage(ConfigValue):
    def __eq__(self, other):
        if (other.name != self.name or
            other.elements != self.elements or
            other.release != self.release or
            other.rebuild_age != self.rebuild_age or
            other.env_vars != self.env_vars or
            other.image_types != self.image_types or
            other.pause != self.pause or
            other.username != self.username):
            return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "<DiskImage %s>" % self.name


def get_provider_config(provider):
    provider.setdefault('driver', 'openstack')
    # Ensure legacy configuration still works when using fake cloud
    if provider.get('name', '').startswith('fake'):
        provider['driver'] = 'fake'
    driver = Drivers.get(provider['driver'])
    return driver['config'](provider)


def openConfig(path):
    retry = 3

    # Since some nodepool code attempts to dynamically re-read its config
    # file, we need to handle the race that happens if an outside entity
    # edits it (causing it to temporarily not exist) at the same time we
    # attempt to reload it.
    while True:
        try:
            config = yaml.load(open(path))
            break
        except IOError as e:
            if e.errno == 2:
                retry = retry - 1
                time.sleep(.5)
            else:
                raise e
            if retry == 0:
                raise e
    return config


def loadConfig(config_path):
    config = openConfig(config_path)

    # Call driver config reset now to clean global hooks like os_client_config
    for driver in Drivers.drivers.values():
        driver["config"].reset()

    newconfig = Config()
    newconfig.db = None
    newconfig.webapp = {
        'port': config.get('webapp', {}).get('port', 8005),
        'listen_address': config.get('webapp', {}).get('listen_address',
                                                       '0.0.0.0')
    }
    newconfig.providers = {}
    newconfig.labels = {}
    newconfig.elementsdir = config.get('elements-dir')
    newconfig.imagesdir = config.get('images-dir')
    newconfig.provider_managers = {}
    newconfig.zookeeper_servers = {}
    newconfig.diskimages = {}

    for server in config.get('zookeeper-servers', []):
        z = zk.ZooKeeperConnectionConfig(server['host'],
                                         server.get('port', 2181),
                                         server.get('chroot', None))
        name = z.host + '_' + str(z.port)
        newconfig.zookeeper_servers[name] = z

    for diskimage in config.get('diskimages', []):
        d = DiskImage()
        d.name = diskimage['name']
        newconfig.diskimages[d.name] = d
        if 'elements' in diskimage:
            d.elements = u' '.join(diskimage['elements'])
        else:
            d.elements = ''
        # must be a string, as it's passed as env-var to
        # d-i-b, but might be untyped in the yaml and
        # interpreted as a number (e.g. "21" for fedora)
        d.release = str(diskimage.get('release', ''))
        d.rebuild_age = int(diskimage.get('rebuild-age', 86400))
        d.env_vars = diskimage.get('env-vars', {})
        if not isinstance(d.env_vars, dict):
            d.env_vars = {}
        d.image_types = set(diskimage.get('formats', []))
        d.pause = bool(diskimage.get('pause', False))
        d.username = diskimage.get('username', 'zuul')

    for label in config.get('labels', []):
        l = Label()
        l.name = label['name']
        newconfig.labels[l.name] = l
        l.max_ready_age = label.get('max-ready-age', 0)
        l.min_ready = label.get('min-ready', 2)
        l.pools = []

    for provider in config.get('providers', []):
        p = get_provider_config(provider)
        p.load(newconfig)
        newconfig.providers[p.name] = p
    return newconfig


def loadSecureConfig(config, secure_config_path):
    secure = openConfig(secure_config_path)
    if not secure:   # empty file
        return

    # Eliminate any servers defined in the normal config
    if secure.get('zookeeper-servers', []):
        config.zookeeper_servers = {}

    # TODO(Shrews): Support ZooKeeper auth
    for server in secure.get('zookeeper-servers', []):
        z = zk.ZooKeeperConnectionConfig(server['host'],
                                         server.get('port', 2181),
                                         server.get('chroot', None))
        name = z.host + '_' + str(z.port)
        config.zookeeper_servers[name] = z
