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

import fixtures
import mock
import testtools

from nodepool.cmd import nodepoolcmd
from nodepool import tests
from nodepool import zk
from nodepool.nodeutils import iterate_timeout


class TestNodepoolCMD(tests.DBTestCase):
    def setUp(self):
        super(TestNodepoolCMD, self).setUp()

    def patch_argv(self, *args):
        argv = ["nodepool"]
        argv.extend(args)
        self.useFixture(fixtures.MonkeyPatch('sys.argv', argv))

    def assert_listed(self, configfile, cmd, col, val, count, col_count=0):
        log = logging.getLogger("tests.PrettyTableMock")
        self.patch_argv("-c", configfile, *cmd)
        for _ in iterate_timeout(10, AssertionError, 'assert listed'):
            try:
                with mock.patch('prettytable.PrettyTable.add_row') as \
                        m_add_row:
                    nodepoolcmd.main()
                    rows_with_val = 0
                    # Find add_rows with the status were looking for
                    for args, kwargs in m_add_row.call_args_list:
                        row = args[0]
                        if col_count:
                            self.assertEquals(len(row), col_count)
                        log.debug(row)
                        if row[col] == val:
                            rows_with_val += 1
                    self.assertEquals(rows_with_val, count)
                break
            except AssertionError:
                # retry
                pass

    def assert_alien_images_listed(self, configfile, image_cnt, image_id):
        self.assert_listed(configfile, ['alien-image-list'], 2, image_id,
                           image_cnt)

    def assert_alien_images_empty(self, configfile):
        self.assert_alien_images_listed(configfile, 0, 0)

    def assert_images_listed(self, configfile, image_cnt, status="ready"):
        self.assert_listed(configfile, ['image-list'], 6, status, image_cnt)

    def assert_nodes_listed(self, configfile, node_cnt, status="ready",
                            detail=False, validate_col_count=False):
        cmd = ['list']
        col_count = 9
        if detail:
            cmd += ['--detail']
            col_count = 18
        if not validate_col_count:
            col_count = 0
        self.assert_listed(configfile, cmd, 6, status, node_cnt, col_count)

    def test_image_list_empty(self):
        self.assert_images_listed(self.setup_config("node_cmd.yaml"), 0)

    def test_image_delete_invalid(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-delete",
                        "--provider", "invalid-provider",
                        "--image", "invalid-image",
                        "--build-id", "invalid-build-id",
                        "--upload-id", "invalid-upload-id")
        nodepoolcmd.main()

    def test_image_delete(self):
        configfile = self.setup_config("node.yaml")
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        image = self.zk.getMostRecentImageUpload('fake-image', 'fake-provider')
        self.patch_argv("-c", configfile, "image-delete",
                        "--provider", "fake-provider",
                        "--image", "fake-image",
                        "--build-id", image.build_id,
                        "--upload-id", image.id)
        nodepoolcmd.main()
        self.waitForUploadRecordDeletion('fake-provider', 'fake-image',
                                         image.build_id, image.id)

    def test_alien_image_list_empty(self):
        configfile = self.setup_config("node.yaml")
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        self.patch_argv("-c", configfile, "alien-image-list")
        nodepoolcmd.main()
        self.assert_alien_images_empty(configfile)

    def test_alien_image_list_fail(self):
        def fail_list(self):
            raise RuntimeError('Fake list error')
        self.useFixture(fixtures.MonkeyPatch(
            'nodepool.driver.fake.provider.FakeOpenStackCloud.list_servers',
            fail_list))

        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "alien-image-list")
        nodepoolcmd.main()

    def test_list_nodes(self):
        configfile = self.setup_config('node.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')

        for _ in iterate_timeout(10, Exception, "assert nodes are listed"):
            try:
                self.assert_nodes_listed(configfile, 1, detail=False,
                                         validate_col_count=True)
                break
            except AssertionError:
                # node is not listed yet, retry later
                pass

    def test_list_nodes_detail(self):
        configfile = self.setup_config('node.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes('fake-label')
        for _ in iterate_timeout(10, Exception, "assert nodes are listed"):
            try:
                self.assert_nodes_listed(configfile, 1, detail=True,
                                         validate_col_count=True)
                break
            except AssertionError:
                # node is not listed yet, retry later
                pass

    def test_config_validate(self):
        config = os.path.join(os.path.dirname(tests.__file__),
                              'fixtures', 'config_validate', 'good.yaml')
        self.patch_argv('-c', config, 'config-validate')
        nodepoolcmd.main()

    def test_dib_image_list(self):
        configfile = self.setup_config('node.yaml')
        self.useBuilder(configfile)
        self.waitForImage('fake-provider', 'fake-image')
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)

    def test_dib_image_build_pause(self):
        configfile = self.setup_config('node_diskimage_pause.yaml')
        self.useBuilder(configfile)
        self.patch_argv("-c", configfile, "image-build", "fake-image")
        with testtools.ExpectedException(Exception):
            nodepoolcmd.main()
        self.assert_listed(configfile, ['dib-image-list'], 1, 'fake-image', 0)

    def test_dib_image_pause(self):
        configfile = self.setup_config('node_diskimage_pause.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        nodes = self.waitForNodes('fake-label2')
        self.assertEqual(len(nodes), 1)
        self.assert_listed(configfile, ['dib-image-list'], 1, 'fake-image', 0)
        self.assert_listed(configfile, ['dib-image-list'], 1, 'fake-image2', 1)

    def test_dib_image_upload_pause(self):
        configfile = self.setup_config('node_image_upload_pause.yaml')
        self.useBuilder(configfile)
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()
        nodes = self.waitForNodes('fake-label2')
        self.assertEqual(len(nodes), 1)
        # Make sure diskimages were built.
        self.assert_listed(configfile, ['dib-image-list'], 1, 'fake-image', 1)
        self.assert_listed(configfile, ['dib-image-list'], 1, 'fake-image2', 1)
        # fake-image will be missing, since it is paused.
        self.assert_listed(configfile, ['image-list'], 3, 'fake-image', 0)
        self.assert_listed(configfile, ['image-list'], 3, 'fake-image2', 1)

    def test_dib_image_delete(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)
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

    def test_delete(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        # Assert one node exists and it is nodes[0].id in a ready state.
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)

        # Delete node
        self.patch_argv('-c', configfile, 'delete', nodes[0].id)
        nodepoolcmd.main()
        self.waitForNodeDeletion(nodes[0])

        # Assert the node is gone
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 0)

    def test_delete_now(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)

        # (Shrews): This is a hack to avoid a race with the DeletedNodeWorker
        # thread where it may see that our direct call to NodeDeleter.delete()
        # has changed the node state to DELETING and lock the node during the
        # act of deletion, but *after* the lock znode child has been deleted
        # and *before* kazoo has fully removed the node znode itself. This race
        # causes the rare kazoo.exceptions.NotEmptyError in this test because
        # a new lock znode gets created (that the original delete does not see)
        # preventing the node znode from being deleted.
        pool.delete_interval = 5

        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        # Assert one node exists and it is node 1 in a ready state.
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 1)
        self.assert_nodes_listed(configfile, 1, zk.READY)

        # Delete node
        self.patch_argv('-c', configfile, 'delete', '--now', nodes[0].id)
        nodepoolcmd.main()
        self.waitForNodeDeletion(nodes[0])

        # Assert the node is gone
        self.assert_listed(configfile, ['list'], 0, nodes[0].id, 0)

    def test_image_build(self):
        configfile = self.setup_config('node.yaml')
        self.useBuilder(configfile)

        # wait for the scheduled build to arrive
        self.waitForImage('fake-provider', 'fake-image')
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 1)
        image = self.zk.getMostRecentImageUpload('fake-image', 'fake-provider')

        # now do the manual build request
        self.patch_argv("-c", configfile, "image-build", "fake-image")
        nodepoolcmd.main()

        self.waitForImage('fake-provider', 'fake-image', [image])
        self.assert_listed(configfile, ['dib-image-list'], 4, zk.READY, 2)

    def test_request_list(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        self.waitForImage('fake-provider', 'fake-image')
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(len(nodes), 1)

        req = zk.NodeRequest()
        req.state = zk.PENDING   # so it will be ignored
        req.node_types = ['fake-label']
        req.requestor = 'test_request_list'
        self.zk.storeNodeRequest(req)

        self.assert_listed(configfile, ['request-list'], 0, req.id, 1)

    def test_without_argument(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile)
        result = nodepoolcmd.main()
        self.assertEqual(1, result)

    def test_info_and_erase(self):
        configfile = self.setup_config('info_cmd_two_provider.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self.useBuilder(configfile)
        pool.start()
        p1_image = self.waitForImage('fake-provider', 'fake-image')
        p1_nodes = self.waitForNodes('fake-label')
        p2_nodes = self.waitForNodes('fake-label2')

        # Get rid of the second provider so that when we remove its
        # data from ZooKeeper, the builder and launcher don't attempt to
        # recreate the data.
        self.replace_config(configfile, 'info_cmd_two_provider_remove.yaml')

        # Verify that the second provider image is listed
        self.assert_listed(
            configfile,
            ['info', 'fake-provider2'],
            0, 'fake-image', 1)

        # Verify that the second provider node is listed.
        self.assert_listed(
            configfile,
            ['info', 'fake-provider2'],
            0, p2_nodes[0].id, 1)

        # Erase the data for the second provider
        self.patch_argv(
            "-c", configfile, 'erase', 'fake-provider2', '--force')
        nodepoolcmd.main()

        # Verify that no build or node for the second provider is listed
        # after the previous erase
        self.assert_listed(
            configfile,
            ['info', 'fake-provider2'],
            0, 'fake-image', 0)
        self.assert_listed(
            configfile,
            ['info', 'fake-provider2'],
            0, p2_nodes[0].id, 0)

        # Verify that we did not affect the first provider
        image = self.waitForImage('fake-provider', 'fake-image')
        self.assertEqual(p1_image, image)
        nodes = self.waitForNodes('fake-label')
        self.assertEqual(1, len(nodes))
        self.assertEqual(p1_nodes[0], nodes[0])
