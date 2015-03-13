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

import sys  # noqa making sure its available for monkey patching

import fixtures
import mock

from nodepool.cmd import nodepoolcmd
from nodepool import tests


class TestNodepoolCMD(tests.DBTestCase):
    def patch_argv(self, *args):
        argv = ["nodepool"]
        argv.extend(args)
        self.useFixture(fixtures.MonkeyPatch('sys.argv', argv))

    def assert_images_listed(self, configfile, image_cnt, status="ready"):
        self.patch_argv("-c", configfile, "image-list")
        with mock.patch('prettytable.PrettyTable.add_row') as m_add_row:
            nodepoolcmd.main()
            images_with_status = 0
            # Find add_rows with the status were looking for
            for args, kwargs in m_add_row.call_args_list:
                row = args[0]
                status_column = 7
                if row[status_column] == status:
                    images_with_status += 1
            self.assertEquals(images_with_status, image_cnt)

    def test_snapshot_image_update(self):
        configfile = self.setup_config("node.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider", "fake-image")
        nodepoolcmd.main()
        self.wait_for_threads()
        self.assert_images_listed(configfile, 1)

    def test_dib_image_update(self):
        configfile = self.setup_config("node_dib.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "fake-dib-provider", "fake-dib-image")
        nodepoolcmd.main()
        self.wait_for_threads()
        self.assert_images_listed(configfile, 1)

    def test_dib_snapshot_image_update(self):
        configfile = self.setup_config("node_dib_and_snap.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider1", "fake-dib-image")
        nodepoolcmd.main()
        self.patch_argv("-c", configfile, "image-update",
                        "fake-provider2", "fake-dib-image")
        nodepoolcmd.main()
        self.wait_for_threads()
        self.assert_images_listed(configfile, 2)

    def test_dib_snapshot_image_update_all(self):
        configfile = self.setup_config("node_dib_and_snap.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-dib-image")
        nodepoolcmd.main()
        self.wait_for_threads()
        self.assert_images_listed(configfile, 2)

    def test_image_update_all(self):
        configfile = self.setup_config("node_cmd.yaml")
        self.patch_argv("-c", configfile, "image-update",
                        "all", "fake-image1")
        nodepoolcmd.main()
        self.wait_for_threads()
        self.assert_images_listed(configfile, 1)

    def test_image_list_empty(self):
        self.assert_images_listed(self.setup_config("node_cmd.yaml"), 0)
