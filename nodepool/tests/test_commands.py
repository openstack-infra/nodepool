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

import logging
import os.path
import sys  # noqa making sure its available for monkey patching
from unittest import skip

import fixtures
import mock

from nodepool.cmd import nodepoolcmd
from nodepool import tests
from nodepool import zk


class TestNodepoolCMD(tests.DBTestCase):
    def patch_argv(self, *args):
        argv = ["nodepool", "-s", self.secure_conf]
        argv.extend(args)
        self.useFixture(fixtures.MonkeyPatch('sys.argv', argv))

    def assert_listed(self, configfile, cmd, col, val, count):
        log = logging.getLogger("tests.PrettyTableMock")
        self.patch_argv("-c", configfile, *cmd)
        with mock.patch('prettytable.PrettyTable.add_row') as m_add_row:
            nodepoolcmd.main()
            rows_with_val = 0
            # Find add_rows with the status were looking for
            for args, kwargs in m_add_row.call_args_list:
                row = args[0]
                log.debug(row)
                if row[col] == val:
                    rows_with_val += 1
            self.assertEquals(rows_with_val, count)

    def assert_images_listed(self, configfile, image_cnt, status="ready"):
        self.assert_listed(configfile, ['image-list'], 5, status, image_cnt)

    def assert_nodes_listed(self, configfile, node_cnt, status="ready"):
        self.assert_listed(configfile, ['list'], 10, status, node_cnt)

    @skip("Skipping until ZooKeeper is enabled")
    def test_dib_image_update(self):
        configfile = self.setup_config("node.yaml")
        self._useBuilder(configfile)
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider", "fake-image")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 1)

    @skip("Skipping until ZooKeeper is enabled")
    def test_image_update_all(self):
        configfile = self.setup_config("node_cmd.yaml")
        self._useBuilder(configfile)
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-image1")
        nodepoolcmd.main()
        self.assert_images_listed(configfile, 1)

    def test_image_list_empty(self):
        self.assert_images_listed(self.setup_config("node_cmd.yaml"), 0)

    @skip("Skipping until ZooKeeper is enabled")
    def test_image_delete_invalid(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-delete", "invalid-image")
        nodepoolcmd.main()

    @skip("Skipping until ZooKeeper is enabled")
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

    @skip("Skipping until ZooKeeper is enabled")
    def test_alien_list_fail(self):
        def fail_list(self):
            raise RuntimeError('Fake list error')
        self.useFixture(fixtures.MonkeyPatch(
            'nodepool.fakeprovider.FakeOpenStackCloud.list_servers',
            fail_list))

        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "alien-list")
        nodepoolcmd.main()

    @skip("Skipping until ZooKeeper is enabled")
    def test_alien_image_list_fail(self):
        def fail_list(self):
            raise RuntimeError('Fake list error')
        self.useFixture(fixtures.MonkeyPatch(
            'nodepool.fakeprovider.FakeOpenStackCloud.list_servers',
            fail_list))

        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "alien-image-list")
        nodepoolcmd.main()

    @skip("Skipping until ZooKeeper is enabled")
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
        configfile = self.setup_config('node.yaml')
        self._useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)

    def test_dib_image_delete(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes(pool)
        # Check the image exists
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)
        builds = self.zk.getMostRecentBuilds(1, 'fake-image', zk.READY)
        # Delete the image
        self.patch_argv('-c', configfile, 'dib-image-delete',
                        'fake-image-%s' % (builds[0].id))
        nodepoolcmd.main()
        self.waitForBuildDeletion('fake-image', '0000000001')
        # Check that fake-image-0000000001 doesn't exist
        self.assert_listed(
            configfile, ['dib-image-list'], 0, 'fake-image-0000000001', 0)

    @skip("Skipping until ZooKeeper is enabled")
    def test_image_upload(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-diskimage')
        self.waitForNodes(pool)
        # Check dib image exists and a single upload is available for it.
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)
        self.assert_images_listed(configfile, 1)
        # Reupload the image
        self.patch_argv('-c', configfile, 'image-upload',
                        'fake-provider', 'fake-image')
        nodepoolcmd.main()
        # Check that two images are ready for it now.
        self.assert_images_listed(configfile, 2)

    @skip("Skipping until ZooKeeper is enabled")
    def test_image_upload_all(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)
        # Check dib image exists and a single upload is available for it.
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)
        self.assert_images_listed(configfile, 1)
        # Reupload the image
        self.patch_argv('-c', configfile, 'image-upload',
                        'all', 'fake-image')
        nodepoolcmd.main()
        # Check that two images are ready for it now.
        self.assert_images_listed(configfile, 2)

    @skip("Skipping until ZooKeeper is enabled")
    def test_hold(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)
        # Assert one node exists and it is node 1 in a ready state.
        self.assert_listed(configfile, ['list'], 0, 1, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)
        # Hold node 1
        self.patch_argv('-c', configfile, 'hold', '1')
        nodepoolcmd.main()
        # Assert the state changed to HOLD
        self.assert_listed(configfile, ['list'], 0, 1, 1)
        self.assert_nodes_listed(configfile, 1, 'hold')

    @skip("Skipping until ZooKeeper is enabled")
    def test_delete(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)
        # Assert one node exists and it is node 1 in a ready state.
        self.assert_listed(configfile, ['list'], 0, 1, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)
        # Delete node 1
        self.assert_listed(configfile, ['delete', '1'], 10, 'delete', 1)

    @skip("Skipping until ZooKeeper is enabled")
    def test_delete_now(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage(pool, 'fake-provider', 'fake-image')
        self.waitForNodes(pool)
        # Assert one node exists and it is node 1 in a ready state.
        self.assert_listed(configfile, ['list'], 0, 1, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)
        # Delete node 1
        self.patch_argv('-c', configfile, 'delete', '--now', '1')
        nodepoolcmd.main()
        # Assert the node is gone
        self.assert_listed(configfile, ['list'], 0, 1, 0)

    def test_image_build(self):
        configfile = self.setup_config('node.yaml')
        self._useBuilder(configfile)

        # wait for the scheduled build to arrive
        self.waitForImage('fake-provider', 'fake-image')
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)
        image = self.zk.getMostRecentImageUpload('fake-image', 'fake-provider')

        # now do the manual build request
        self.patch_argv("-c", configfile, "image-build", "fake-image")
        nodepoolcmd.main()

        self.waitForImage('fake-provider', 'fake-image', [image])
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 2)

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
