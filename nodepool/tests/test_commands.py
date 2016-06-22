# Copyright (C) 2015 OpenStack Foundation
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
# See the License for the specific language governing permissions and
# limitations under the License.

import os.path
import sys  # noqa making sure its available for monkey patching

import fixtures
import mock

from nodepool.cmd import nodepoolcmd
from nodepool import tests


class TestNodepoolCMD(tests.DBTestCase):
    def patch_argv(self, *args):
        argv = ["nodepool", "-s", self.secure_conf]
        argv.extend(args)
        self.useFixture(fixtures.MonkeyPatch('sys.argv', argv))

    def assert_listed(self, configfile, cmd, col, val, count):
        self.patch_argv("-c", configfile, *cmd)
        with mock.patch('prettytable.PrettyTable.add_row') as m_add_row:
            nodepoolcmd.main()
            rows_with_val = 0
            # Find add_rows with the status were looking for
            for args, kwargs in m_add_row.call_args_list:
                row = args[0]
                if row[col] == val:
                    rows_with_val += 1
            self.assertEquals(rows_with_val, count)

    def assert_images_listed(self, configfile, image_cnt, status="ready"):
        self.assert_listed(configfile, ['image-list'], 7, status, image_cnt)

    def assert_nodes_listed(self, configfile, node_cnt, status="ready"):
        self.assert_listed(configfile, ['list'], 10, status, node_cnt)

    def test_snapshot_image_update(self):
        configfile = self.setup_config("node.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider", "fake-image")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 1)

    def test_dib_image_update(self):
        configfile = self.setup_config("node_dib.yaml")
        self._useBuilder(configfile)
        self.patch_argv("-c", configfile, "image-update",
                        "fake-dib-provider", "fake-dib-image")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 1)

    def test_dib_snapshot_image_update(self):
        configfile = self.setup_config("node_dib_and_snap.yaml")
        self._useBuilder(configfile)
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider1", "fake-dib-image")
        nodepoolcmd.main()
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider2", "fake-dib-image")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 2)

    def test_dib_snapshot_image_update_all(self):
        configfile = self.setup_config("node_dib_and_snap.yaml")
        self._useBuilder(configfile)
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-dib-image")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 2)

    def test_image_update_all(self):
        configfile = self.setup_config("node_cmd.yaml")
        self._useBuilder(configfile)
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-image1")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 1)

    def test_image_list_empty(self):
        self.assert_images_listed(self.setup_config("node_cmd.yaml"), 0)

    def test_image_delete_invalid(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-delete", "invalid-image")
        nodepoolcmd.main()

    def test_image_delete_snapshot(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-image1")
        nodepoolcmd.main()
        pool = self.useNodepool(configfile, watermark_sleep=1)
        # This gives us a nodepool with a working db but not running which
        # is important so we can control image building
        pool.updateConfig()
        self.waitForImage(pool, 'fake-provider1', 'fake-image1')

        self.patch_argv("-c", configfile, "image-delete", '1')
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 0)

    def test_alien_list_fail(self):
        def fail_list(self):
            raise RuntimeError('Fake list error')
        self.useFixture(fixtures.MonkeyPatch(
            'nodepool.fakeprovider.FakeOpenStackCloud.list_servers',
            fail_list))

        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "alien-list")
        nodepoolcmd.main()

    def test_alien_image_list_fail(self):
        def fail_list(self):
            raise RuntimeError('Fake list error')
        self.useFixture(fixtures.MonkeyPatch(
            'nodepool.fakeprovider.FakeOpenStackCloud.list_servers',
            fail_list))

        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "alien-image-list")
        nodepoolcmd.main()

    def test_list_nodes(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)
        self.assert_nodes_listed(configfile, 1)

    def test_config_validate(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate', 'good.yaml')
        self.patch_argv('-c', config, 'config-validate')
        nodepoolcmd.main()

    def test_dib_image_list(self):
        configfile = self.setup_config('node_dib.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage(pool, 'fake-dib-provider', 'fake-dib-image')
        self.waitForNodes(pool)
        self.assert_listed(configfile, ['dib-image-list'], 4, 'ready', 1)

    def test_dib_image_delete(self):
        configfile = self.setup_config('node_dib.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage(pool, 'fake-dib-provider', 'fake-dib-image')
        self.waitForNodes(pool)
        # Check the image exists
        self.assert_listed(configfile, ['dib-image-list'], 0, 1, 1)
        self.assert_listed(configfile, ['dib-image-list'], 4, 'ready', 1)
        # Delete the image
        self.patch_argv('-c', configfile, 'dib-image-delete', '1')
        nodepoolcmd.main()
        # Check the the image is no longer listed
        self.assert_listed(configfile, ['dib-image-list'], 0, 1, 0)

    def test_image_upload(self):
        configfile = self.setup_config('node_dib.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage(pool, 'fake-dib-provider', 'fake-dib-image')
        self.waitForNodes(pool)
        # Check dib image exists and a single upload is available for it.
        self.assert_listed(configfile, ['dib-image-list'], 4, 'ready', 1)
        self.assert_images_listed(configfile, 1)
        # Reupload the image
        self.patch_argv('-c', configfile, 'image-upload',
                        'fake-dib-provider', 'fake-dib-image')
        nodepoolcmd.main()
        # Check that two images are ready for it now.
        self.assert_images_listed(configfile, 2)

    def test_image_upload_all(self):
        configfile = self.setup_config('node_dib.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage(pool, 'fake-dib-provider', 'fake-dib-image')
        self.waitForNodes(pool)
        # Check dib image exists and a single upload is available for it.
        self.assert_listed(configfile, ['dib-image-list'], 4, 'ready', 1)
        self.assert_images_listed(configfile, 1)
        # Reupload the image
        self.patch_argv('-c', configfile, 'image-upload',
                        'all', 'fake-dib-image')
        nodepoolcmd.main()
        # Check that two images are ready for it now.
        self.assert_images_listed(configfile, 2)

    def test_hold(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)
        # Assert one node exists and it is node 1 in a ready state.
        self.assert_listed(configfile, ['list'], 0, 1, 1)
        self.assert_nodes_listed(configfile, 1, 'ready')
        # Hold node 1
        self.patch_argv('-c', configfile, 'hold', '1')
        nodepoolcmd.main()
        # Assert the state changed to HOLD
        self.assert_listed(configfile, ['list'], 0, 1, 1)
        self.assert_nodes_listed(configfile, 1, 'hold')

    def test_delete(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)
        # Assert one node exists and it is node 1 in a ready state.
        self.assert_listed(configfile, ['list'], 0, 1, 1)
        self.assert_nodes_listed(configfile, 1, 'ready')
        # Delete node 1
        self.assert_listed(configfile, ['delete', '1'], 10, 'delete', 1)

    def test_delete_now(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)
        # Assert one node exists and it is node 1 in a ready state.
        self.assert_listed(configfile, ['list'], 0, 1, 1)
        self.assert_nodes_listed(configfile, 1, 'ready')
        # Delete node 1
        self.patch_argv('-c', configfile, 'delete', '--now', '1')
        nodepoolcmd.main()
        # Assert the node is gone
        self.assert_listed(configfile, ['list'], 0, 1, 0)

    def test_image_build(self):
        configfile = self.setup_config('node_dib.yaml')
        self._useBuilder(configfile)

        self.patch_argv("-c", configfile, "image-build", "fake-dib-diskimage")
        nodepoolcmd.main()
        self.assert_listed(configfile, ['dib-image-list'], 4, 'ready', 1)

    def test_job_create(self):
        configfile = self.setup_config('node.yaml')
        self.patch_argv("-c", configfile, "job-create", "fake-job",
                        "--hold-on-failure", "1")
        nodepoolcmd.main()
        self.assert_listed(configfile, ['job-list'], 2, 1, 1)

    def test_job_delete(self):
        configfile = self.setup_config('node.yaml')
        self.patch_argv("-c", configfile, "job-create", "fake-job",
                        "--hold-on-failure", "1")
        nodepoolcmd.main()
        self.assert_listed(configfile, ['job-list'], 2, 1, 1)
        self.patch_argv("-c", configfile, "job-delete", "1")
        nodepoolcmd.main()
        self.assert_listed(configfile, ['job-list'], 0, 1, 0)
