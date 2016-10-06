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

import mock
import testtools
import time

from nodepool import exceptions as npe
from nodepool import tests
from nodepool import zk


class TestZooKeeper(tests.ZKTestCase):

    def setUp(self):
        super(TestZooKeeper, self).setUp()
        self.zk = zk.ZooKeeper(self.zkclient)

    def test_buildZooKeeperHosts_single(self):
        hosts = [
            zk.ZooKeeperConnectionConfig('127.0.0.1', port=2181,
                                         chroot='/test1')
        ]
        self.assertEqual('127.0.0.1:2181/test1',
                         zk.buildZooKeeperHosts(hosts))

    def test_buildZooKeeperHosts_multiple(self):
        hosts = [
            zk.ZooKeeperConnectionConfig('127.0.0.1', port=2181,
                                         chroot='/test1'),
            zk.ZooKeeperConnectionConfig('127.0.0.2', port=2182,
                                         chroot='/test2')
        ]
        self.assertEqual('127.0.0.1:2181/test1,127.0.0.2:2182/test2',
                         zk.buildZooKeeperHosts(hosts))

    def test_imageBuildLock(self):
        path = self.zk._imageBuildLockPath("ubuntu-trusty")
        with self.zk.imageBuildLock("ubuntu-trusty", blocking=False):
            self.assertIsNotNone(self.zk._current_lock)
            self.assertIsNotNone(self.zk.client.exists(path))
        self.assertIsNone(self.zk._current_lock)

    def test_imageBuildLock_exception_nonblocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  port=self.zookeeper_port,
                                                  chroot=self.chroot_path)])
        with zk2.imageBuildLock("ubuntu-trusty", blocking=False):
            with testtools.ExpectedException(npe.ZKLockException):
                with self.zk.imageBuildLock("ubuntu-trusty", blocking=False):
                    pass
        zk2.disconnect()

    def test_imageBuildLock_exception_blocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  port=self.zookeeper_port,
                                                  chroot=self.chroot_path)])
        with zk2.imageBuildLock("ubuntu-trusty", blocking=False):
            with testtools.ExpectedException(npe.TimeoutException):
                with self.zk.imageBuildLock("ubuntu-trusty",
                                            blocking=True,
                                            timeout=1):
                    pass
        zk2.disconnect()

    def test_imageUploadLock(self):
        path = self.zk._imageUploadLockPath("ubuntu-trusty", "0000", "prov1")
        with self.zk.imageUploadLock("ubuntu-trusty", "0000", "prov1",
                                     blocking=False):
            self.assertIsNotNone(self.zk._current_lock)
            self.assertIsNotNone(self.zk.client.exists(path))
        self.assertIsNone(self.zk._current_lock)

    def test_imageUploadLock_exception_nonblocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  port=self.zookeeper_port,
                                                  chroot=self.chroot_path)])
        with zk2.imageUploadLock("ubuntu-trusty", "0000", "prov1",
                                blocking=False):
            with testtools.ExpectedException(npe.ZKLockException):
                with self.zk.imageUploadLock("ubuntu-trusty", "0000", "prov1",
                                             blocking=False):
                    pass
        zk2.disconnect()

    def test_imageUploadLock_exception_blocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  port=self.zookeeper_port,
                                                  chroot=self.chroot_path)])
        with zk2.imageUploadLock("ubuntu-trusty", "0000", "prov1",
                                 blocking=False):
            with testtools.ExpectedException(npe.TimeoutException):
                with self.zk.imageUploadLock("ubuntu-trusty", "0000", "prov1",
                                             blocking=True, timeout=1):
                    pass
        zk2.disconnect()

    def test_storeBuild(self):
        image = "ubuntu-trusty"
        b1 = self.zk.storeBuild(image, {})
        b2 = self.zk.storeBuild(image, {})
        self.assertLess(int(b1), int(b2))

    def test_store_and_get_build(self):
        image = "ubuntu-trusty"
        orig_data = dict(builder="host", filename="file", state="state")
        with self.zk.imageBuildLock(image, blocking=True, timeout=1):
            build_num = self.zk.storeBuild(image, orig_data)

        data = self.zk.getBuild(image, build_num)
        self.assertEqual(orig_data, data)
        self.assertEqual(self.zk.getImageNames(), ["ubuntu-trusty"])
        self.assertEqual(self.zk.getBuildNumbers("ubuntu-trusty"), [build_num])

    def test_getImageNames_not_found(self):
        self.assertEqual(self.zk.getImageNames(), [])

    def test_getBuildNumbers_not_found(self):
        self.assertEqual(self.zk.getBuildNumbers("ubuntu-trusty"), [])

    def test_getBuild_not_found(self):
        self.assertIsNone(self.zk.getBuild("ubuntu-trusty", "0000000000"))

    def test_getImageUpload_not_found(self):
        image = "ubuntu-trusty"
        build_number = "0000000001"
        provider = "rax"

        with testtools.ExpectedException(
            npe.ZKException, "Cannot find upload data .*"
        ):
            self.zk.getImageUpload(image, build_number, provider, "0000000001")

    def test_storeImageUpload(self):
        image = "ubuntu-trusty"
        provider = "rax"
        bnum = self.zk.storeBuild(image, {})
        up1 = self.zk.storeImageUpload(image, bnum, provider, {})
        up2 = self.zk.storeImageUpload(image, bnum, provider, {})
        self.assertLess(int(up1), int(up2))

    def test_storeImageUpload_invalid_build(self):
        image = "ubuntu-trusty"
        build_number = "0000000001"
        provider = "rax"
        orig_data = dict(external_id="deadbeef", state="READY")

        with testtools.ExpectedException(
            npe.ZKException, "Cannot find build .*"
        ):
            self.zk.storeImageUpload(image, build_number, provider, orig_data)

    def test_store_and_get_image_upload(self):
        image = "ubuntu-trusty"
        provider = "rax"
        orig_data = dict(external_id="deadbeef", state="READY")

        build_number = self.zk.storeBuild(image, {})
        upload_id = self.zk.storeImageUpload(image, build_number, provider,
                                             orig_data)
        data = self.zk.getImageUpload(image, build_number, provider, upload_id)

        self.assertEqual(orig_data, data)

    def test_registerBuildRequestWatch(self):
        func = mock.MagicMock()
        image = "ubuntu-trusty"
        watch_path = self.zk._imageBuildRequestPath(image)

        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  self.zookeeper_port,
                                                  self.chroot_path)])

        # First client registers the watch
        self.zk.registerBuildRequestWatch(image, func)
        self.assertIn(watch_path, self.zk._data_watches)

        # Second client triggers the watch. Give ZK time to dispatch the event
        # to the other client.
        zk2.submitBuildRequest(image)
        time.sleep(1)
        zk2.disconnect()

        # Make sure the registered function was called.
        func.assert_called_once_with(mock.ANY)

        # The watch should be unregistered now.
        self.assertNotIn(watch_path, self.zk._data_watches)

    def test_build_request(self):
        '''Test the build request API methods (has/submit/remove)'''
        image = "ubuntu-trusty"
        self.zk.submitBuildRequest(image)
        self.assertTrue(self.zk.hasBuildRequest(image))
        self.zk.removeBuildRequest(image)
        self.assertFalse(self.zk.hasBuildRequest(image))

    def test_getMostRecentBuild(self):
        image = "ubuntu-trusty"
        v1 = {'state': 'ready', 'state_time': int(time.time())}
        v2 = {'state': 'ready', 'state_time': v1['state_time'] + 10}
        v3 = {'state': 'delete', 'state_time': v2['state_time'] + 10}
        self.zk.storeBuild(image, v1)
        self.zk.storeBuild(image, v2)
        self.zk.storeBuild(image, v3)

        # v2 should be the most recent 'ready' build
        data = self.zk.getMostRecentBuild(image, 'ready')
        self.assertIsNotNone(data)
        self.assertEqual(data[1], v2)

    def test_getMostRecentImageUpload(self):
        image = "ubuntu-trusty"
        provider = "rax"
        build = {'state': 'ready', 'state_time': int(time.time())}
        up1 = {'state': 'ready', 'state_time': int(time.time())}
        up2 = {'state': 'ready', 'state_time': up1['state_time'] + 10}
        up3 = {'state': 'delete', 'state_time': up2['state_time'] + 10}

        bnum = self.zk.storeBuild(image, build)
        self.zk.storeImageUpload(image, bnum, provider, up1)
        self.zk.storeImageUpload(image, bnum, provider, up2)
        self.zk.storeImageUpload(image, bnum, provider, up3)

        # up2 should be the most recent 'ready' upload
        data = self.zk.getMostRecentImageUpload(image, bnum, provider, 'ready')
        self.assertIsNotNone(data)
        self.assertEqual(data[1], up2)

