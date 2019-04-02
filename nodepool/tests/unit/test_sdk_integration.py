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

import os

import fixtures
import voluptuous
import yaml

from nodepool import config as nodepool_config
from nodepool import provider_manager
from nodepool import tests


class TestShadeIntegration(tests.IntegrationTestCase):
    def _cleanup_cloud_config(self):
        os.remove(self.clouds_path)

    def _use_cloud_config(self, config):
        config_dir = fixtures.TempDir()
        self.useFixture(config_dir)
        self.clouds_path = os.path.join(config_dir.path, 'clouds.yaml')
        self.useFixture(fixtures.MonkeyPatch(
            'openstack.config.loader.CONFIG_FILES',
            [self.clouds_path]))

        with open(self.clouds_path, 'w') as h:
            yaml.safe_dump(config, h)

        self.addCleanup(self._cleanup_cloud_config)

    def test_nodepool_provider_config_bad(self):
        # nodepool doesn't support clouds.yaml-less config anymore
        # Assert that we get a nodepool error and not an os-client-config
        # error.
        self.assertRaises(
            voluptuous.MultipleInvalid,
            self.setup_config, 'integration_noocc.yaml')

    def test_nodepool_occ_config(self):
        configfile = self.setup_config('integration_occ.yaml')
        auth_data = {'username': 'os_real',
                     'project_name': 'os_real',
                     'password': 'os_real',
                     'auth_url': 'os_real'}
        occ_config = {'clouds': {'real-cloud': {'auth': auth_data}}}
        self._use_cloud_config(occ_config)

        config = nodepool_config.loadConfig(configfile)
        self.assertIn('real-provider', config.providers)
        pm = provider_manager.get_provider(config.providers['real-provider'])
        # We need to cleanup the provider manager so that it doesn't leak a
        # thread that causes wait_for_threads in subsequent tests to fail.
        self.addCleanup(pm.stop)
        pm.start(None)
        self.assertEqual(pm._client.auth, auth_data)

    def test_nodepool_occ_config_reload(self):
        configfile = self.setup_config('integration_occ.yaml')
        auth_data = {'username': 'os_real',
                     'project_name': 'os_real',
                     'password': 'os_real',
                     'auth_url': 'os_real'}
        occ_config = {'clouds': {'real-cloud': {'auth': auth_data}}}
        self._use_cloud_config(occ_config)

        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.updateConfig()
        provider_manager = pool.config.provider_managers['real-provider']
        self.assertEqual(provider_manager._client.auth, auth_data)

        # update the config
        auth_data['password'] = 'os_new_real'
        os.remove(self.clouds_path)
        with open(self.clouds_path, 'w') as h:
            yaml.safe_dump(occ_config, h)

        pool.updateConfig()
        provider_manager = pool.config.provider_managers['real-provider']
        self.assertEqual(provider_manager._client.auth, auth_data)
