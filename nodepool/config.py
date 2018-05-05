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

import math
import time
import yaml

from nodepool import zk
from nodepool.driver import ConfigValue
from nodepool.driver import Drivers


class Config(ConfigValue):
    '''
    Class representing the nodepool configuration.

    This class implements methods to read each of the top-level configuration
    items found in the YAML config file, and set attributes accordingly.
    '''
    def __init__(self):
        self.diskimages = {}
        self.labels = {}
        self.providers = {}
        self.provider_managers = {}
        self.zookeeper_servers = {}

    def setElementsDir(self, value):
        self.elementsdir = value

    def setImagesDir(self, value):
        self.imagesdir = value

    def setBuildLog(self, directory, retention):
        if retention is None:
            retention = 7
        self.build_log_dir = directory
        self.build_log_retention = retention

    def setMaxHoldAge(self, value):
        if value is None or value <= 0:
            value = math.inf
        self.max_hold_age = value

    def setWebApp(self, webapp_cfg):
        if webapp_cfg is None:
            webapp_cfg = {}
        self.webapp = {
            'port': webapp_cfg.get('port', 8005),
            'listen_address': webapp_cfg.get('listen_address', '0.0.0.0')
        }

    def setZooKeeperServers(self, zk_cfg):
        if not zk_cfg:
            return

        for server in zk_cfg:
            z = zk.ZooKeeperConnectionConfig(server['host'],
                                             server.get('port', 2181),
                                             server.get('chroot', None))
            name = z.host + '_' + str(z.port)
            self.zookeeper_servers[name] = z

    def setDiskImages(self, diskimages_cfg):
        if not diskimages_cfg:
            return

        for diskimage in diskimages_cfg:
            d = DiskImage()
            d.name = diskimage['name']
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
            # Ensure at least qcow2 is set to be passed to qemu-img
            if not d.image_types:
                d.image_types.add('qcow2')
            d.pause = bool(diskimage.get('pause', False))
            d.username = diskimage.get('username', 'zuul')
            self.diskimages[d.name] = d

    def setLabels(self, labels_cfg):
        if not labels_cfg:
            return

        for label in labels_cfg:
            l = Label()
            l.name = label['name']
            l.max_ready_age = label.get('max-ready-age', 0)
            l.min_ready = label.get('min-ready', 0)
            l.pools = []
            self.labels[l.name] = l

    def setProviders(self, providers_cfg):
        if not providers_cfg:
            return

        for provider in providers_cfg:
            p = get_provider_config(provider)
            p.load(self)
            self.providers[p.name] = p


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


def as_list(item):
    if not item:
        return []
    if isinstance(item, list):
        return item
    return [item]


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

    newconfig.setElementsDir(config.get('elements-dir'))
    newconfig.setImagesDir(config.get('images-dir'))
    newconfig.setBuildLog(config.get('build-log-dir'),
                          config.get('build-log-retention'))
    newconfig.setMaxHoldAge(config.get('max-hold-age'))
    newconfig.setWebApp(config.get('webapp'))
    newconfig.setZooKeeperServers(config.get('zookeeper-servers'))
    newconfig.setDiskImages(config.get('diskimages'))
    newconfig.setLabels(config.get('labels'))
    newconfig.setProviders(config.get('providers'))

    return newconfig


def loadSecureConfig(config, secure_config_path):
    secure = openConfig(secure_config_path)
    if not secure:   # empty file
        return

    # Eliminate any servers defined in the normal config
    if secure.get('zookeeper-servers', []):
        config.zookeeper_servers = {}

    # TODO(Shrews): Support ZooKeeper auth
    config.setZooKeeperServers(secure.get('zookeeper-servers'))
