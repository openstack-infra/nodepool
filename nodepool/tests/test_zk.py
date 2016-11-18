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


class TestZooKeeper(tests.DBTestCase):

    def setUp(self):
        super(TestZooKeeper, self).setUp()

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
            self.assertIsNotNone(self.zk._current_build_lock)
            self.assertIsNotNone(self.zk.client.exists(path))
        self.assertIsNone(self.zk._current_build_lock)

    def test_imageBuildLock_exception_nonblocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  port=self.zookeeper_port,
                                                  chroot=self.zookeeper_chroot)])
        with zk2.imageBuildLock("ubuntu-trusty", blocking=False):
            with testtools.ExpectedException(npe.ZKLockException):
                with self.zk.imageBuildLock("ubuntu-trusty", blocking=False):
                    pass
        zk2.disconnect()

    def test_imageBuildLock_exception_blocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  port=self.zookeeper_port,
                                                  chroot=self.zookeeper_chroot)])
        with zk2.imageBuildLock("ubuntu-trusty", blocking=False):
            with testtools.ExpectedException(npe.TimeoutException):
                with self.zk.imageBuildLock("ubuntu-trusty",
                                            blocking=True,
                                            timeout=1):
                    pass
        zk2.disconnect()

    def test_imageBuildNumberLock(self):
        path = self.zk._imageBuildNumberLockPath("ubuntu-trusty", "0000")
        with self.zk.imageBuildNumberLock(
            "ubuntu-trusty", "0000", blocking=False
        ):
            self.assertIsNotNone(self.zk._current_build_number_lock)
            self.assertIsNotNone(self.zk.client.exists(path))
        self.assertIsNone(self.zk._current_build_number_lock)

    def test_imageBuildNumberLock_exception_nonblocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(
            self.zookeeper_host,
            port=self.zookeeper_port,
            chroot=self.zookeeper_chroot)])
        with zk2.imageBuildNumberLock("ubuntu-trusty", "0000", blocking=False):
            with testtools.ExpectedException(npe.ZKLockException):
                with self.zk.imageBuildNumberLock(
                    "ubuntu-trusty", "0000", blocking=False
                ):
                    pass
        zk2.disconnect()

    def test_imageBuildNumberLock_exception_blocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(
            self.zookeeper_host,
            port=self.zookeeper_port,
            chroot=self.zookeeper_chroot)])
        with zk2.imageBuildNumberLock("ubuntu-trusty", "0000", blocking=False):
            with testtools.ExpectedException(npe.TimeoutException):
                with self.zk.imageBuildNumberLock(
                    "ubuntu-trusty", "0000", blocking=True, timeout=1
                ):
                    pass
        zk2.disconnect()

    def test_imageUploadLock(self):
        path = self.zk._imageUploadLockPath("ubuntu-trusty", "0000", "prov1")
        with self.zk.imageUploadLock("ubuntu-trusty", "0000", "prov1",
                                     blocking=False):
            self.assertIsNotNone(self.zk._current_upload_lock)
            self.assertIsNotNone(self.zk.client.exists(path))
        self.assertIsNone(self.zk._current_upload_lock)

    def test_imageUploadLock_exception_nonblocking(self):
        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  port=self.zookeeper_port,
                                                  chroot=self.zookeeper_chroot)])
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
                                                  chroot=self.zookeeper_chroot)])
        with zk2.imageUploadLock("ubuntu-trusty", "0000", "prov1",
                                 blocking=False):
            with testtools.ExpectedException(npe.TimeoutException):
                with self.zk.imageUploadLock("ubuntu-trusty", "0000", "prov1",
                                             blocking=True, timeout=1):
                    pass
        zk2.disconnect()

    def test_storeBuild(self):
        image = "ubuntu-trusty"
        b1 = self.zk.storeBuild(image, zk.ImageBuild())
        b2 = self.zk.storeBuild(image, zk.ImageBuild())
        self.assertLess(int(b1), int(b2))

    def test_store_and_get_build(self):
        image = "ubuntu-trusty"
        orig_data = zk.ImageBuild()
        orig_data.builder = 'host'
        orig_data.state = zk.READY
        with self.zk.imageBuildLock(image, blocking=True, timeout=1):
            build_num = self.zk.storeBuild(image, orig_data)

        data = self.zk.getBuild(image, build_num)
        self.assertEqual(orig_data.builder, data.builder)
        self.assertEqual(orig_data.state, data.state)
        self.assertEqual(orig_data.state_time, data.state_time)
        self.assertEqual(build_num, data.id)
        self.assertEqual(self.zk.getImageNames(), ["ubuntu-trusty"])
        self.assertEqual(self.zk.getBuildNumbers("ubuntu-trusty"), [build_num])

    def test_getImageNames_not_found(self):
        self.assertEqual(self.zk.getImageNames(), [])

    def test_getBuildNumbers_not_found(self):
        self.assertEqual(self.zk.getBuildNumbers("ubuntu-trusty"), [])

    def test_getBuildProviders_not_found(self):
        self.assertEqual(self.zk.getBuildProviders(
            "ubuntu-trusty", "0000000000"), [])

    def test_getImageUploadNumbers_not_found(self):
        self.assertEqual(self.zk.getImageUploadNumbers(
            "ubuntu-trusty", "0000000000", "rax"), [])

    def test_getBuild_not_found(self):
        self.assertIsNone(self.zk.getBuild("ubuntu-trusty", "0000000000"))

    def test_getImageUpload_not_found(self):
        self.assertIsNone(
            self.zk.getImageUpload("trusty", "0001", "rax", "0000000001")
        )

    def test_storeImageUpload(self):
        image = "ubuntu-trusty"
        provider = "rax"
        bnum = self.zk.storeBuild(image, zk.ImageBuild())
        up1 = self.zk.storeImageUpload(image, bnum, provider, zk.ImageUpload())
        up2 = self.zk.storeImageUpload(image, bnum, provider, zk.ImageUpload())
        self.assertLess(int(up1), int(up2))

    def test_storeImageUpload_invalid_build(self):
        image = "ubuntu-trusty"
        build_number = "0000000001"
        provider = "rax"
        orig_data = zk.ImageUpload()

        with testtools.ExpectedException(
            npe.ZKException, "Cannot find build .*"
        ):
            self.zk.storeImageUpload(image, build_number, provider, orig_data)

    def test_store_and_get_image_upload(self):
        image = "ubuntu-trusty"
        provider = "rax"
        orig_data = zk.ImageUpload()
        orig_data.external_id = "deadbeef"
        orig_data.state = zk.READY

        build_number = self.zk.storeBuild(image, zk.ImageBuild())
        upload_id = self.zk.storeImageUpload(image, build_number, provider,
                                             orig_data)
        data = self.zk.getImageUpload(image, build_number, provider, upload_id)

        self.assertEqual(upload_id, data.id)
        self.assertEqual(orig_data.external_id, data.external_id)
        self.assertEqual(orig_data.state, data.state)
        self.assertEqual(orig_data.state_time, data.state_time)
        self.assertEqual(self.zk.getBuildProviders("ubuntu-trusty",
                                                   build_number),
                         [provider])
        self.assertEqual(self.zk.getImageUploadNumbers("ubuntu-trusty",
                                                       build_number,
                                                       provider),
                         [upload_id])

    def test_registerBuildRequestWatch(self):
        func = mock.MagicMock()
        image = "ubuntu-trusty"
        watch_path = self.zk._imageBuildRequestPath(image)

        zk2 = zk.ZooKeeper()
        zk2.connect([zk.ZooKeeperConnectionConfig(self.zookeeper_host,
                                                  self.zookeeper_port,
                                                  self.zookeeper_chroot)])

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

    def test_getMostRecentBuilds(self):
        image = "ubuntu-trusty"
        v1 = {'state': zk.READY, 'state_time': int(time.time())}
        v2 = {'state': zk.READY, 'state_time': v1['state_time'] + 10}
        v3 = {'state': zk.READY, 'state_time': v1['state_time'] + 20}
        v4 = {'state': zk.DELETING, 'state_time': v2['state_time'] + 10}
        self.zk.storeBuild(image, zk.ImageBuild.fromDict(v1))
        v2_id = self.zk.storeBuild(image, zk.ImageBuild.fromDict(v2))
        v3_id = self.zk.storeBuild(image, zk.ImageBuild.fromDict(v3))
        self.zk.storeBuild(image, zk.ImageBuild.fromDict(v4))

        # v2 and v3 should be the 2 most recent 'ready' builds
        matches = self.zk.getMostRecentBuilds(2, image, zk.READY)
        self.assertEqual(2, len(matches))

        # Should be in descending order, according to state_time
        self.assertEqual(matches[0].id, v3_id)
        self.assertEqual(matches[1].id, v2_id)

    def test_getMostRecentBuildImageUploads_with_state(self):
        image = "ubuntu-trusty"
        provider = "rax"
        build = {'state': zk.READY, 'state_time': int(time.time())}
        up1 = zk.ImageUpload()
        up1.state = zk.READY
        up2 = zk.ImageUpload()
        up2.state = zk.READY
        up2.state_time = up1.state_time + 10
        up3 = zk.ImageUpload()
        up3.state = zk.DELETING
        up3.state_time = up2.state_time + 10

        bnum = self.zk.storeBuild(image, zk.ImageBuild.fromDict(build))
        self.zk.storeImageUpload(image, bnum, provider, up1)
        up2_id = self.zk.storeImageUpload(image, bnum, provider, up2)
        self.zk.storeImageUpload(image, bnum, provider, up3)

        # up2 should be the most recent 'ready' upload
        data = self.zk.getMostRecentBuildImageUploads(1, image, bnum, provider, zk.READY)
        self.assertNotEqual([], data)
        self.assertEqual(1, len(data))
        self.assertEqual(data[0].id, up2_id)

    def test_getMostRecentBuildImageUploads_any_state(self):
        image = "ubuntu-trusty"
        provider = "rax"
        build = {'state': zk.READY, 'state_time': int(time.time())}
        up1 = zk.ImageUpload()
        up1.state = zk.READY
        up2 = zk.ImageUpload()
        up2.state = zk.READY
        up2.state_time = up1.state_time + 10
        up3 = zk.ImageUpload()
        up3.state = zk.UPLOADING
        up3.state_time = up2.state_time + 10

        bnum = self.zk.storeBuild(image, zk.ImageBuild.fromDict(build))
        self.zk.storeImageUpload(image, bnum, provider, up1)
        self.zk.storeImageUpload(image, bnum, provider, up2)
        up3_id = self.zk.storeImageUpload(image, bnum, provider, up3)

        # up3 should be the most recent upload, regardless of state
        data = self.zk.getMostRecentBuildImageUploads(1, image, bnum, provider, None)
        self.assertNotEqual([], data)
        self.assertEqual(1, len(data))
        self.assertEqual(data[0].id, up3_id)

    def test_getMostRecentImageUpload(self):
        image = "ubuntu-trusty"
        provider = "rax"

        build1 = zk.ImageBuild()
        build1.state = zk.READY
        build2 = zk.ImageBuild()
        build2.state = zk.READY
        build2.state_time = build1.state_time + 10

        bnum1 = self.zk.storeBuild(image, build1)
        bnum2 = self.zk.storeBuild(image, build2)

        upload1 = zk.ImageUpload()
        upload1.state = zk.READY
        upload2 = zk.ImageUpload()
        upload2.state = zk.READY
        upload2.state_time = upload1.state_time + 10

        self.zk.storeImageUpload(image, bnum1, provider, upload1)
        self.zk.storeImageUpload(image, bnum2, provider, upload2)

        d = self.zk.getMostRecentImageUpload(image, provider, zk.READY)
        self.assertEqual(upload2.state_time, d.state_time)

    def test_getBuilds_any(self):
        image = "ubuntu-trusty"
        path = self.zk._imageBuildsPath(image)
        v1 = {'state': zk.READY}
        v2 = {'state': zk.BUILDING}
        v3 = {'state': zk.FAILED}
        v4 = {'state': zk.DELETING}
        v5 = {}
        self.zk.client.create(path + "/1", value=self.zk._dictToStr(v1),
                              makepath=True)
        self.zk.client.create(path + "/2", value=self.zk._dictToStr(v2),
                              makepath=True)
        self.zk.client.create(path + "/3", value=self.zk._dictToStr(v3),
                              makepath=True)
        self.zk.client.create(path + "/4", value=self.zk._dictToStr(v4),
                              makepath=True)
        self.zk.client.create(path + "/5", value=self.zk._dictToStr(v5),
                              makepath=True)
        self.zk.client.create(path + "/lock", makepath=True)

        matches = self.zk.getBuilds(image, None)
        self.assertEqual(5, len(matches))

    def test_getBuilds(self):
        image = "ubuntu-trusty"
        path = self.zk._imageBuildsPath(image)
        v1 = {'state': zk.BUILDING}
        v2 = {'state': zk.READY}
        v3 = {'state': zk.FAILED}
        v4 = {'state': zk.DELETING}
        v5 = {}
        self.zk.client.create(path + "/1", value=self.zk._dictToStr(v1),
                              makepath=True)
        self.zk.client.create(path + "/2", value=self.zk._dictToStr(v2),
                              makepath=True)
        self.zk.client.create(path + "/3", value=self.zk._dictToStr(v3),
                              makepath=True)
        self.zk.client.create(path + "/4", value=self.zk._dictToStr(v4),
                              makepath=True)
        self.zk.client.create(path + "/5", value=self.zk._dictToStr(v5),
                              makepath=True)
        self.zk.client.create(path + "/lock", makepath=True)

        matches = self.zk.getBuilds(image, [zk.DELETING, zk.FAILED])
        self.assertEqual(2, len(matches))

    def test_getUploads(self):
        path = self.zk._imageUploadPath("trusty", "000", "rax")
        v1 = {'state': zk.READY}
        v2 = {'state': zk.UPLOADING}
        v3 = {'state': zk.FAILED}
        v4 = {'state': zk.DELETING}
        v5 = {}
        self.zk.client.create(path + "/1", value=self.zk._dictToStr(v1),
                              makepath=True)
        self.zk.client.create(path + "/2", value=self.zk._dictToStr(v2),
                              makepath=True)
        self.zk.client.create(path + "/3", value=self.zk._dictToStr(v3),
                              makepath=True)
        self.zk.client.create(path + "/4", value=self.zk._dictToStr(v4),
                              makepath=True)
        self.zk.client.create(path + "/5", value=self.zk._dictToStr(v5),
                              makepath=True)
        self.zk.client.create(path + "/lock", makepath=True)

        matches = self.zk.getUploads("trusty", "000", "rax",
                                     [zk.DELETING, zk.FAILED])
        self.assertEqual(2, len(matches))

    def test_getUploads_any(self):
        path = self.zk._imageUploadPath("trusty", "000", "rax")
        v1 = {'state': zk.READY}
        v2 = {'state': zk.UPLOADING}
        v3 = {'state': zk.FAILED}
        v4 = {'state': zk.DELETING}
        v5 = {}
        self.zk.client.create(path + "/1", value=self.zk._dictToStr(v1),
                              makepath=True)
        self.zk.client.create(path + "/2", value=self.zk._dictToStr(v2),
                              makepath=True)
        self.zk.client.create(path + "/3", value=self.zk._dictToStr(v3),
                              makepath=True)
        self.zk.client.create(path + "/4", value=self.zk._dictToStr(v4),
                              makepath=True)
        self.zk.client.create(path + "/5", value=self.zk._dictToStr(v5),
                              makepath=True)
        self.zk.client.create(path + "/lock", makepath=True)

        matches = self.zk.getUploads("trusty", "000", "rax", None)
        self.assertEqual(5, len(matches))

    def test_deleteBuild(self):
        image = 'trusty'
        build = zk.ImageBuild()
        build.state = zk.READY
        bnum = self.zk.storeBuild(image, build)
        self.assertTrue(self.zk.deleteBuild(image, bnum))

    def test_deleteBuild_with_uploads(self):
        image = 'trusty'
        provider = 'rax'

        build = zk.ImageBuild()
        build.state = zk.READY
        bnum = self.zk.storeBuild(image, build)

        upload = zk.ImageUpload()
        upload.state = zk.READY
        self.zk.storeImageUpload(image, bnum, provider, upload)

        self.assertFalse(self.zk.deleteBuild(image, bnum))

    def test_deleteUpload(self):
        path = self.zk._imageUploadPath("trusty", "000", "rax") + "/000001"
        self.zk.client.create(path, makepath=True)
        self.zk.deleteUpload("trusty", "000", "rax", "000001")
        self.assertIsNone(self.zk.client.exists(path))


class TestZKModel(tests.BaseTestCase):

    def setUp(self):
        super(TestZKModel, self).setUp()

    def test_BaseBuilderModel_bad_id(self):
        with testtools.ExpectedException(
            TypeError, "'id' attribute must be a string type"
        ):
            zk.BaseBuilderModel(123)

    def test_BaseBuilderModel_bad_state(self):
        with testtools.ExpectedException(
            TypeError, "'blah' is not a valid state"
        ):
            o = zk.BaseBuilderModel('0001')
            o.state = 'blah'

    def test_BaseBuilderModel_toDict(self):
        o = zk.BaseBuilderModel('0001')
        o.state = zk.BUILDING
        d = o.toDict()
        self.assertNotIn('id', d)
        self.assertEqual(o.state, d['state'])
        self.assertIsNotNone(d['state_time'])

    def test_ImageBuild_toDict(self):
        o = zk.ImageBuild('0001')
        o.builder = 'localhost'
        o.formats = ['qemu', 'raw']

        d = o.toDict()
        self.assertNotIn('id', d)
        self.assertEqual(','.join(o.formats), d['formats'])
        self.assertEqual(o.builder, d['builder'])

    def test_ImageBuild_fromDict(self):
        now = int(time.time())
        d_id = '0001'
        d = {
            'builder': 'localhost',
            'formats': 'qemu,raw',
            'state': zk.BUILDING,
            'state_time': now
        }

        o = zk.ImageBuild.fromDict(d, d_id)
        self.assertEqual(o.id, d_id)
        self.assertEqual(o.state, d['state'])
        self.assertEqual(o.state_time, d['state_time'])
        self.assertEqual(o.builder, d['builder'])
        self.assertEqual(o.formats, d['formats'].split(','))

    def test_ImageUpload_toDict(self):
        o = zk.ImageUpload('0001', '0003')
        o.external_id = 'DEADBEEF'
        o.external_name = 'trusty'

        d = o.toDict()
        self.assertNotIn('id', d)
        self.assertNotIn('build_id', d)
        self.assertNotIn('provider_name', d)
        self.assertEqual(o.external_id, d['external_id'])
        self.assertEqual(o.external_name, d['external_name'])

    def test_ImageUpload_fromDict(self):
        now = int(time.time())
        upload_id = '0001'
        build_id = '0003'
        d = {
            'external_id': 'DEADBEEF',
            'external_name': 'trusty',
            'state': zk.READY,
            'state_time': now
        }

        o = zk.ImageUpload.fromDict(d, build_id, 'rax', upload_id)
        self.assertEqual(o.id, upload_id)
        self.assertEqual(o.build_id, build_id)
        self.assertEqual(o.provider_name, 'rax')
        self.assertEqual(o.state, d['state'])
        self.assertEqual(o.state_time, d['state_time'])
        self.assertEqual(o.external_id, d['external_id'])
        self.assertEqual(o.external_name, d['external_name'])
