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

import logging

from nodepool.driver import Drivers


def get_provider(provider, use_taskmanager):
    driver = Drivers.get(provider.driver.name)
    return driver.getProvider(provider, use_taskmanager)


class ProviderManager(object):
    log = logging.getLogger("nodepool.ProviderManager")

    @staticmethod
    def reconfigure(old_config, new_config, use_taskmanager=True):
        stop_managers = []
        for p in new_config.providers.values():
            oldmanager = None
            if old_config:
                oldmanager = old_config.provider_managers.get(p.name)
            if oldmanager and p != oldmanager.provider:
                stop_managers.append(oldmanager)
                oldmanager = None
            if oldmanager:
                new_config.provider_managers[p.name] = oldmanager
            else:
                ProviderManager.log.debug("Creating new ProviderManager object"
                                          " for %s" % p.name)
                new_config.provider_managers[p.name] = \
                    get_provider(p, use_taskmanager)
                new_config.provider_managers[p.name].start()

        for stop_manager in stop_managers:
            stop_manager.stop()

    @staticmethod
    def stopProviders(config):
        for m in config.provider_managers.values():
            m.stop()
            m.join()
