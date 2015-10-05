# Copyright (C) 2015 Hewlett-Packard Development Company, L.P.
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

import logging
import os

import fixtures
import shade
import testtools
import yaml

from nodepool import tests
from nodepool.provider_manager import shade_inner_exceptions


class TestShadeIntegration(tests.IntegrationTestCase):
    def _cleanup_cloud_config(self):
        os.remove(self.clouds_path)

    def _use_cloud_config(self, config):
        config_dir = fixtures.TempDir()
        self.useFixture(config_dir)
        self.clouds_path = os.path.join(config_dir.path, 'clouds.yaml')
        self.useFixture(fixtures.MonkeyPatch(
            'os_client_config.config.CONFIG_FILES',
            [self.clouds_path]))

        with open(self.clouds_path, 'w') as h:
            yaml.safe_dump(config, h)

        self.addCleanup(self._cleanup_cloud_config)

    def test_nodepool_provider_config(self):
        configfile = self.setup_config('integration.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.updateConfig()
        provider_manager = pool.config.provider_managers['real-provider']
        auth_data = {'username': 'real',
                     'project_id': 'real',
                     'password': 'real',
                     'auth_url': 'real'}
        self.assertEqual(provider_manager._client.auth, auth_data)
        self.assertEqual(provider_manager._client.region_name, 'real-region')

    def test_nodepool_osc_config(self):
        configfile = self.setup_config('integration_osc.yaml')
        auth_data = {'username': 'os_real',
                     'project_name': 'os_real',
                     'password': 'os_real',
                     'auth_url': 'os_real'}
        osc_config = {'clouds': {'real-cloud': {'auth': auth_data}}}
        self._use_cloud_config(osc_config)

        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.updateConfig()
        provider_manager = pool.config.provider_managers['real-provider']
        self.assertEqual(provider_manager._client.auth, auth_data)

    def test_nodepool_osc_config_reload(self):
        configfile = self.setup_config('integration_osc.yaml')
        auth_data = {'username': 'os_real',
                     'project_name': 'os_real',
                     'password': 'os_real',
                     'auth_url': 'os_real'}
        osc_config = {'clouds': {'real-cloud': {'auth': auth_data}}}
        self._use_cloud_config(osc_config)

        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.updateConfig()
        provider_manager = pool.config.provider_managers['real-provider']
        self.assertEqual(provider_manager._client.auth, auth_data)

        # update the config
        auth_data['password'] = 'os_new_real'
        os.remove(self.clouds_path)
        with open(self.clouds_path, 'w') as h:
            yaml.safe_dump(osc_config, h)

        pool.updateConfig()
        provider_manager = pool.config.provider_managers['real-provider']
        self.assertEqual(provider_manager._client.auth, auth_data)

    def test_exceptions(self):
        log = self.useFixture(fixtures.FakeLogger(level=logging.DEBUG))
        with testtools.ExpectedException(shade.OpenStackCloudException):
            with shade_inner_exceptions():
                try:
                    raise Exception("inner test")
                except:
                    raise shade.OpenStackCloudException("outer test")
        self.assertTrue('Exception("inner test")' in log.output)
